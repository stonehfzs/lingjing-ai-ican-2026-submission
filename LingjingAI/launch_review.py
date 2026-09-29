"""Portable source-review launcher. No credentials or model downloads."""
from pathlib import Path
import argparse,json,os,shutil,socket,subprocess,sys,time,urllib.request,webbrowser
ROOT=Path(__file__).resolve().parent
def relocate():
 marker=ROOT/'data/review-root.json';old=json.loads(marker.read_text(encoding='utf-8')).get('root') if marker.exists() else None
 prefixes=['@APP_ROOT@']+([old,old.replace('\\','/')] if old else [])
 def move(v):
  if isinstance(v,dict):return {k:move(x) for k,x in v.items()}
  if isinstance(v,list):return [move(x) for x in v]
  if isinstance(v,str):
   for prefix in prefixes:
    if v.startswith(prefix):v=str(ROOT)+v[len(prefix):];break
  return v
 if old==str(ROOT):return
 for directory in ['data','media','preproduction','personal-library']:
  for p in (ROOT/directory).rglob('*.json'):
   try:doc=json.loads(p.read_text(encoding='utf-8-sig'))
   except (ValueError,UnicodeDecodeError):continue
   p.write_text(json.dumps(move(doc),ensure_ascii=False,indent=2),encoding='utf-8')
 if (ROOT/'studio-workspaces.json').exists():
  p=ROOT/'studio-workspaces.json';p.write_text(json.dumps(move(json.loads(p.read_text(encoding='utf-8-sig'))),ensure_ascii=False,indent=2),encoding='utf-8')
 marker.write_text(json.dumps({'root':str(ROOT)}),encoding='utf-8')
def free_port(start):
 for port in range(start,start+20):
  with socket.socket() as s:
   try:s.bind(('127.0.0.1',port));return port
   except OSError:pass
 raise RuntimeError('No free review port')
def main():
 a=argparse.ArgumentParser();a.add_argument('--port',type=int,default=8878);a.add_argument('--no-browser',action='store_true');args=a.parse_args()
 if sys.version_info<(3,11):raise RuntimeError('Python 3.11 or newer is required; tested with Python 3.12.')
 relocate()
 missing=[x for x in ['node','ffmpeg','ffprobe'] if not shutil.which(x)]
 if missing:print('Optional editing/media dependencies not on PATH:',', '.join(missing),'-- see README.md',flush=True)
 port=free_port(args.port);env={**os.environ,'PYTHONUTF8':'1','MINIMAX_DESIGN_GATEWAY':os.environ.get('MINIMAX_DESIGN_GATEWAY','http://127.0.0.1:9')}
 process=subprocess.Popen([sys.executable,'-X','utf8','-u',str(ROOT/'server.py'),'--port',str(port)],cwd=ROOT,env=env)
 try:
  for _ in range(60):
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:d=json.load(r)
    if Path(d['appRoot']).resolve()==ROOT:break
   except Exception:time.sleep(.3)
  else:raise RuntimeError('Review service did not become ready')
  url=f'http://127.0.0.1:{port}/?workspace=cangtou-film-v1-0';print('Lingjing AI:',url,flush=True)
  if not args.no_browser:webbrowser.open(url)
  process.wait()
 except KeyboardInterrupt:pass
 finally:
  if process.poll() is None:process.terminate();process.wait(timeout=10)
if __name__=='__main__':main()
