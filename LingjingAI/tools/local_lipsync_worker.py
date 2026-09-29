"""Isolated MuseTalk 1.5 adapter; no account or cloud service is accessed."""
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, time, uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]

def run(spec):
    if spec.get('operation')!='lip_sync':raise ValueError('Only lip_sync is supported')
    if spec.get('device','cuda')!='cuda':raise ValueError('MuseTalk当前只开放经过验证的GPU路线')
    import torch
    if not torch.cuda.is_available():raise RuntimeError('NVIDIA GPU未就绪')
    source=pathlib.Path(spec['videoPath']).resolve();audio=pathlib.Path(spec['audioPath']).resolve()
    if not source.is_file() or not audio.is_file():raise FileNotFoundError('输入视频或音频不存在')
    out=pathlib.Path(spec['outputDir']).resolve();out.mkdir(parents=True,exist_ok=True)
    runtime=ROOT/'.runtime/musetalk';doc=json.loads((runtime/'source.json').read_text());repo=pathlib.Path(doc['repo'])
    required=['models/musetalkV15/unet.pth','models/musetalkV15/musetalk.json','models/sd-vae/diffusion_pytorch_model.bin','models/whisper/pytorch_model.bin','models/dwpose/dw-ll_ucoco_384.pth','models/face-parse-bisent/79999_iter.pth','models/face-parse-bisent/resnet18-5c106cde.pth','musetalk/utils/face_detection/detection/sfd/s3fd.pth']
    for relative in required:
        if not (repo/relative).is_file():raise FileNotFoundError('缺少本地模型：'+relative)
    token=uuid.uuid4().hex;stage=runtime/'jobs'/token;stage.mkdir(parents=True)
    shutil.copyfile(source,stage/'video.mp4')
    ffmpeg=ROOT/'.runtime/ffmpeg/ffmpeg.exe'
    subprocess.run([str(ffmpeg),'-v','error','-nostdin','-i',str(audio),'-ac','1','-ar','16000',str(stage/'audio.wav')],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    prefix='../jobs/'+token
    config={'candidate':{'video_path':prefix+'/video.mp4','audio_path':prefix+'/audio.wav','result_name':'candidate.mp4'}}
    config_path=stage/'request.json';config_path.write_text(json.dumps(config),encoding='utf8')
    env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','PYTHONUTF8':'1',
      'PATH':str(ffmpeg.parent)+os.pathsep+os.environ.get('PATH',''),
      'PYTHONPATH':str(repo)+os.pathsep+str(repo/'musetalk/utils')}
    command=[sys.executable,'-X','utf8',str(ROOT/'tools/musetalk_inference_offline.py'),'--inference_config',str(config_path),'--result_dir',prefix+'/results','--unet_model_path','models/musetalkV15/unet.pth','--unet_config','models/musetalkV15/musetalk.json','--whisper_dir','models/whisper','--version','v15','--use_float16','--batch_size','1','--ffmpeg_path',str(ffmpeg.parent)]
    start=time.monotonic()
    with (out/'musetalk.log').open('wb') as log:
        proc=subprocess.run(command,cwd=repo,env=env,stdout=log,stderr=log,timeout=1800,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    output=stage/'results/v15/candidate.mp4'
    if proc.returncode or not output.is_file() or output.stat().st_size<1024:
        raise RuntimeError((out/'musetalk.log').read_text(encoding='utf8',errors='replace')[-2500:])
    target=out/'lipsync-candidate.mp4';shutil.copyfile(output,target)
    return {'outputs':[{'path':str(target),'kind':'video','role':'reference','name':'本机口型候选'}],
      'metadata':{'engine':'MuseTalk 1.5','sourceRevision':doc['revision'],'device':'cuda','deviceName':torch.cuda.get_device_name(0),'offline':True,'networkBlocked':True,'elapsedSeconds':round(time.monotonic()-start,2),'sourceSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'referenceSha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'reviewStatus':'candidate','batchSize':1,'precision':'float16'}}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);parser.add_argument('--result',required=True);args=parser.parse_args()
    try:result=run(json.loads(pathlib.Path(args.request).read_text(encoding='utf-8-sig')))
    except Exception as exc:
        result={'outputs':[],'error':str(exc)}
        pathlib.Path(args.result).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');raise
    pathlib.Path(args.result).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
