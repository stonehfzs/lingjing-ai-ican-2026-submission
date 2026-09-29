"""Export a scoped analysis packet; no generation or source mutation."""
import argparse
import json
from pathlib import Path
import urllib.request

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('invalid local port')
    if args.out.exists() and any(args.out.iterdir()):parser.error('output must be empty; preserve previous packet')
    args.out.mkdir(parents=True,exist_ok=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        req=urllib.request.Request(f'http://127.0.0.1:{args.port}'+path,headers={'X-Studio-Workspace':args.workspace})
        with opener.open(req,timeout=45) as response:return json.load(response)
    project=get('/api/project')
    data={'project-context.json':project,'script-blocks.json':get('/api/script'),
          'assets.json':get('/api/assets'),'workflow.json':get('/api/workflow'),
          'direction-draft.json':get('/api/direction')}
    for name,value in data.items():
        (args.out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (args.out/'script.md').write_text(project.get('script',''),encoding='utf8')
    print(json.dumps({'workspace':args.workspace,'out':str(args.out.resolve()),'shots':len(project.get('shots',[])),
        'analysisCompleted':False,'generated':False},ensure_ascii=False))

if __name__=='__main__':main()
