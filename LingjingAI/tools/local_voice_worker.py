import argparse,json,os,pathlib,sys,time,uuid,traceback,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def run(spec):
    start=time.monotonic(); operation=spec['operation']; out=pathlib.Path(spec['outputDir']).resolve();out.mkdir(parents=True,exist_ok=True)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    if operation=='lip_sync':raise RuntimeError('MuseTalk尚未部署；不会自动调用云端服务')
    if spec.get('device')=='cpu':os.environ['CUDA_VISIBLE_DEVICES']='-1'
    import torch,soundfile as sf
    torch.manual_seed(20260926)
    device=spec.get('device','cuda')
    if device not in ('cuda','cpu'):raise ValueError('device must be cuda or cpu')
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA不可用；请启用独显或显式选择CPU。不会静默切换设备')
    if device=='cpu':torch.set_num_threads(min(8,os.cpu_count() or 1))
    source=pathlib.Path(spec['sourcePath']).resolve()
    if not source.is_file():raise ValueError('sourcePath missing')
    duration=sf.info(str(source)).duration
    if duration>30:raise ValueError('当前本机worker仅接受不超过30秒短句')
    if device=='cuda':torch.cuda.reset_peak_memory_stats()
    tmp=out/('candidate-'+uuid.uuid4().hex);tmp.mkdir()
    if operation=='voice_convert':
        runtime=ROOT/'.runtime/voice';repo=runtime/'seed-vc-51383efd921027683c89e5348211d93ff12ac2a8';sys.path.insert(0,str(repo));os.chdir(repo)
        ref=pathlib.Path(spec['referencePath']).resolve()
        if not ref.is_file():raise ValueError('referencePath missing')
        if sf.info(str(ref)).duration>25:raise ValueError('参考声线请先选取不超过25秒的片段')
        import yaml,hf_utils
        models=runtime/'models'
        def local_model(repo_id,model_filename='pytorch_model.bin',config_filename=None):
            base=models/repo_id.replace('/','--');p=base/model_filename
            if not p.is_file():raise FileNotFoundError(p)
            return (str(p),str(base/config_filename)) if config_filename else str(p)
        hf_utils.load_custom_model_from_hf=local_model
        cfg=yaml.safe_load((models/'Plachta--Seed-VC/config_dit_mel_seed_uvit_whisper_small_wavenet.yml').read_text())
        cfg['model_params']['speech_tokenizer']['name']=str(models/'openai--whisper-small')
        cfg['model_params']['vocoder']['name']=str(models/'nvidia--bigvgan_v2_22khz_80band_256x')
        cfgpath=tmp/'config.yml';cfgpath.write_text(yaml.safe_dump(cfg))
        import inference,torchaudio
        inference.device=torch.device(device)
        if device=='cpu':
            import transformers
            load_whisper=transformers.WhisperModel.from_pretrained
            def cpu_whisper(*args,**kwargs):
                kwargs['torch_dtype']=torch.float32
                return load_whisper(*args,**kwargs)
            transformers.WhisperModel.from_pretrained=cpu_whisper
        # torchaudio 2.8 save backend requires TorchCodec; soundfile writes the same PCM samples.
        torchaudio.save=lambda path,tensor,sr,**kw:sf.write(path,tensor.detach().cpu().numpy().T,sr)
        args=argparse.Namespace(source=str(source),target=str(ref),output=str(tmp),diffusion_steps=30,length_adjust=1.0,inference_cfg_rate=.7,f0_condition=False,auto_f0_adjust=False,semi_tone_shift=0,checkpoint=local_model('Plachta/Seed-VC','DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth'),config=str(cfgpath),fp16=device=='cuda')
        inference.main(args);engine='Seed-VC v1';files=list(tmp.glob('*.wav'))
    elif operation=='speech_edit':
        runtime=ROOT/'.runtime/dots';repo=runtime/'dots.tts-32407a55228630475c48ecdb2c4e2c0f9c09e030';sys.path.insert(0,str(repo/'src'))
        from dots_tts.data.edit_instruction import render_source_text,render_target_text,instruction_operation_tags
        if render_source_text(spec['instruction']) != spec['sourceText']:raise ValueError('编辑指令与已校正原文不一致')
        if not instruction_operation_tags(spec['instruction']):raise ValueError('编辑指令缺少局部操作')
        from dots_tts.edit_runtime import DotsTtsEditRuntime
        model=DotsTtsEditRuntime.from_pretrained(str(runtime/'model'),precision='bfloat16' if device=='cuda' else 'float32',optimize=False,max_generate_length=500,max_sequence_length=2048)
        if device=='cpu':torch.set_num_threads(min(8,os.cpu_count() or 1))
        result=model.generate_edit(source_audio_path=str(source),source_text=spec['sourceText'],instruction=spec['instruction'])
        file=tmp/'edited.wav';sf.write(str(file),result['audio'].detach().cpu().squeeze().float().numpy(),result['sample_rate']);files=[file];engine='dots.tts.edit'
    else:raise ValueError('Unknown operation')
    if not files:raise RuntimeError('Model did not create audio')
    import numpy as np
    waveform,rate=sf.read(str(files[0]))
    if waveform.size==0 or not np.isfinite(waveform).all() or np.max(np.abs(waveform))<1e-6:raise RuntimeError('引擎输出为空、非有限值或静音，请检查日志')
    return {'targetText':render_target_text(spec['instruction']) if operation=='speech_edit' else spec.get('text'),'outputs':[{'path':str(f.resolve()),'kind':'audio','role':'dialogue_performance','name':f.name} for f in files], 'metadata':{'engine':engine,'device':device,'deviceName':str(torch.cuda.get_device_name()) if device=='cuda' else 'CPU','inputDuration':duration,'outputDuration':sf.info(str(files[0])).duration,'durationDelta':sf.info(str(files[0])).duration-duration,'elapsed':time.monotonic()-start,'peakVramBytes':torch.cuda.max_memory_allocated() if device=='cuda' else 0,'offline':True,'seed':20260926,'torchVersion':torch.__version__,'pythonVersion':sys.version.split()[0],'reviewStatus':'candidate','sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'sourceRevision':'51383efd921027683c89e5348211d93ff12ac2a8' if operation=='voice_convert' else '32407a55228630475c48ecdb2c4e2c0f9c09e030','modelManifest':str(runtime/'model-lock.json')}}
def main():
    p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--result',required=True);a=p.parse_args()
    a.request=str(pathlib.Path(a.request).resolve());a.result=str(pathlib.Path(a.result).resolve())
    try:result=run(json.loads(pathlib.Path(a.request).read_text(encoding='utf-8-sig')))
    except Exception as e:
        traceback.print_exc();pathlib.Path(a.result).write_text(json.dumps({'outputs':[],'error':str(e)},ensure_ascii=False),encoding='utf-8');return 1
    pathlib.Path(a.result).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return 0
if __name__=='__main__':sys.exit(main())
