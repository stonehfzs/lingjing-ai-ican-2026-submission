"""Offline Qwen VoiceDesign / reference TTS preview worker; no resource-library writes."""
import argparse,json,os,pathlib,sys,time,subprocess,hashlib,uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
os.environ['PATH']=str(ROOT/'.runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')
_handles=[]
if os.name=='nt':
 dll=ROOT/'.runtime/audio/Lib/site-packages/torch/lib'
 if dll.is_dir():os.environ['PATH']=str(dll)+os.pathsep+os.environ['PATH'];_handles.append(os.add_dll_directory(str(dll)))
def report_progress(req,phase):
 # Progress is advisory; inability to write telemetry must not change audio output.
 temp=None
 try:
  out=pathlib.Path(req['outputDir']).resolve();out.mkdir(parents=True,exist_ok=True)
  temp=out/('progress.'+uuid.uuid4().hex+'.tmp')
  temp.write_text(json.dumps({'runId':req.get('progressRunId'),'phase':phase,'indeterminate':True,'percent':None}),encoding='utf-8')
  os.replace(temp,out/'progress.json')
 except OSError:
  if temp is not None:
   try:temp.unlink(missing_ok=True)
   except OSError:pass

def run(req):
 start=time.perf_counter()
 report_progress(req,'loading')
 if req.get('blockNetwork'):
  import socket
  def blocked(*args,**kwargs):raise RuntimeError('Network disabled for offline inference verification')
  socket.socket.connect=blocked;socket.socket.connect_ex=blocked;socket.create_connection=blocked
 import torch,numpy as np,soundfile as sf
 from qwen_tts import Qwen3TTSModel
 device=req.get('device','cuda');mode=req.get('mode','design');text=req['text'].strip()
 if not text or len(text)>200:raise ValueError('试听台词限1–200字')
 if mode not in {'design','reference'} or device not in {'cpu','cuda'}:raise ValueError('Unsupported mode/device')
 if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('NVIDIA GPU unavailable; no cloud fallback')
 torch.manual_seed(int(req.get('seed',1234)))
 if device=='cuda':torch.cuda.manual_seed_all(int(req.get('seed',1234)));torch.cuda.reset_peak_memory_stats()
 else:torch.set_num_threads(4)
 model_path=pathlib.Path(req['modelPath']).resolve();out=pathlib.Path(req['outputDir']).resolve();out.mkdir(parents=True,exist_ok=True)
 m=Qwen3TTSModel.from_pretrained(str(model_path),device_map='cuda:0' if device=='cuda' else 'cpu',dtype=torch.bfloat16 if device=='cuda' else torch.float32,attn_implementation='sdpa',local_files_only=True)
 kwargs={'max_new_tokens':min(900,max(160,len(text)*12)),'do_sample':True,'temperature':float(req.get('temperature',.9)),'top_p':.95,'repetition_penalty':1.05}
 with torch.inference_mode():
  if mode=='design':
   description=req.get('description','').strip()
   if not description:raise ValueError('声线描述不能为空')
   instruct=description+('\n本句表演：'+req['performance'].strip() if req.get('performance','').strip() else '')
   report_progress(req,'generating')
   wavs,sr=m.generate_voice_design(text=text,language='Chinese',instruct=instruct,**kwargs)
  else:
   ref=pathlib.Path(req['referencePath']).resolve();converted=out/'reference-input.wav'
   subprocess.run([str(ROOT/'.runtime/ffmpeg/ffmpeg.exe'),'-v','error','-nostdin','-y','-i',str(ref),'-t','31','-ac','1','-ar','24000',str(converted)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   arr,rs=sf.read(converted,dtype='float32');converted.unlink()
   if arr.ndim>1:arr=arr.mean(axis=1)
   if len(arr)/rs>30.05:raise ValueError('固定声线样本须不超过30秒')
   report_progress(req,'generating')
   wavs,sr=m.generate_voice_clone(text=text,language='Chinese',ref_audio=(arr,rs),ref_text=req.get('referenceText') or None,x_vector_only_mode=not bool(req.get('referenceText')),**kwargs)
 report_progress(req,'encoding')
 a=np.asarray(wavs[0],dtype=np.float32)
 if not a.size or not np.isfinite(a).all() or float(np.max(np.abs(a)))<1e-5:raise RuntimeError('Invalid/silent output')
 path=out/'preview.wav';sf.write(path,a,sr,subtype='PCM_16')
 return {'path':str(path),'metadata':{'mode':mode,'modelId':req['modelId'],'revision':req.get('modelRevision'),'device':device,'attention':'sdpa','dtype':'bfloat16' if device=='cuda' else 'float32','sampleRate':sr,'duration':len(a)/sr,'peakVramBytes':torch.cuda.max_memory_allocated() if device=='cuda' else None,'peakReservedVramBytes':torch.cuda.max_memory_reserved() if device=='cuda' else None,'elapsedSeconds':round(time.perf_counter()-start,3),'seed':req.get('seed',1234),'temperature':req.get('temperature',.9),'offline':True,'networkBlockedTest':bool(req.get('blockNetwork')),'peakAmplitude':float(np.max(np.abs(a))),'rms':float(np.sqrt(np.mean(a*a))),'reviewStatus':'not_auditioned'}}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--result',required=True);a=p.parse_args()
 try:r=run(json.loads(pathlib.Path(a.request).read_text(encoding='utf-8-sig')))
 except Exception as exc:
  import traceback
  traceback.print_exc();pathlib.Path(a.result).write_text(json.dumps({'error':str(exc)},ensure_ascii=False),encoding='utf-8');sys.exit(1)
 pathlib.Path(a.result).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
