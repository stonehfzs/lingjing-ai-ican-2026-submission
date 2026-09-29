"""Workspace-isolated FableCut integration. Keep the existing storyboard and jobs intact."""
import copy, hashlib, json, math, os, pathlib, re, shutil, socket, subprocess, threading, time, urllib.request, urllib.error, urllib.parse, uuid
from workflow import load, write, WorkflowError
from editing import media_catalog
from media_probe import binary, probe

class EditorBridge:
    def __init__(self,root):self.root=pathlib.Path(root);self.lock=threading.RLock();self.sessions={};self.proxy_jobs={}
    def directory(self,w):return w.data/'editor-v7'
    def call(self,session,path,body=None,method=None):
        req=urllib.request.Request(session['url']+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
        except urllib.error.HTTPError as exc:raise WorkflowError(exc.read().decode(errors='replace'),'EDITOR_CONFLICT' if exc.code==409 else 'EDITOR_ERROR',exc.code)
    def check(self,s,w):
        try:
            with urllib.request.urlopen(s['url']+'/api/studio/health',timeout=1) as r:d=json.load(r)
            return d.get('workspaceId')==w.id and pathlib.Path(d.get('dataDir','')).resolve()==self.directory(w).resolve() and d.get('nonce')==s.get('nonce')
        except Exception:return False
    def open(self,w):
        with self.lock:
            saved=self.sessions.get(w.id) or load(w.data/'editor-session.json',{})
            if saved and self.check(saved,w):self.sessions[w.id]=saved;return saved
            directory=self.directory(w);directory.mkdir(parents=True,exist_ok=True)
            if not (directory/'project.json').exists():self.seed(w)
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            nonce=uuid.uuid4().hex
            env={**os.environ,'PORT':str(port),'HOST':'127.0.0.1','FABLECUT_DATA_DIR':str(directory),'STUDIO_WORKSPACE':w.id,'STUDIO_NONCE':nonce,'PATH':str(pathlib.Path(binary('ffmpeg')).parent)+os.pathsep+os.environ.get('PATH','')}
            node=shutil.which('node.exe') or shutil.which('node')
            if not node:raise WorkflowError('未找到桌面剪辑运行环境Node.js')
            out=(directory/'server.log').open('ab');err=(directory/'server-error.log').open('ab')
            try:proc=subprocess.Popen([node,str(self.root/'vendor/fablecut/server.js')],env=env,cwd=self.root/'vendor/fablecut',stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            finally:out.close();err.close()
            session={'url':f'http://127.0.0.1:{port}','workspaceId':w.id,'nonce':nonce,'pid':proc.pid}
            for _ in range(80):
                if self.check(session,w):self.sessions[w.id]=session;write(w.data/'editor-session.json',session);return session
                if proc.poll() is not None:raise WorkflowError('剪辑服务启动失败，请查看editor-v7/server-error.log')
                time.sleep(.1)
            raise WorkflowError('剪辑服务启动超时，请重新打开剪辑。')
    def copy_asset(self,w,asset):
        directory=self.directory(w);src=pathlib.Path(asset['path']).resolve()
        if not src.is_file() or not src.is_relative_to(w.media.resolve()):raise WorkflowError('素材不属于当前工作区')
        digest=asset.get('sha256') or hashlib.sha256(src.read_bytes()).hexdigest();name=digest[:24]+src.suffix.lower()
        target=directory/'media'/name;target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():shutil.copy2(src,target)
        kind=asset.get('mediaType')
        info={'id':asset['id'],'name':asset['name'],'kind':kind,'src':'/media/'+name,'duration':asset.get('duration',5),'width':asset.get('width'),'height':asset.get('height'),'studioAssetId':asset['id']}
        if kind=='image':
            try:
                from PIL import Image
                with Image.open(src) as image:info.update(width=image.width,height=image.height)
            except Exception:pass
        return info
    def seed(self,w):
        directory=self.directory(w);project=load(w.project,{})
        old=load(w.data/'timeline.json',{});settings=old.get('settings',{})
        used={c['assetId'] for c in old.get('clips',[])+old.get('audio',[])}
        assets=[a for a in media_catalog(w) if (a.get('mediaType') in {'audio','video'} or a['id'] in used) and a.get('path') and pathlib.Path(a['path']).is_file()]
        media=[self.copy_asset(w,a) for a in assets];by={a['id']:a for a in media}
        doc={'name':project.get('title',w.name),'revision':1,'width':settings.get('width',1280),'height':settings.get('height',720),'fps':settings.get('fps',24),'background':'#101113','panSchema':1,'media':media,'clips':[],'tracks':[{'id':x,'kind':k} for x,k in [('V3','video'),('V2','video'),('V1','video'),('A1','audio'),('A2','audio')]],'studioWorkspaceId':w.id}
        cursor=0
        for c in old.get('clips',[]):
            if c['assetId'] not in by:continue
            duration=c['out']-c['in'];doc['clips'].append({'id':c['id'],'mediaId':c['assetId'],'kind':by[c['assetId']]['kind'],'track':'V1','start':cursor,'in':c['in'],'duration':duration,'name':by[c['assetId']]['name'],'props':{'volume':0 if c.get('mute') else c.get('gain',1)}});cursor+=duration
        for c in old.get('audio',[]):
            if c['assetId'] in by:doc['clips'].append({'id':c['id'],'mediaId':c['assetId'],'kind':'audio','track':'A1','start':c.get('at',0),'in':c['in'],'duration':c['out']-c['in'],'name':by[c['assetId']]['name'],'props':{'volume':0 if c.get('mute') else c.get('gain',1),'studioUnlinked':True}})
        for cue in old.get('subtitles',[]):doc['clips'].append(self.caption(doc,cue,'migration'))
        write(directory/'project.json',doc);write(directory/'migration.json',{'source':'timeline.json','revision':old.get('revision'),'copiedAssetCount':len(media),'oldFilePreserved':True})
    def project(self,w):return self.call(self.open(w),'/api/project')
    def save(self,w,doc):
        if not isinstance(doc,dict) or not isinstance(doc.get('clips'),list) or len(doc['clips'])>10000:raise WorkflowError('剪辑数据无效')
        return self.call(self.open(w),'/api/project',doc,'PUT')
    def import_assets(self,w,ids):
        with self.lock:
            session=self.open(w);doc=self.call(session,'/api/project');assets={a['id']:a for a in media_catalog(w)};existing={m['id'] for m in doc.get('media',[])};added=[]
            for aid in ids:
                if aid not in assets:raise WorkflowError('当前项目找不到此素材')
                if aid not in existing:
                    item=self.copy_asset(w,assets[aid]);doc['media'].append(item);existing.add(aid);added.append(item)
            if added:doc['revision']+=1;self.call(session,'/api/project',doc,'PUT')
            return {'added':added,'revision':doc['revision']}
    def caption(self,doc,cue,batch,position='bottom',font_size=None):
        y={'top':-.36,'center':0,'bottom':.36}.get(position,.36)*doc['height']
        return {'id':'caption-'+uuid.uuid4().hex[:14],'kind':'text','mediaId':None,'track':'V3','start':cue['start'],'in':0,'duration':cue['end']-cue['start'],'name':cue['text'][:24],'props':{'text':cue['text'],'font':'Microsoft YaHei UI','fontSize':font_size or round(doc['height']*.048),'x':0,'y':y,'color':'#ffffff','strokeWidth':2,'strokeColor':'#151515','textShadow':4,'bold':False,'boxW':doc['width']*.85,'studioCaptionBatch':batch}}
    def apply_captions(self,w,body,jobs):
        with self.lock:
            doc=self.project(w)
            if body.get('revision')!=doc['revision']:raise WorkflowError('识别期间时间线已更新，请刷新后检查再应用。','REVISION_CONFLICT',409)
            job=next((j for j in jobs if j['id']==body.get('jobId') and j.get('status')=='succeeded'),None)
            if not job or not job.get('transcript'):raise WorkflowError('字幕识别任务未完成')
            batch='asr-'+job['id'];doc['clips']=[c for c in doc['clips'] if not (c.get('props',{}).get('studioCaptionBatch')==batch or (body.get('replaceAuto') and c.get('props',{}).get('studioCaptionBatch')))]
            if body.get('replaceClipAuto') and job.get('editorClip'):
                context=job['editorClip'];left=context['start'];right=left+context['duration']
                current=next((c for c in doc['clips'] if c['id']==context['id']),None)
                if not current or any(current.get(k)!=context.get(k) for k in ('mediaId','start','in','duration')):raise WorkflowError('原片段已改变，请重新识别或手动放置字幕')
                doc['clips']=[c for c in doc['clips'] if not (c.get('props',{}).get('studioCaptionBatch') and c['start']<right and c['start']+c['duration']>left)]
            for cue in job['transcript']['segments']:
                cue=copy.deepcopy(cue);offset=float(body.get('offset',0));cue['start']=max(0,cue['start']+offset);cue['end']+=offset
                if cue['end']>cue['start']:doc['clips'].append(self.caption(doc,cue,batch,body.get('position','bottom')))
            doc['revision']+=1;self.save(w,doc);return doc
    def media_path(self,w,src,folder='media'):
        name=pathlib.PurePosixPath(urllib.parse.unquote(str(src))).name
        if not name or name in {'.','..'}:raise WorkflowError('文件名无效')
        target=(self.directory(w)/folder/name).resolve()
        if not target.is_relative_to((self.directory(w)/folder).resolve()) or not target.is_file():raise WorkflowError('编辑器文件不存在')
        return target
    def recover(self,w,name):
        target=self.media_path(w,name,'exports')
        return w.store.import_asset({'path':str(target),'name':target.stem,'role':'result','provenance':{'source':'fablecut-editor','workspaceId':w.id}})
    def prepare_proxy(self,w,aid):
        doc=self.project(w);m=next((m for m in doc['media'] if m['id']==aid and m['kind']=='video'),None)
        if not m:raise WorkflowError('请选择视频素材')
        key=w.id+':'+aid
        with self.lock:
            if key in self.proxy_jobs and self.proxy_jobs[key]['status']=='running':return self.proxy_jobs[key]
            self.proxy_jobs[key]={'status':'running','assetId':aid,'workspaceId':w.id}
        def worker():
            try:
                source=self.media_path(w,m['src']);name='proxy-'+hashlib.sha256(source.read_bytes()).hexdigest()[:20]+'.mp4';out=self.directory(w)/'media'/name
                if not out.exists():
                    result=subprocess.run([binary('ffmpeg'),'-v','error','-y','-i',str(source),'-vf',"scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",'-c:v','libx264','-preset','ultrafast','-crf','23','-g','12','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(out)],capture_output=True,timeout=1800,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace')[-1000:])
                if probe(out,False).get('probeStatus')!='verified':raise RuntimeError('代理文件无效')
                self.proxy_jobs[key].update(status='succeeded',src='/media/'+name)
            except Exception as e:self.proxy_jobs[key].update(status='failed',error=str(e))
        threading.Thread(target=worker,daemon=True).start();return self.proxy_jobs[key]

    def prepare_clip_source(self,w,body):
        """Take a saved clip's actual trim for local AI, without modifying its source."""
        doc=self.project(w)
        if body.get('revision')!=doc['revision']:raise WorkflowError('剪辑已更新，请保存后重试。','REVISION_CONFLICT',409)
        operation=body.get('operation')
        if operation not in {'extract_audio','separate_vocals','transcribe','voice_convert','speech_edit','lip_sync','tts'}:raise WorkflowError('不支持此剪辑处理')
        clip=next((c for c in doc['clips'] if c['id']==body.get('clipId')),None)
        if not clip or clip.get('kind') not in {'audio','video'}:raise WorkflowError('请选择一个声音或视频片段')
        if float(clip.get('props',{}).get('speed',1))!=1 or (clip.get('keyframes') or {}).get('speed'):raise WorkflowError('变速片段请先导出，再进行模型处理；当前处理保留原始表演速度。')
        if operation=='lip_sync' and clip['kind']!='video':raise WorkflowError('口型修复需要视频片段')
        media=next((m for m in doc['media'] if m['id']==clip['mediaId']),None)
        if not media:raise WorkflowError('找不到片段源素材')
        source=self.media_path(w,media['src']);info=probe(source,False)
        start=float(clip.get('in',0));duration=float(clip['duration'])
        if not all(math.isfinite(v) for v in (start,duration)) or start<0 or duration<=0 or start+duration>float(info.get('duration',0))+.06:raise WorkflowError('片段裁切范围无效')
        limit=15 if operation=='lip_sync' else 30 if operation in {'tts','voice_convert','speech_edit'} else 1800
        if duration>limit:raise WorkflowError(f'此操作请先裁成{limit}秒以内的片段')
        if operation!='lip_sync' and not any(s.get('codec_type')=='audio' for s in info.get('streams',[])):raise WorkflowError('此片段没有声音')
        with source.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        video=operation=='lip_sync';digest=hashlib.sha256(json.dumps([sha,start,duration,video,clip.get('props',{}).get('audioChannel')],sort_keys=True).encode()).hexdigest()[:24]
        target=w.media/'editor-inputs'/(digest+('.mp4' if video else '.wav'));target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():
            args=[binary('ffmpeg'),'-v','error','-y','-ss',str(start),'-i',str(source),'-t',str(duration)]
            if video:args+=['-map','0:v:0','-an','-c:v','libx264','-crf','18','-pix_fmt','yuv420p']
            else:
                args+=['-vn']
                ch=clip.get('props',{}).get('audioChannel')
                if isinstance(ch,int) and ch>=0:args+=['-af',f'pan=mono|c0=c{ch}']
                args+=['-ar','48000','-c:a','pcm_s16le']
            temporary=target.with_name(target.stem+'-'+uuid.uuid4().hex+target.suffix)
            try:
                result=subprocess.run(args+[str(temporary)],capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:raise WorkflowError('截取模型输入失败：'+result.stderr.decode(errors='replace')[-600:])
                os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        asset=w.store.import_asset({'path':str(target),'name':clip.get('name','片段')+' · 模型输入','role':'reference','provenance':{'source':'editor-clip','clipId':clip['id'],'sourceHash':sha,'in':start,'duration':duration}})
        spec={k:body[k] for k in ('operation','requestId','referenceAssetId','referenceText','text','sourceText','instruction','language','device') if k in body}
        if operation=='tts':spec['referenceAssetId']=asset['id']
        else:spec['assetId']=asset['id']
        context={k:clip.get(k) for k in ('id','mediaId','start','in','duration','track','kind')}
        return spec,context
