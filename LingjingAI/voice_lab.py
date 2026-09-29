"""Workspace-scoped audition drafts and previews. Adoption is an explicit separate action."""
import copy,hashlib,json,math,os,pathlib,re,subprocess,threading,time,uuid
from workflow import load,write,timestamp,WorkflowError
from editing import resolve_media
from media_probe import probe
PROGRESS_STAGES={'queued':'等待本机任务','loading':'加载模型','generating':'合成试听','encoding':'保存音频','complete':'可试听，尚未存入资源库','failed':'生成失败','cancelled':'已取消'}
STATUS_PHASES={'queued':'queued','succeeded':'complete','failed':'failed','cancelled':'cancelled'}
def progress_state(phase):
 return {'phase':phase,'indeterminate':phase in {'queued','loading','generating','encoding'},'percent':100 if phase=='complete' else None}
class VoiceLab:
 def __init__(self,root):self.root=pathlib.Path(root);self.lock=threading.RLock();self.processes={}
 def path(self,w):return w.data/'voice-lab.json'
 def read(self,w):return load(self.path(w),{'schemaVersion':1,'draftRevision':0,'draft':{},'takes':[]})
 def engines(self):
  design=load(self.root/'data/voice-design-deployment.json',{});base=load(self.root/'data/local-speech-deployment.json',{}).get('tts',{})
  rows=[]
  for mode,m,label in [('design',design,'文字设计声线 · Qwen 1.7B'),('reference',base,'固定声线读台词 · Qwen 0.6B')]:
   valid=bool(m.get('ready') and pathlib.Path(m.get('modelPath','__missing__'),'model.safetensors').is_file() and pathlib.Path(m.get('python','__missing__')).is_file() and (self.root/'tools/voice_lab_worker.py').is_file())
   rows.append({'mode':mode,'name':label,'ready':valid,'modelId':m.get('modelId'),'message':m.get('message') or '等待下载及本机推理验证','devices':m.get('validatedDevices',[]),'deployment':m})
  return rows
 def public(self,row):
  result={k:copy.deepcopy(v) for k,v in row.items() if k not in {'workerRequest','requestHash','path','progressRunId'}}
  result.setdefault('progress',progress_state(STATUS_PHASES.get(row['status'],'loading')))
  result.setdefault('startedAt',None)
  result.setdefault('stage',PROGRESS_STAGES[result['progress']['phase']])
  return result
 def listing(self,w):
  with self.lock:d=self.read(w)
  profiles=[]
  if w.id=='cangtou-film-v1-0':profiles=load(self.root/'preproduction/voice_redesign_v9/voice_profiles.json',{}).get('characters',[])
  if not profiles:
   for c in load(w.data/'direction.json',{}).get('characters',[]):profiles.append({'id':c['id'],'characterId':c.get('characterId'),'name':c['name'],'identityPrompt':c.get('voiceDesign',''),'auditions':[{'shotId':a.get('shotId'),'text':a.get('text',''),'performancePrompt':a.get('intention','')} for a in c.get('auditionCases',[])]})
  for profile in profiles:
   for image in profile.get('imageAssets',[]):
    p=pathlib.Path(image.get('path','')).resolve()
    if p.is_relative_to(w.media.resolve()) and p.is_file():image['mediaUrl']='/media/'+p.relative_to(w.media.resolve()).as_posix()
  return {'draftRevision':d['draftRevision'],'draft':d['draft'],'profiles':profiles,'takes':[self.public(t) for t in reversed(d['takes'])],'engines':[{k:v for k,v in e.items() if k!='deployment'} for e in self.engines()]}
 def clean_draft(self,body):
  result={}
  for key,limit in [('description',2500),('performance',1500),('text',200),('name',120),('voiceSlotId',100),('characterId',100),('referenceTakeId',80),('referenceAssetId',100),('referenceText',600)]:
   value=body.get(key,'')
   if not isinstance(value,str) or len(value)>limit:raise WorkflowError(f'{key} 内容过长或类型无效')
   result[key]=value.strip()
  result['mode']=body.get('mode','design');result['device']=body.get('device','cuda')
  if result['mode'] not in {'design','reference'} or result['device'] not in {'cpu','cuda'}:raise WorkflowError('模式或设备无效')
  for key,default,low,high in [('seed',1234,0,2147483647),('temperature',.9,.3,1.3)]:
   value=body.get(key,default)
   if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high or key=='seed' and int(value)!=value:raise WorkflowError(key+' 超出范围')
   result[key]=value
  return result
 def save_draft(self,w,body):
  draft=self.clean_draft(body.get('draft',{}))
  with self.lock:
   d=self.read(w)
   if body.get('revision')!=d['draftRevision']:raise WorkflowError('调试配置已被另一窗口修改，请刷新后合并。','REVISION_CONFLICT',409)
   d.update(draft=draft,draftRevision=d['draftRevision']+1);write(self.path(w),d);return {'draft':draft,'draftRevision':d['draftRevision']}
 def prepare(self,w,body):
  rid=body.get('requestId','')
  if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',rid):raise WorkflowError('缺少有效试听请求ID')
  spec=self.clean_draft(body);digest=hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()
  with self.lock:
   d=self.read(w);old=next((t for t in d['takes'] if t['requestId']==rid),None)
   if old:
    if old['requestHash']!=digest:raise WorkflowError('相同请求ID不能更换试听参数','IDEMPOTENCY_CONFLICT',409)
    return self.public(old),False
   if not spec['text']:raise WorkflowError('请填写试听台词')
   engine=next(e for e in self.engines() if e['mode']==spec['mode'])
   if not engine['ready'] or spec['device'] not in engine['devices']:raise WorkflowError('所选模型/设备尚未通过本机验证','LOCAL_ENGINE_NOT_READY',409)
   if sum(t['status'] in {'queued','running'} for t in d['takes'])>=3:raise WorkflowError('已有三个试听任务排队，请先听完再继续')
   dep=engine['deployment'];worker={**spec,'modelId':dep['modelId'],'modelPath':dep['modelPath'],'modelRevision':dep.get('revision'),'blockNetwork':True}
   if spec['mode']=='design' and not spec['description']:raise WorkflowError('请填写固定声线描述')
   if spec['mode']=='reference':
    if spec['performance']:raise WorkflowError('固定声线模式不支持自由表演指令；请清空表演栏，或改用文字设计模式。')
    if spec['referenceTakeId']:
     ref=next((t for t in d['takes'] if t['id']==spec['referenceTakeId'] and t['status']=='succeeded'),None)
     if not ref:raise WorkflowError('请选当前项目已完成的试听')
     path=pathlib.Path(ref['path']).resolve();reference_text=spec['referenceText'] or ref['spec']['text']
     if not path.is_file() or not path.is_relative_to(w.media.resolve()):raise WorkflowError('试听文件不存在或不属于本项目')
     info=probe(path,False)
    else:
     ref=resolve_media(w,spec['referenceAssetId'],{'audio'});path=pathlib.Path(ref['path']);reference_text=spec['referenceText'] or ref.get('spokenText','');info=ref
    if info.get('duration',0)>30.05:raise WorkflowError('固定声线样本需在30秒以内，请用短句重新设计')
    worker.update(referencePath=str(path),referenceText=reference_text)
   row={'id':uuid.uuid4().hex,'workspaceId':w.id,'requestId':rid,'requestHash':digest,'status':'queued','stage':PROGRESS_STAGES['queued'],'progress':progress_state('queued'),'startedAt':None,'spec':spec,'workerRequest':worker,'createdAt':timestamp(),'updatedAt':timestamp(),'adoptions':[]}
   d['takes'].append(row);write(self.path(w),d);return self.public(row),True
 def update(self,w,tid,**changes):
  with self.lock:
   if changes.get('status') in STATUS_PHASES:
    phase=STATUS_PHASES[changes['status']];changes.update(progress=progress_state(phase));changes.setdefault('stage',PROGRESS_STAGES[phase])
   d=self.read(w);t=next(t for t in d['takes'] if t['id']==tid);t.update(**changes,updatedAt=timestamp());write(self.path(w),d);return copy.deepcopy(t)
 def collect_progress(self,w,tid,path,run_id):
  # Only a matching execution may advance a live take. Partial/corrupt telemetry
  # never fails synthesis, and terminal state is authoritative under this lock.
  try:report=json.loads(path.read_text(encoding='utf-8-sig'))
  except (OSError,ValueError,UnicodeError):return
  phases=('queued','loading','generating','encoding')
  if not isinstance(report,dict) or report.get('runId')!=run_id or report.get('phase') not in phases[1:]:return
  with self.lock:
   t=next(t for t in self.read(w)['takes'] if t['id']==tid)
   if t['status']!='running' or t.get('progressRunId')!=run_id:return
   previous=t.get('progress',{}).get('phase','queued');phase=report['phase']
   if previous not in phases or phases.index(phase)<=phases.index(previous):return
   self.update(w,tid,stage=PROGRESS_STAGES[phase],progress=progress_state(phase))
 def execute(self,w,tid):
  with self.lock:
   t=next(t for t in self.read(w)['takes'] if t['id']==tid)
   if t['status']!='queued':return
   run_id=uuid.uuid4().hex
   self.update(w,tid,status='running',stage='准备启动试听',startedAt=timestamp(),progressRunId=run_id,progress=progress_state('queued'))
  try:
   dep=next(e['deployment'] for e in self.engines() if e['mode']==t['spec']['mode']);directory=w.media/'voice-lab'/tid;directory.mkdir(parents=True,exist_ok=True)
   req={**t['workerRequest'],'outputDir':str(directory),'progressRunId':run_id};request=directory/'request.json';result=directory/'result.json';progress=directory/'progress.json';write(request,req)
   with (directory/'worker.log').open('wb') as output:
    with self.lock:
     if next(t for t in self.read(w)['takes'] if t['id']==tid)['status']!='running':return
     process=subprocess.Popen([dep['python'],str(self.root/'tools/voice_lab_worker.py'),'--request',str(request),'--result',str(result)],cwd=self.root,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
     self.processes[(w.id,tid)]=process
    try:
     deadline=time.monotonic()+1800
     while True:
      self.collect_progress(w,tid,progress,run_id)
      remaining=deadline-time.monotonic()
      if remaining<=0:
       process.kill();process.wait();raise WorkflowError('本机试听超时，已停止；可缩短台词再试。')
      try:process.wait(timeout=min(.5,remaining));break
      except subprocess.TimeoutExpired:pass
     self.collect_progress(w,tid,progress,run_id)
    finally:
     with self.lock:self.processes.pop((w.id,tid),None)
   with self.lock:
    if next(t for t in self.read(w)['takes'] if t['id']==tid)['status']!='running':return
   report=load(result,{})
   if process.returncode or report.get('error'):raise WorkflowError(report.get('error') or '本机试听失败，请查看任务日志')
   path=pathlib.Path(report['path']).resolve()
   if not path.is_file() or not path.is_relative_to(directory.resolve()):raise WorkflowError('模型没有返回本任务目录内的音频')
   info=probe(path,False)
   if info.get('probeStatus')!='verified' or info.get('duration',0)<=0:raise WorkflowError('生成音频无法解码')
   with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
   with self.lock:
    if next(t for t in self.read(w)['takes'] if t['id']==tid)['status']!='running':return
    self.update(w,tid,status='succeeded',stage='可试听，尚未存入资源库',path=str(path),mediaUrl='/media/'+path.relative_to(w.media).as_posix(),duration=info['duration'],sha256=sha,metadata=report['metadata'])
  except Exception as e:
   with self.lock:
    if next(t for t in self.read(w)['takes'] if t['id']==tid)['status']=='running':self.update(w,tid,status='failed',stage='生成失败',error=str(e)[-1800:])
 def cancel(self,w,body):
  with self.lock:
   d=self.read(w);t=next((t for t in d['takes'] if t['id']==body.get('takeId')),None)
   if not t:raise WorkflowError('本项目找不到这个试听')
   if t['status'] not in {'queued','running'}:return self.public(t)
   t.update(status='cancelled',stage='已取消',progress=progress_state('cancelled'),updatedAt=timestamp());write(self.path(w),d)
   process=self.processes.get((w.id,t['id']))
   if process and process.poll() is None:process.terminate()
   return self.public(t)
 def adopt(self,w,body,library=None):
  if body.get('confirmed') is not True:raise WorkflowError('请试听后明确点击存入资源库')
  with self.lock:
   d=self.read(w);t=next((t for t in d['takes'] if t['id']==body.get('takeId') and t['status']=='succeeded'),None)
   if not t:raise WorkflowError('只能保存当前项目已成功的试听')
   role=body.get('role','voice_identity');target=body.get('target','project')
   if role not in {'voice_identity','dialogue_performance'} or target not in {'project','library'}:raise WorkflowError('保存用途或目标无效')
   path=pathlib.Path(t['path']).resolve()
   if not path.is_relative_to((w.media/'voice-lab'/t['id']).resolve()) or not path.is_file():raise WorkflowError('试听文件归属无效')
   name=str(body.get('name') or t['spec'].get('name') or '配音试听')[:120]
   spec={'path':str(path),'name':name,'role':role,'spokenText':t['spec']['text'],'description':t['spec']['description'],'characterId':t['spec'].get('characterId'),'voiceId':t['spec'].get('voiceSlotId'),'reviewStatus':'candidate','provenance':{'source':'voice-lab','takeId':t['id'],'modelId':t['metadata']['modelId'],'seed':t['spec']['seed'],'description':t['spec']['description'],'performance':t['spec']['performance'],'sourceWorkspaceId':w.id,'spec':copy.deepcopy(t['spec']),'metadata':copy.deepcopy(t['metadata'])}}
   if target=='project':asset=w.store.import_asset({**spec,'id':'voice-lab-'+t['id']+'-'+role})
   else:
    if library is None:raise WorkflowError('个人资料库不可用')
    asset=library.add({**spec,'personId':body.get('personId'),'notes':'配音调试台试听。固定声线：'+t['spec']['description']+'\n本句表演：'+t['spec']['performance']})
   record={'target':target,'assetId':asset['id'],'role':role,'name':asset['name']}
   if record not in t['adoptions']:t['adoptions'].append(record);write(self.path(w),d)
   return {'asset':asset,'target':target,'takeId':t['id']}
 def recover_interrupted(self,w):
  with self.lock:
   d=self.read(w);changed=False
   for t in d['takes']:
    if t['status'] in {'queued','running'}:t.update(status='failed',stage=PROGRESS_STAGES['failed'],progress=progress_state('failed'),updatedAt=timestamp(),error='服务重启，试听任务已停止；配置保留，可重新生成。');changed=True
   if changed:write(self.path(w),d)
