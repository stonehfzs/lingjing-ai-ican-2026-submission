"""Local reusable people/media library; use in a project creates a versioned copy."""
import base64,copy,pathlib,threading,uuid,hashlib
from workflow import WorkflowStore,WorkflowError,load,write,timestamp

class PersonalLibrary:
    def __init__(self,root):
        self.root=pathlib.Path(root)/'personal-library';self.store=WorkflowStore(self.root);self.lock=threading.RLock()
    def doc(self):return load(self.root/'people.json',{'revision':0,'people':[]})
    def listing(self):
        doc=self.doc();assets=self.store.assets()['assets']
        for a in assets:
            for key in ['mediaUrl','thumbnailUrl']:
                if (a.get(key) or '').startswith('/media/'):a[key]='/library'+a[key]
        return {**doc,'assets':assets}
    def person(self,body):
        name=str(body.get('name','')).strip()
        if not name or len(name)>80:raise WorkflowError('请输入人物/资料组名称（1–80字）')
        with self.lock:
            doc=self.doc();pid=body.get('id') or 'person-'+uuid.uuid4().hex[:12]
            row=next((p for p in doc['people'] if p['id']==pid),None)
            if row is None:row={'id':pid,'createdAt':timestamp()};doc['people'].append(row)
            row.update(name=name,description=str(body.get('description',''))[:3000],tags=[str(x)[:40] for x in body.get('tags',[])][:20],updatedAt=timestamp());doc['revision']+=1;write(self.root/'people.json',doc);return row
    def add(self,body):
        pid=body.get('personId')
        if pid and not any(p['id']==pid for p in self.doc()['people']):raise WorkflowError('资料组不存在')
        if body.get('dataBase64'):
            suffix=pathlib.Path(body.get('filename','')).suffix.lower()
            if suffix not in {'.jpg','.png','.jpeg','.webp','.wav','.mp3','.m4a','.mp4','.mov','.webm'}:raise WorkflowError('不支持这种媒体文件')
            try:data=base64.b64decode(body['dataBase64'],validate=True)
            except Exception:raise WorkflowError('文件编码无效')
            if len(data)>64*1024**2:raise WorkflowError('网页上传最多64MB，较大文件请使用本机路径导入')
            source=self.root/'incoming'/(uuid.uuid4().hex+suffix);source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(data)
        else:source=pathlib.Path(body.get('path',''))
        if not source.is_file():raise WorkflowError('文件不存在')
        with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        asset=self.store.import_asset({'id':'lib-'+(pid or 'general')+'-'+digest[:16],'path':str(source),'name':body.get('name') or pathlib.Path(body.get('filename') or str(source)).stem,'role':body.get('role','reference'),'characterId':pid,'notes':body.get('notes',''),'spokenText':body.get('spokenText',''),'provenance':{'source':'personal-library','originalFilename':body.get('filename') or source.name,'origin':copy.deepcopy(body.get('provenance')) if isinstance(body.get('provenance'),dict) else None}})
        return next(a for a in self.listing()['assets'] if a['id']==asset['id'])
    def use(self,w,body):
        asset=next((a for a in self.store.assets()['assets'] if a['id']==body.get('assetId')),None)
        if not asset:raise WorkflowError('资料库中找不到此素材')
        person=next((p for p in self.doc()['people'] if p['id']==asset.get('characterId')),None)
        return w.store.import_asset({'id':'library-'+asset['id'],'path':asset['path'],'name':asset['name'],'role':asset['role'],'characterId':asset.get('characterId'),'spokenText':asset.get('spokenText',''),'description':(person or {}).get('description',''),'provenance':{'source':'personal-library','libraryAssetId':asset['id'],'personId':asset.get('characterId'),'sha256':asset['sha256']},'reviewStatus':'candidate'})
    def edit_asset(self,aid,body):
        patch={k:v for k,v in body.items() if k in {'name','notes','role','spokenText','characterId','reviewStatus'}}
        if patch.get('characterId') and not any(p['id']==patch['characterId'] for p in self.doc()['people']):raise WorkflowError('资料组不存在')
        return self.store.patch_asset(aid,patch)
