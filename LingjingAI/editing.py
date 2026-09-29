"""Non-destructive project editing and local audio processing. No cloud calls."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

from media_probe import binary, probe
from workflow import WorkflowError, load, write, timestamp
from local_ai import ENGINE_SPECS, engine_catalog, validate_spec as validate_ai_spec, run_worker

_PROBES = {}
_DEVICE_STATE = {'checked':0,'cuda':False,'deviceName':'CPU'}


def current_device():
    # Historical deployment proof is not proof that a laptop's dGPU is enabled now.
    if time.monotonic()-_DEVICE_STATE['checked']<30:return dict(_DEVICE_STATE)
    found=False;name='CPU';smi=shutil.which('nvidia-smi')
    if smi:
        try:
            result=subprocess.run([smi,'--query-gpu=name','--format=csv,noheader'],capture_output=True,text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            found=result.returncode==0 and bool(result.stdout.strip())
            if found:name=result.stdout.strip().splitlines()[0]
        except (OSError,subprocess.TimeoutExpired):pass
    _DEVICE_STATE.update(checked=time.monotonic(),cuda=found,deviceName=name)
    return dict(_DEVICE_STATE)

def cached_probe(path):
    path=Path(path);stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
    if key not in _PROBES:
        if len(_PROBES)>512:_PROBES.clear()
        _PROBES[key]=probe(path,waveform=False)
    return copy.deepcopy(_PROBES[key])


def fail(message, code='INVALID_EDIT'):
    raise WorkflowError(message, code, 400)


def number(value, name, low=0, high=86400):
    if isinstance(value, bool): fail(name+' 必须为数字')
    try: value=float(value)
    except (TypeError, ValueError): fail(name+' 必须为数字')
    if not math.isfinite(value) or not low <= value <= high: fail(name+' 超出范围')
    return value


def media_catalog(workspace):
    rows=copy.deepcopy(workspace.store.assets()['assets'])
    for job in load(workspace.jobs, []):
        if job.get('status')!='succeeded' or job.get('kind') not in {'video','audio'}: continue
        if job.get('assetId'): continue
        path=job.get('sourcePath') or job.get('mediaPath')
        if not path or not Path(path).is_file(): continue
        info=cached_probe(path)
        rows.append({'id':'job:'+job['id'],'name':(job.get('shotId') or '')+' · 生成源片',
            'path':path,'mediaType':job['kind'],'mediaUrl':job.get('sourceMediaUrl') or job.get('mediaUrl'),
            'thumbnailUrl':job.get('thumbnailUrl'),'reviewStatus':'candidate','shotId':job.get('shotId'),
            'validationOnly':job.get('validationOnly',False), **info})
    return rows


def resolve_media(workspace, aid, kinds):
    asset=next((a for a in media_catalog(workspace) if a['id']==aid),None)
    if not asset or asset.get('mediaType') not in kinds: fail('请选择当前项目中的'+ '/'.join(kinds)+'素材。','MEDIA_NOT_FOUND')
    path=Path(asset.get('path','')).resolve()
    if not path.is_file() or not path.is_relative_to(workspace.media.resolve()): fail('媒体不在当前项目资源库中。','MEDIA_NOT_FOUND')
    info=cached_probe(path)
    if info.get('probeStatus')!='verified': fail('无法读取素材实际时长。')
    return {**asset,**info,'path':str(path)}


def empty_timeline(workspace):
    return {'schemaVersion':1,'revision':0,'projectId':load(workspace.project,{})['id'],
            'name':'主剪辑','clips':[],'audio':[],'subtitles':[],
            'settings':{'width':1280,'height':720,'fps':24}}


def validate_timeline(workspace, timeline):
    if not isinstance(timeline,dict) or timeline.get('projectId')!=load(workspace.project,{})['id']:
        fail('剪辑必须属于当前项目。')
    doc=copy.deepcopy(timeline)
    if doc.get('schemaVersion')!=1: fail('剪辑格式版本无效。')
    settings=doc.get('settings',{})
    if (settings.get('width'),settings.get('height')) not in {(1280,720),(1920,1080),(720,1280),(1080,1920)} or settings.get('fps') not in {24,25,30}:
        fail('导出规格不支持。')
    ids=set(); total=0
    for field,kinds in [('clips',{'video'}),('audio',{'audio','video'})]:
        rows=doc.get(field)
        if not isinstance(rows,list) or len(rows)>250: fail('每类剪辑最多250段。')
        for clip in rows:
            if not isinstance(clip,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(clip.get('id',''))) or clip['id'] in ids:
                fail('片段ID缺失或重复。')
            ids.add(clip['id'])
            asset=resolve_media(workspace,clip.get('assetId'),kinds)
            if field=='audio' and not any(s['codec_type']=='audio' for s in asset['streams']): fail('所选素材没有音轨。')
            start=number(clip.get('in',0),'素材入点',high=asset['duration'])
            end=min(number(clip.get('out',asset['duration']),'素材出点',high=asset['duration']+.05),asset['duration'])
            if end-start<.04: fail('片段至少保留一帧的时长。')
            clip.update({'in':start,'out':min(end,asset['duration']),'gain':number(clip.get('gain',1),'音量',0,2),'mute':bool(clip.get('mute',False))})
            if field=='audio':clip['at']=number(clip.get('at',0),'音轨开始时间',0,7200)
            else: total+=clip['out']-clip['in']
    if total>7200:fail('当前单条时间线最多2小时。')
    if not isinstance(doc.get('subtitles',[]),list) or len(doc.get('subtitles',[]))>1000:fail('字幕列表无效。')
    for cue in doc.get('subtitles',[]):
        cue['start']=number(cue.get('start'),'字幕起点',0,max(total,0))
        cue['end']=number(cue.get('end'),'字幕终点',0,max(total,0))
        if cue['end']<=cue['start'] or not isinstance(cue.get('text'),str) or len(cue['text'])>1000:fail('字幕时段或文本无效。')
    doc['duration']=round(total,6)
    return doc


def save_timeline(workspace, body):
    path=workspace.data/'timeline.json';old=load(path,empty_timeline(workspace))
    if body.get('revision')!=old['revision']:raise WorkflowError('时间线已有新版本，请重新载入。','REVISION_CONFLICT',409)
    doc=validate_timeline(workspace,body)
    if old['revision']:write(workspace.data/'snapshots'/f'timeline-r{old["revision"]}.json',old)
    doc.update(revision=old['revision']+1,updatedAt=timestamp());write(path,doc);return doc


def import_transcript(workspace, job, revision, offset=0):
    """Import only this workspace's result, replacing its previous import idempotently."""
    timeline=load(workspace.data/'timeline.json',empty_timeline(workspace))
    if revision!=timeline['revision']:raise WorkflowError('时间线已更新。','REVISION_CONFLICT',409)
    if not job or job.get('workspaceId')!=workspace.id or job.get('status')!='succeeded' or not job.get('transcript'):
        fail('识别任务不存在或尚未成功。')
    timeline=validate_timeline(workspace,timeline)
    if not timeline['duration']:fail('请先将对应视频加入时间线；也可以先单独下载SRT。')
    offset=number(offset,'字幕偏移',-7200,7200);cues=[]
    for segment in job['transcript'].get('segments',[]):
        start=max(0,segment['start']+offset);end=min(timeline['duration'],segment['end']+offset)
        if end>start:cues.append({'start':start,'end':end,'text':segment.get('text',''),'sourceLocalJobId':job['id']})
    if not cues:fail('字幕与时间线没有重叠，请检查偏移。')
    timeline['subtitles']=[c for c in timeline.get('subtitles',[]) if c.get('sourceLocalJobId')!=job['id']]+cues
    timeline['subtitles'].sort(key=lambda c:c['start'])
    return save_timeline(workspace,timeline)


def run(command, log, timeout=7200, env=None, cwd=None):
    with Path(log).open('ab') as out:
        proc=subprocess.run(command,stdout=out,stderr=out,timeout=timeout,env=env,cwd=cwd,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode:
        tail=Path(log).read_text(encoding='utf8',errors='replace')[-2500:]
        raise RuntimeError('本地处理失败：'+tail)


def srt_time(seconds):
    ms=round(seconds*1000);hours,ms=divmod(ms,3600000);minutes,ms=divmod(ms,60000);seconds,ms=divmod(ms,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def render_timeline(workspace, timeline, directory, progress=lambda **_:None):
    doc=validate_timeline(workspace,timeline)
    if not doc['clips']:fail('时间线没有视频片段。')
    ff=binary('ffmpeg')
    if not ff:fail('FFmpeg 不可用。')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);log=directory/'ffmpeg.log'
    width,height,fps=(doc['settings'][k] for k in ['width','height','fps'])
    for i,clip in enumerate(doc['clips']):
        progress(progress=round(i/len(doc['clips'])*75),stage=f'处理视频 {i+1}/{len(doc["clips"])}')
        asset=resolve_media(workspace,clip['assetId'],{'video'});duration=clip['out']-clip['in']
        has_audio=any(s['codec_type']=='audio' for s in asset['streams']) and not clip['mute']
        cmd=[ff,'-hide_banner','-loglevel','error','-y','-ss',str(clip['in']),'-t',str(duration),'-i',asset['path']]
        if not has_audio:cmd+=['-f','lavfi','-t',str(duration),'-i','anullsrc=r=48000:cl=stereo']
        cmd+=['-map','0:v:0','-map','0:a:0' if has_audio else '1:a:0','-vf',
              f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}',
              '-af',f'aresample=48000,apad,volume={clip["gain"]}','-t',str(duration),
              '-c:v','libx264','-preset','veryfast','-crf','20','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2',str(directory/f'segment{i:03}.mp4')]
        run(cmd,log)
    (directory/'concat.txt').write_text(''.join(f"file 'segment{i:03}.mp4'\n" for i in range(len(doc['clips']))),encoding='utf8')
    run([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','1','-i','concat.txt','-c','copy','base.mp4'],log,cwd=directory)
    progress(progress=80,stage='合成声音与导出')
    cmd=[ff,'-hide_banner','-loglevel','error','-y','-i',str(directory/'base.mp4')];filters=['[0:a]asetpts=PTS-STARTPTS[a0]'];labels=['[a0]']
    for i,clip in enumerate([c for c in doc['audio'] if not c['mute']],1):
        a=resolve_media(workspace,clip['assetId'],{'audio','video'});cmd+=['-i',a['path']]
        filters.append(f'[{i}:a]atrim=start={clip["in"]}:end={clip["out"]},asetpts=PTS-STARTPTS,aresample=48000,volume={clip["gain"]},adelay={round(clip["at"]*1000)}:all=1[a{i}]')
        labels.append(f'[a{i}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=longest:normalize=0,alimiter=limit=0.95,atrim=duration={doc["duration"]}[mix]')
    script=directory/'audio-filter.txt';script.write_text(';\n'.join(filters),encoding='utf8')
    output=directory/'film.mp4'
    cmd+=['-filter_complex_script',str(script),'-map','0:v:0','-map','[mix]','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(doc['duration']),'-movflags','+faststart',str(output)]
    run(cmd,log)
    subtitles=directory/'film.srt'
    subtitles.write_text('\n\n'.join(f'{i+1}\n{srt_time(c["start"])} --> {srt_time(c["end"])}\n{c["text"]}' for i,c in enumerate(doc.get('subtitles',[]))),encoding='utf-8-sig')
    return [(output,'video','剪辑成片','result')], {'subtitlePath':str(subtitles),'timelineRevision':doc['revision'],'duration':doc['duration']}


def process_audio(workspace, app_root, spec, directory, progress=lambda **_:None):
    asset=resolve_media(workspace,spec.get('assetId'),{'audio','video'})
    if not any(s['codec_type']=='audio' for s in asset['streams']):fail('该视频没有声音；无法提取不存在的音轨。','NO_AUDIO_STREAM')
    ff=binary('ffmpeg');directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    operation=spec['operation'];start=number(spec.get('in',0),'入点',high=asset['duration']);end=number(spec.get('out',asset['duration']),'出点',high=asset['duration']+.05)
    if end<=start:fail('音轨出点必须晚于入点。')
    output=directory/'audio.wav';log=directory/'audio.log'
    progress(progress=10,stage='读取音轨')
    run([ff,'-hide_banner','-loglevel','error','-y','-ss',str(start),'-t',str(end-start),'-i',asset['path'],'-vn','-map','0:a:0','-ar','44100','-ac','2','-c:a','pcm_s24le',str(output)],log)
    if operation=='extract_audio':return [(output,'audio',asset['name']+' · 提取音轨','reference')],{}
    if operation!='separate_vocals':fail('未知音频工具。')
    python=Path(app_root)/'.runtime/audio/Scripts/python.exe'
    if not python.is_file():fail('本地人声分离模型尚未安装。')
    progress(progress=20,stage='Demucs 分离人声与其余声音')
    env={**os.environ,'HF_HOME':str(Path(app_root)/'.runtime/models/huggingface'),'TORCH_HOME':str(Path(app_root)/'.runtime/models/torch'),
         'PYTHONUTF8':'1','PATH':str(Path(ff).parent)+os.pathsep+os.environ.get('PATH','')}
    run([str(python),'-m','demucs','-n','htdemucs','--two-stems','vocals','--float32','--segment','7','--shifts','0','-d',spec.get('device','cpu'),'-o',str(directory/'stems'),str(output)],log,env=env)
    folder=directory/'stems/htdemucs/audio'
    return [(folder/'vocals.wav','audio',asset['name']+' · 分离人声','dialogue_performance'),
            (folder/'no_vocals.wav','audio',asset['name']+' · 其余声音','ambience')],{'model':'htdemucs','note':'音乐源分离模型；影视对白可能残留音乐/拟声，不等于按人物分轨。'}


class LocalJobs:
    """A shared lock protects files; each worker receives an explicit workspace."""
    def __init__(self, app_root):self.app_root=Path(app_root);self.lock=threading.RLock()
    def get(self,workspace):return load(workspace.data/'local-jobs.json',[])
    def update(self,workspace,jid,**changes):
        with self.lock:
            jobs=self.get(workspace);job=next(j for j in jobs if j['id']==jid);job.update(changes,updatedAt=timestamp());write(workspace.data/'local-jobs.json',jobs);return copy.deepcopy(job)
    def prepare(self,workspace,spec):
        rid=spec.get('requestId','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',rid):fail('本地任务需要稳定 requestId。')
        digest=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
        with self.lock:
            jobs=self.get(workspace);old=next((j for j in jobs if j.get('requestId')==rid),None)
            if old:
                if old['requestHash']!=digest:raise WorkflowError('相同请求ID不能换参数。','IDEMPOTENCY_CONFLICT',409)
                return old,False
            operation=spec.get('operation')
            if operation=='render':
                timeline=load(workspace.data/'timeline.json',empty_timeline(workspace))
                if spec.get('revision')!=timeline['revision']:raise WorkflowError('请先保存并使用当前剪辑版本。','REVISION_CONFLICT',409)
                timeline=validate_timeline(workspace,timeline)
                if not timeline['clips']:fail('先把视频添加到时间线。')
                payload={'timeline':timeline}
            elif operation in {'extract_audio','separate_vocals'}:
                a=resolve_media(workspace,spec.get('assetId'),{'audio','video'})
                if not any(s['codec_type']=='audio' for s in a['streams']):fail('所选文件没有音轨。','NO_AUDIO_STREAM')
                if spec.get('device','cpu') not in {'cpu','cuda'}:fail('设备无效。')
                payload=spec
            elif operation in ENGINE_SPECS:
                payload=validate_ai_spec(workspace,self.app_root,spec,resolve_media)
            else:fail('本地处理类型无效。')
            job={'id':uuid.uuid4().hex,'requestId':rid,'requestHash':digest,'operation':operation,'workspaceId':workspace.id,'status':'queued','progress':0,'payload':payload,'createdAt':timestamp()}
            jobs.append(job);write(workspace.data/'local-jobs.json',jobs);return job,True
    def execute(self,workspace,jid):
        with self.lock:
            job=next(j for j in self.get(workspace) if j['id']==jid)
            if job['status']!='queued':return
            self.update(workspace,jid,status='running')
        try:
            directory=workspace.media/'local'/jid
            progress=lambda **kwargs:self.update(workspace,jid,**kwargs)
            if job['operation']=='render':outputs,extra=render_timeline(workspace,job['payload']['timeline'],directory,progress)
            elif job['operation'] in ENGINE_SPECS:outputs,extra=run_worker(workspace,self.app_root,job['payload'],directory,progress)
            else:outputs,extra=process_audio(workspace,self.app_root,job['payload'],directory,progress)
            registered=[]
            for index,(path,kind,name,role) in enumerate(outputs):
                if not path.is_file():raise RuntimeError('本地模型未返回预期文件。')
                registered.append(workspace.store.import_asset({'id':'local-'+jid+'-'+str(index),'path':str(path),'kind':kind,'name':name,'role':role,'reviewStatus':'candidate',
                    'spokenText':extra.get('targetText') or job['payload'].get('text'),'cueIds':[job['payload']['cueId']] if job['payload'].get('cueId') else [],
                    'voiceId':job['payload'].get('voiceId'),
                    'provenance':{'source':'local-processing','operation':job['operation'],'sourceAssetId':job['payload'].get('assetId') or job['payload'].get('sourceAssetId'),'localJobId':jid,'engine':extra.get('engine'),'referenceAssetId':job['payload'].get('referenceAssetId')}}))
            if extra.get('subtitlePath'):extra['subtitleUrl']='/media/'+Path(extra['subtitlePath']).relative_to(workspace.media).as_posix()
            self.update(workspace,jid,status='succeeded',progress=100,stage='完成',assets=registered,**extra)
        except Exception as exc:self.update(workspace,jid,status='failed',error=str(exc)[-2800:])


def local_capabilities(app_root):
    manifest=load(Path(app_root)/'.runtime/audio-capabilities.json',{})
    device=current_device();engines=engine_catalog(app_root)
    for engine in engines:
        if engine['ready'] and engine['device']=='cuda' and not device['cuda']:
            engine.update(ready=False,status='device_unavailable',message='模型已部署，但当前未检测到可用NVIDIA独显。请恢复独显后刷新，或使用已验证的CPU引擎。')
    return {'ffmpeg':bool(binary('ffmpeg')),'demucs':manifest.get('demucs',False),
        'demucsModelReady':manifest.get('modelReady',False),'cuda':device['cuda'],
        'deviceName':device['deviceName'],'deviceMessage':'NVIDIA独显当前可见' if device['cuda'] else '当前使用CPU；未检测到可用NVIDIA独显',
        'models':[{'id':'ffmpeg','name':'FFmpeg · 剪辑 / 提轨 / 导出','operation':'extract','modelId':'本机媒体工具','device':'cpu','local':True,'ready':bool(binary('ffmpeg'))},
        {'id':'htdemucs','name':'Demucs · 人声/其余声音','operation':'extract','device':'cuda' if device['cuda'] else 'cpu','modelId':manifest.get('model'),'local':True,'ready':manifest.get('modelReady',False)},*engines]}


def public_local_job(job):
    result={k:v for k,v in job.items() if k not in {'payload','requestHash'}}
    payload=job.get('payload',{})
    result['inputs']=[{'assetId':aid,'label':label} for aid,label in [(payload.get('sourceAssetId') or payload.get('assetId'),'原素材'),(payload.get('referenceAssetId'),'目标参考')] if aid]
    result['spokenText']=job.get('targetText') or payload.get('text')
    result['shotId']=payload.get('shotId')
    return result
