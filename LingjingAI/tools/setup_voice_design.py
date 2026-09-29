"""Explicit download, offline inference, and readiness registration for VoiceDesign."""
from pathlib import Path
import json,os,sys,subprocess,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from huggingface_hub import snapshot_download
from media_probe import probe
model='Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign';revision='5ecdb67327fd37bb2e042aab12ff7391903235d3';dest=ROOT/'.runtime/speech-models/voice-design'
snapshot_download(model,revision=revision,local_dir=str(dest),max_workers=3)
expected='391e8db219f292c515297cdceeb43e4eae67cdde35fa57e79a6a8a532fca0522'
with (dest/'model.safetensors').open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==expected,'Model hash mismatch'
out=ROOT/'.runtime/voice-design-check';out.mkdir(parents=True,exist_ok=True)
request={'mode':'design','device':'cuda','modelPath':str(dest),'modelId':model,'modelRevision':revision,'blockNetwork':True,'outputDir':str(out),'text':'欢迎来到灵镜AI。','description':'自然清晰的中文青年男声，像平静地和朋友说话。','performance':'正常语速，语气自然。','seed':1234,'temperature':.8}
(out/'request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
subprocess.run([sys.executable,str(ROOT/'tools/voice_lab_worker.py'),'--request',str(out/'request.json'),'--result',str(out/'result.json')],check=True)
result=json.loads((out/'result.json').read_text(encoding='utf-8'));info=probe(Path(result['path']),False)
assert info.get('probeStatus')=='verified' and info.get('duration',0)>0,'Generated audio did not validate; ensure ffprobe is installed'
record={'ready':True,'status':'verified','message':'本机离线短样本已验证，音质仍需试听。','modelId':model,'revision':revision,'modelPath':str(dest),'python':sys.executable,'validatedDevices':['cuda'],'license':'Apache-2.0','sourceUrl':'https://huggingface.co/'+model,'validation':result['metadata']}
(ROOT/'data/voice-design-deployment.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');print('VoiceDesign is ready. Refresh the model panel.')
