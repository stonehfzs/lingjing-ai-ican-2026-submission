"""Typed offline worker dispatch. Engines report ready only after real validation."""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import time

from workflow import load, write, WorkflowError

ENGINE_SPECS = {
    'transcribe': ('asr', 'local-speech-deployment.json', '字幕识别'),
    'tts': ('tts', 'local-speech-deployment.json', '本地参考配音'),
    'voice_convert': ('voice_convert', 'local-voice-deployment.json', '保留表演换声'),
    'speech_edit': ('speech_edit', 'local-voice-deployment.json', '局部声音精修'),
    'lip_sync': ('lip_sync', 'local-voice-deployment.json', '口型修复'),
}


def engine_catalog(root):
    root=Path(root);rows=[]
    for operation,(key,filename,label) in ENGINE_SPECS.items():
        doc=load(root/'data'/filename,{})
        item=copy.deepcopy(doc.get('engines',doc).get(key,{}))
        # Tolerate the workers' existing contract while keeping deployment status explicit.
        if not isinstance(item,dict):item={}
        python=Path(item.get('python') or '__missing__');worker=Path(item.get('worker') or '__missing__')
        valid_files=python.is_file() and worker.is_file()
        ready=item.get('ready') is True and valid_files
        rows.append({'id':key,'operation':operation,'name':label,'local':True,'ready':ready,
            'status':'ready' if ready else ('failed' if item.get('ready') and not valid_files else item.get('status','not_installed')),
            'message':item.get('error') or item.get('message') or ('已完成本机推理验证' if ready else '等待安装或推理验证'),
            'modelId':item.get('modelId') or item.get('model'), 'device':item.get('device','cuda'),
            'devices':item.get('validatedDevices',[item.get('device','cuda')]),
            'python':str(python) if valid_files else None,'worker':str(worker) if valid_files else None,
            'modelPath':item.get('modelPath'),'deployment':filename,'download':item.get('download',{}),
            'license':item.get('license'),'sourceUrl':item.get('sourceUrl')})
    design=load(root/'data/voice-design-deployment.json',{})
    if design:
        ready=bool(design.get('ready') and Path(design.get('modelPath','__missing__'),'model.safetensors').is_file() and Path(design.get('python','__missing__')).is_file() and (root/'tools/voice_lab_worker.py').is_file())
        rows.append({'id':'voice_design','operation':'voice_lab_design','uiRoute':'voice-lab','name':'文字设计声线 · Qwen3-TTS 1.7B','local':True,'ready':ready,'status':'ready' if ready else design.get('status','not_installed'),'message':design.get('message',''),'modelId':design.get('modelId'),'device':'cuda','devices':design.get('validatedDevices',[]),'license':design.get('license'),'sourceUrl':design.get('sourceUrl')})
    return rows


def require_engine(root,operation):
    row=next((r for r in engine_catalog(root) if r['operation']==operation),None)
    if not row or not row['ready']:
        raise WorkflowError((row or {}).get('message','此本地操作尚未实现。'),'LOCAL_ENGINE_NOT_READY',409)
    return row


def validate_spec(workspace,root,spec,resolve):
    operation=spec['operation'];engine=require_engine(root,operation)
    prepared={'operation':operation,'device':spec.get('device',engine['device'])}
    if prepared['device'] not in {'cpu','cuda'}:raise WorkflowError('设备无效。')
    if operation in {'transcribe','voice_convert','speech_edit'}:
        source=resolve(workspace,spec.get('assetId'),{'audio','video'} if operation=='transcribe' else {'audio'})
        if not any(s.get('codec_type')=='audio' for s in source['streams']):raise WorkflowError('所选媒体没有声音。')
        prepared.update(sourcePath=source['path'],sourceAssetId=source['id'],sourceDuration=source['duration'])
        if operation in {'voice_convert','speech_edit'} and source['duration']>30:raise WorkflowError('换声或局部精修请先裁成30秒以内的短句。')
        if operation=='voice_convert':prepared['text']=source.get('spokenText')
    if operation in {'voice_convert','tts'}:
        reference=resolve(workspace,spec.get('referenceAssetId'),{'audio'})
        limit=25 if operation=='voice_convert' else 30
        if reference['duration']>limit:raise WorkflowError(f'先选{limit}秒以内的干净单人参考录音。')
        prepared.update(referencePath=reference['path'],referenceAssetId=reference['id'])
    if operation=='tts':
        text=spec.get('text','').strip();reference_text=spec.get('referenceText','').strip()
        if not text or len(text)>600:raise WorkflowError('本地配音请输入1–600字，长段请按句拆分。')
        prepared.update(text=text,referenceText=reference_text,language=spec.get('language','zh'))
    if operation=='transcribe':prepared['language']=spec.get('language','zh')
    if operation=='speech_edit':
        source_text=spec.get('sourceText','').strip();instruction=spec.get('instruction','').strip()
        if not source_text or not instruction or len(instruction)>5000:raise WorkflowError('请校对原台词并提供明确的编辑范围。')
        prepared.update(sourceText=source_text,instruction=instruction)
    if operation=='lip_sync':
        video=resolve(workspace,spec.get('assetId'),{'video'});audio=resolve(workspace,spec.get('referenceAssetId'),{'audio'})
        if video['duration']>15 or audio['duration']>15:raise WorkflowError('口型修复请先裁成15秒以内的单人镜头。')
        if audio['duration']>video['duration']+.1:raise WorkflowError('对白长于视频，请先延长或重新剪辑视频，避免自动循环画面。')
        prepared.update(videoPath=video['path'],audioPath=audio['path'],sourceAssetId=video['id'],referenceAssetId=audio['id'])
    if spec.get('shotId'):
        project=load(workspace.project,{})
        shot=next((s for s in project.get('shots',[]) if s['id']==spec['shotId']),None)
        if not shot:raise WorkflowError('镜头不属于当前项目。')
        prepared['shotId']=shot['id']
        if spec.get('cueId'):
            cue=next((c for c in shot.get('audioPlan',{}).get('cues',[]) if (c.get('cueId') or c.get('id'))==spec['cueId']),None)
            if not cue:raise WorkflowError('台词不属于当前镜头。')
            if operation=='tts' and cue.get('text','').strip()!=prepared['text']:raise WorkflowError('台词已修改，请取消原句绑定或重新选择。')
            prepared.update(cueId=spec['cueId'],voiceId=cue.get('voiceId') or cue.get('voice_id'))
    elif spec.get('cueId'):raise WorkflowError('绑定台词须先选择镜头。')
    return prepared


def run_worker(workspace,root,spec,directory,progress):
    engine=require_engine(root,spec['operation']);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    request_path=directory/'worker-request.json';result_path=directory/'worker-result.json';log=directory/'worker.log'
    request={**spec,'outputDir':str(directory.resolve()),'modelPath':engine.get('modelPath'),'offline':True}
    write(request_path,request);progress(progress=10,stage='加载本地模型 · '+engine['name'])
    env={**os.environ,'PYTHONUTF8':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
        'HF_HOME':str(Path(root)/'.runtime/models/huggingface'),'TORCH_HOME':str(Path(root)/'.runtime/models/torch'),
        'PATH':str(Path(root)/'.runtime/ffmpeg')+os.pathsep+os.environ.get('PATH','')}
    start=time.monotonic()
    with log.open('wb') as output:
        process=subprocess.run([engine['python'],engine['worker'],'--request',str(request_path),'--result',str(result_path)],
            stdout=output,stderr=output,env=env,cwd=root,timeout=3600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result=load(result_path,{})
    if process.returncode or result.get('error'):
        detail=result.get('error') or log.read_text(encoding='utf8',errors='replace')[-2200:]
        raise WorkflowError('本机推理失败：'+str(detail),'LOCAL_INFERENCE_FAILED',500)
    outputs=[]
    for row in result.get('outputs',[]):
        path=Path(row.get('path','')).resolve()
        if not path.is_file() or not path.is_relative_to(directory.resolve()):raise WorkflowError('模型结果不在本任务目录内。')
        outputs.append((path,row.get('kind','audio'),row.get('name',engine['name']+'结果'),row.get('role','reference')))
    extra={'targetText':result.get('targetText'),'engine':engine['id'],'elapsedSeconds':round(time.monotonic()-start,2),'engineMetadata':result.get('metadata',{})}
    transcript=result.get('transcript')
    if transcript:
        if not isinstance(transcript,dict) or not isinstance(transcript.get('segments',[]),list):raise WorkflowError('识别结果格式无效。')
        transcript.update(sourceAssetId=spec.get('sourceAssetId'),reviewStatus='candidate')
        for s in transcript.get('segments',[]):
            if not isinstance(s,dict) or any(isinstance(s.get(k),bool) or not isinstance(s.get(k),(int,float)) or not math.isfinite(s[k]) for k in ('start','end')) or s['start']<0 or s['end']<=s['start']:
                raise WorkflowError('识别时间点无效。')
            if s['end']>spec.get('sourceDuration',86400)+.5 or not isinstance(s.get('text'),str):raise WorkflowError('识别结果超出源音频范围。')
        from editing import srt_time
        subtitle=directory/'transcript.srt'
        subtitle.write_text('\n\n'.join(f'{i+1}\n{srt_time(s["start"])} --> {srt_time(s["end"])}\n{s.get("text","")}' for i,s in enumerate(transcript.get('segments',[]))),encoding='utf-8-sig')
        write(directory/'transcript.json',transcript);extra.update(transcript=transcript,subtitlePath=str(subtitle))
    if not outputs and not transcript:raise WorkflowError('本地模型没有产生可用输出。')
    progress(progress=90,stage='登记结果与版本')
    return outputs,extra
