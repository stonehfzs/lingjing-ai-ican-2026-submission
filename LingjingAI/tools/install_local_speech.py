"""Explicit online model installer; inference never calls this script."""
import argparse, json, os, pathlib, time
os.environ['HF_HUB_DISABLE_XET']='1'
from huggingface_hub import HfApi, snapshot_download
root=pathlib.Path(__file__).resolve().parents[1]
base=root/'.runtime/speech-models'; base.mkdir(parents=True,exist_ok=True)
a=argparse.ArgumentParser(); a.add_argument('--engine',choices=['asr','tts','all'],default='all'); args=a.parse_args()
for key,repo in [('asr','Systran/faster-whisper-small'),('tts','Qwen/Qwen3-TTS-12Hz-0.6B-Base')]:
    if args.engine not in ('all',key): continue
    start=time.time(); revision={'asr':'536b0662742c02347bc0e980a01041f333bce120','tts':'5d83992436eae1d760afd27aff78a71d676296fc'}[key]; info=HfApi().model_info(repo,revision=revision); dest=base/key
    print('Downloading',repo,info.sha,flush=True)
    snapshot_download(repo,revision=info.sha,local_dir=str(dest),max_workers=3)
    record={'modelId':repo,'modelPath':str(dest),'revision':info.sha,'license':getattr(info.card_data,'license',None),'downloadSeconds':round(time.time()-start,2)}
    (base/(key+'-source.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print('Downloaded',key,flush=True)

