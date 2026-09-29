import argparse,concurrent.futures,hashlib,json,pathlib,subprocess,sys,time,urllib.request,venv,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
SOURCES={'voice':('Plachtaa/seed-vc','51383efd921027683c89e5348211d93ff12ac2a8'),'dots':('studio-dots-ai/dots.tts','32407a55228630475c48ecdb2c4e2c0f9c09e030')}
MODELS={'voice':{'Plachta/Seed-VC':('257283f9f41585055e8f858fba4fd044e5caed6e',['DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth','config_dit_mel_seed_uvit_whisper_small_wavenet.yml']),'funasr/campplus':('e4b6ede7ce16997aff4ae69fbca1f0175e2afede',['campplus_cn_common.bin']),'nvidia/bigvgan_v2_22khz_80band_256x':('633ff708ed5b74903e86ff1298cf4a98e921c513',['config.json','bigvgan_generator.pt']),'openai/whisper-small':('973afd24965f72e36ca33b3055d56a652f456b4d',['config.json','preprocessor_config.json','model.safetensors'])},'dots':{'dots-studio/dots.tts.edit':('e2aea9cab95a802a63f1a491702a1cc8bf796b30',['README.md','added_tokens.json','chat_template.jinja','config.json','latent_stats.pt','llm_config.json','merges.txt','model.safetensors','speaker_encoder.safetensors','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.json','vocoder.safetensors'])}}
def json_url(url):
 try:
  with urllib.request.urlopen(url,timeout=90) as r:return json.load(r)
 except urllib.error.URLError:
  return json.loads(subprocess.check_output(['curl.exe','-L','--fail','--silent','--show-error','--max-time','120',url]))
def fetch(url,out,size):
 if out.is_file() and out.stat().st_size==size:return
 parts=out.parent/(out.name+'.chunks');parts.mkdir(parents=True,exist_ok=True);chunk=8*1024*1024
 def part(i):
  p=parts/str(i);a=i*chunk;b=min(size-1,a+chunk-1)
  if p.exists() and p.stat().st_size==b-a+1:return p
  for attempt in range(5):
   try:
    req=urllib.request.Request(url+f'?download=true&chunk={i}',headers={'Range':f'bytes={a}-{b}'})
    try:
     with urllib.request.urlopen(req,timeout=120) as r:data=r.read()
    except urllib.error.URLError:
     temp=p.with_suffix('.curl-part')
     subprocess.run(['curl.exe','-L','--fail','--silent','--show-error','--max-time','180','--range',f'{a}-{b}',url+f'?download=true&chunk={i}','-o',str(temp)],check=True)
     data=temp.read_bytes();temp.unlink()
    if len(data)!=b-a+1:raise IOError(f'Unexpected chunk size {len(data)}')
    p.write_bytes(data);print(out.name,i,flush=True);return p
   except Exception:
    if attempt==4:raise
 with concurrent.futures.ThreadPoolExecutor(8) as pool:paths=list(pool.map(part,range((size+chunk-1)//chunk)))
 staging=out.with_suffix(out.suffix+'.assembling')
 with staging.open('wb') as f:
  for p in paths:f.write(p.read_bytes())
 staging.replace(out)
 for p in paths:p.unlink()
 parts.rmdir()
def main():
 p=argparse.ArgumentParser(description='Install fixed local voice engines; never calls paid services or marks inference ready.');p.add_argument('--engine',choices=SOURCES,required=True);p.add_argument('--models-only',action='store_true');p.add_argument('--verify-only',action='store_true');a=p.parse_args();folder=ROOT/'.runtime'/a.engine;folder.mkdir(parents=True,exist_ok=True)
 if not a.models_only and not a.verify_only:
  repo,rev=SOURCES[a.engine];archive=folder/'source.zip';source=folder/(repo.split('/')[-1]+'-'+rev)
  if not source.exists():
   urllib.request.urlretrieve(f'https://codeload.github.com/{repo}/zip/{rev}',archive)
   with zipfile.ZipFile(archive) as z:z.extractall(folder)
  env=folder/'venv'
  if not (env/'Scripts/python.exe').exists():venv.EnvBuilder(with_pip=True,system_site_packages=True).create(env)
  (env/'Lib/site-packages/shared_audio.pth').write_text(str(ROOT/'.runtime/audio/Lib/site-packages'))
  py=str(env/'Scripts/python.exe');constraints=folder/'constraints.txt';constraints.write_text('torch==2.8.0+cu128\ntorchaudio==2.8.0+cu128\n')
  subprocess.run([py,'-m','pip','install','torchaudio==2.8.0','--index-url','https://download.pytorch.org/whl/cu128'],check=True)
  deps=['transformers==4.46.3','librosa==0.10.2','munch==4.0.0','einops==0.8.0','hydra-core==1.3.2','descript-audio-codec==1.0.0','accelerate','huggingface-hub','soundfile'] if a.engine=='voice' else ['transformers==4.57.6','librosa==0.11.0','soundfile>=0.13.1','einops','loguru','langcodes[data]','numpy==2.2.6','pydantic>=2.12.5','PyYaml>=6.0.3','safetensors>=0.8','torchdiffeq']
  subprocess.run([py,'-m','pip','install','-c',str(constraints),*deps],check=True)
  if a.engine=='dots':
   text=source/'src/dots_tts/utils/text.py';s=text.read_text(encoding='utf-8');s=s.replace('from tn.chinese.normalizer import Normalizer as ZhNormalizer\n','').replace('from tn.english.normalizer import Normalizer as EnNormalizer\n','');s=s.replace('    return ZhNormalizer()','    from tn.chinese.normalizer import Normalizer as ZhNormalizer\n    return ZhNormalizer()').replace('    return EnNormalizer()','    from tn.english.normalizer import Normalizer as EnNormalizer\n    return EnNormalizer()');s=s.replace('from lingua import Language, LanguageDetectorBuilder\n','').replace('def get_language_detector():','def get_language_detector():\n    from lingua import Language, LanguageDetectorBuilder');text.write_text(s,encoding='utf-8')
 lock={}
 for repo,(rev,files) in MODELS[a.engine].items():
  meta=json_url(f'https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true');entries={x['rfilename']:x for x in meta['siblings']};dest=folder/'model' if a.engine=='dots' else folder/'models'/repo.replace('/','--');dest.mkdir(parents=True,exist_ok=True);checks=[]
  for name in files:
   entry=entries[name];out=dest/name;size=entry.get('size',entry.get('lfs',{}).get('size'))
   if not size:raise RuntimeError('Missing published file size: '+name)
   if not a.verify_only:fetch(f'https://huggingface.co/{repo}/resolve/{rev}/{name}',out,size)
   if out.stat().st_size!=size:raise RuntimeError('Size mismatch: '+str(out))
   with out.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
   expected=entry.get('lfs',{}).get('sha256')
   if expected and sha!=expected:raise RuntimeError('SHA256 mismatch: '+str(out))
   checks.append({'file':name,'size':size,'sha256':sha,'publishedSha256':expected})
  lock[repo]={'revision':rev,'path':str(dest),'files':checks};(folder/'model-lock.json').write_text(json.dumps(lock,indent=2))
 print('Installation files complete. ready remains false until a real offline inference succeeds.')
if __name__=='__main__':main()
