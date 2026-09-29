"""Offline one-shot speech worker. Source media are never modified."""
import argparse, hashlib, json, os, pathlib, subprocess, sys, time, uuid
ROOT = pathlib.Path(__file__).resolve().parents[1]
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['PATH']=str(ROOT/'.runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')
DLL_HANDLES=[]
if os.name=='nt':
    dll=ROOT/'.runtime/audio/Lib/site-packages/torch/lib'
    if dll.is_dir():
        os.environ['PATH']=str(dll)+os.pathsep+os.environ['PATH']
        DLL_HANDLES.append(os.add_dll_directory(str(dll)))
def local_file(value):
    p=pathlib.Path(value).resolve()
    if not p.is_file(): raise ValueError('Local input file is missing: '+str(p))
    return p
def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()
def run(req):
    start=time.perf_counter(); op=req['operation']
    if op not in ('transcribe','tts'): raise ValueError('Unsupported operation')
    key='asr' if op=='transcribe' else 'tts'
    model=pathlib.Path(req.get('modelPath') or ROOT/'.runtime/speech-models'/key).resolve()
    if not model.is_dir(): raise ValueError('Local model missing')
    out=pathlib.Path(req['outputDir']).resolve(); out.mkdir(parents=True,exist_ok=True)
    device=req.get('device','cuda')
    if device not in ('cuda','cpu'): raise ValueError('device must be cuda or cpu')
    token=uuid.uuid4().hex[:12]
    meta={'engine':'faster-whisper' if key=='asr' else 'qwen-tts','modelId':'Systran/faster-whisper-small' if key=='asr' else 'Qwen/Qwen3-TTS-12Hz-0.6B-Base','modelPath':str(model),'device':device,'offline':True,'reviewStatus':'candidate'}
    meta['modelRevision']={'asr':'536b0662742c02347bc0e980a01041f333bce120','tts':'5d83992436eae1d760afd27aff78a71d676296fc'}[key]
    from importlib.metadata import version
    meta['engineVersion']=version(meta['engine'])
    if op=='transcribe':
        from faster_whisper import WhisperModel
        import ctranslate2
        if device=='cuda':
            try: ctranslate2.get_supported_compute_types('cuda')
            except RuntimeError as e:
                device='cpu'; meta['device']='cpu'; meta['fallbackReason']=str(e)
        source=local_file(req['sourcePath']); meta['sourceSha256']=digest(source)
        compute='int8_float16' if device=='cuda' else 'int8'
        m=WhisperModel(str(model),device=device,compute_type=compute,local_files_only=True,cpu_threads=4)
        segments,info=m.transcribe(str(source),language=None if req.get('language')=='auto' else req.get('language','zh'),beam_size=5,vad_filter=True,word_timestamps=True)
        segs=[{'start':float(s.start),'end':float(s.end),'text':s.text,'words':[{'start':float(w.start),'end':float(w.end),'word':w.word,'probability':w.probability} for w in (s.words or [])]} for s in segments]
        transcript={'text':''.join(s['text'] for s in segs),'segments':segs,'language':info.language,'duration':info.duration}
        p=out/f'transcript-{token}.json'; p.write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8')
        meta.update(computeType=compute,peakVramBytes=None)
        result={'outputs':[],'transcript':transcript}
    else:
        import torch, soundfile as sf, numpy as np
        from qwen_tts import Qwen3TTSModel
        if device=='cpu': torch.set_num_threads(4)
        ref=local_file(req['referencePath']); text=str(req.get('text','')).strip()
        if not text or len(text)>600: raise ValueError('TTS text must contain 1-600 characters')
        if device=='cuda' and not torch.cuda.is_available(): raise RuntimeError('CUDA is unavailable; select cpu explicitly')
        if device=='cuda': torch.cuda.reset_peak_memory_stats()
        temp=out/f'reference-{token}.wav'
        subprocess.run([str(ROOT/'.runtime/ffmpeg/ffmpeg.exe'),'-v','error','-nostdin','-i',str(ref),'-t','31','-ac','1','-ar','24000',str(temp)],check=True)
        arr,sr=sf.read(temp,dtype='float32'); temp.unlink()
        if len(arr)>30*sr: raise ValueError('Reference audio must be at most 30 seconds; select a shorter reference with matching text')
        m=Qwen3TTSModel.from_pretrained(str(model),device_map='cuda:0' if device=='cuda' else 'cpu',dtype=torch.bfloat16 if device=='cuda' else torch.float32,attn_implementation='sdpa',local_files_only=True)
        language={'zh':'Chinese','en':'English','ja':'Japanese','ko':'Korean','fr':'French','de':'German','ru':'Russian','es':'Spanish','it':'Italian','pt':'Portuguese','auto':'Auto'}.get(req.get('language','zh'),req.get('language'))
        reftext=str(req.get('referenceText') or '').strip()
        with torch.inference_mode():
            wavs,sr=m.generate_voice_clone(text=text,language=language,ref_audio=(arr,sr),ref_text=reftext or None,x_vector_only_mode=not bool(reftext),max_new_tokens=min(1200,max(100,len(text)*12)))
        p=out/f'tts-{token}.wav'; sf.write(p,wavs[0],sr)
        meta.update(referenceSha256=digest(ref),sampleRate=sr,duration=len(wavs[0])/sr,peakVramBytes=torch.cuda.max_memory_allocated() if device=='cuda' else None,cloneMode='in-context' if reftext else 'speaker-embedding',attention='sdpa')
        if not np.isfinite(wavs[0]).all() or np.max(np.abs(wavs[0]))<1e-5: raise RuntimeError('Generated audio is empty or invalid')
        result={'outputs':[{'path':str(p),'kind':'audio','role':'dialogue_performance','name':'本机声线合成候选'}]}
    meta['elapsedSeconds']=round(time.perf_counter()-start,3)
    try:
        import psutil
        mem=psutil.Process().memory_info(); meta['peakRamBytes']=getattr(mem,'peak_wset',mem.rss)
    except ImportError: pass
    result['metadata']=meta
    return result
if __name__=='__main__':
    a=argparse.ArgumentParser(); a.add_argument('--request',required=True); a.add_argument('--result',required=True); args=a.parse_args()
    try: result=run(json.loads(pathlib.Path(args.request).read_text(encoding='utf-8-sig')))
    except Exception as e:
        import traceback
        traceback.print_exc(); pathlib.Path(args.result).write_text(json.dumps({'error':str(e),'outputs':[]},ensure_ascii=False),encoding='utf-8'); sys.exit(1)
    pathlib.Path(args.result).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')






