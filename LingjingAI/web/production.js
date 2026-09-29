/* One operational surface for generation, non-destructive editing and audio. */
'use strict';
function productionButton(label,className,action){
  const control=button(label,className,()=>{
    if(control.disabled||control.getAttribute('aria-busy')==='true')return;
    const disabled=control.disabled,result=action();
    if(!result?.then)return result;
    control.disabled=true;control.classList.add('production-pending');control.setAttribute('aria-busy','true');
    return Promise.resolve(result).catch(error=>toast(error.message,true)).finally(()=>{control.disabled=disabled;control.classList.remove('production-pending');control.removeAttribute('aria-busy');});
  });
  return control;
}
async function openProduction(page='generate',shotId=null){
  if(page==='edit'&&window.openProEditor)return openProEditor();
  if(state.switching||!state.project)return toast('请等待项目打开');
  closeReview();closeInspector();
  if(window.ProductionController){
    if(window.ProductionController.workspace===state.workspaceId)return window.ProductionController.navigate(page,shotId);
    if(!window.ProductionController.close())return;
  }
  const workspace=state.workspaceId,epoch=Creator.projectEpoch;
  const dialog=el('section','production-dialog production-surface'),head=el('header','production-heading'),nav=el('nav','production-nav'),body=el('div','production-body');
  dialog.id='productionSurface';dialog.tabIndex=-1;dialog.setAttribute('aria-label','制作工作区');
  let dirty=false,active=true,timer=null,timeline=null,media=[],localJobs=[],caps={},history=[],selectedClipId=null,clipClipboard=null,audioMode='transcribe',previewCleanup=()=>{},launching=false;
  const title=el('div');title.append(el('small','',state.project.title),el('h2','','制作工作台'));
  dialog.close=()=>{if(dirty&&!confirm('时间线尚未保存，确定离开并放弃这些修改？'))return false;active=false;clearTimeout(timer);previewCleanup();dialog.querySelectorAll('audio,video').forEach(m=>m.pause());dialog.remove();document.body.classList.remove('production-open');window.ProductionController=null;$$('[data-production]').forEach(b=>b.classList.remove('active'));return true;};
  const close=productionButton('返回分镜','quiet',()=>{if(dialog.close())chooseView('boards');});
  const reload=productionButton('刷新状态','secondary',async()=>{try{await refreshData();render();}catch(e){showFailure(e);}});
  head.append(title,nav,reload,close);dialog.append(head,body);document.querySelector('.work-area').append(dialog);document.body.classList.add('production-open');
  window.ProductionController={workspace,close:dialog.close,save:async()=>{try{if(page==='edit'){await saveEdit();edit();}else toast('请使用当前表单中的提交按钮；已完成任务自动保存。');}catch(e){toast(e.message,true);}},navigate:async(next,sid)=>{try{if(dirty&&page==='edit'&&next!=='edit')await saveEdit();}catch(e){toast(e.message,true);return;}page=next;if(sid)shotId=sid;try{await refreshData();render();}catch(e){showFailure(e);}}};
  dialog.addEventListener('keydown',event=>{
    if(page!=='edit'||!timeline||event.target.matches('input,textarea,select,[contenteditable=true]'))return;
    const modifier=event.ctrlKey||event.metaKey,key=event.key.toLowerCase();
    if(modifier&&key==='z'){event.preventDefault();if(history.length){timeline=history.pop();dirty=true;edit();dialog.focus({preventScroll:true});}return;}
    const field=timeline.clips.some(c=>c.id===selectedClipId)?'clips':'audio',index=timeline[field].findIndex(c=>c.id===selectedClipId);
    if(modifier&&key==='c'&&index>=0){event.preventDefault();clipClipboard={field,clip:structuredClone(timeline[field][index])};toast('片段已复制');}
    if(modifier&&key==='v'&&clipClipboard){event.preventDefault();change(()=>{const target=clipClipboard.field,clip={...clipClipboard.clip,id:uid()};if(target==='audio')clip.at+=clip.out-clip.in;timeline[target].splice(target===field&&index>=0?index+1:timeline[target].length,0,clip);selectedClipId=clip.id;});dialog.focus({preventScroll:true});}
  });
  const guardLeave=e=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',guardLeave);const originalClose=dialog.close;dialog.close=()=>{const closed=originalClose();if(closed)window.removeEventListener('beforeunload',guardLeave);return closed;};
  window.ProductionController.close=dialog.close;
  const check=()=>{if(state.workspaceId!==workspace||epoch!==Creator.projectEpoch)throw new Error('项目已切换，请重新打开制作工作台。');};
  async function call(path,method='GET',value){check();return api(path,{method,...(value!==undefined?{body:JSON.stringify(value)}:{})});}
  const uid=()=>crypto.randomUUID().replaceAll('-','');
  const requests=new Map();
  function requestId(path,payload){const signature=JSON.stringify([path,payload]);if(!requests.has(signature))requests.set(signature,'ui-'+uid());return requests.get(signature);}
  function labeled(label,control){const wrap=el('label','production-field');wrap.append(el('span','',label),control);return wrap;}
  function input(value,type='text'){const x=el('input');x.type=type;x.value=value??'';return x;}
  function select(items,value){const x=el('select');items.forEach(([v,n])=>{const o=el('option','',n);o.value=v;x.append(o);});if(value!==undefined)x.value=value;return x;}
  function note(text){return el('p','field-help',text);}
  function details(text,label='使用说明'){const wrap=el('details','production-help');wrap.append(el('summary','',label),note(text));return wrap;}
  const busyStates=new Set(['queued','pending','running','submitting','processing','downloading','installing','verifying']);
  function taskState(job,{label,prefix=''}={}){
    const busy=busyStates.has(job.status),row=el('div','production-task-state'+(busy?' is-busy':''));
    const names={queued:'排队中',pending:'等待中',running:'处理中',submitting:'提交中',processing:'生成中',downloading:'下载中',installing:'部署中',verifying:'验证中',succeeded:'已完成',completed:'已完成',failed:'失败',cancelled:'已取消',canceled:'已取消',not_installed:'未安装',blocked:'暂不可用',device_unavailable:'设备未就绪'};
    const stage=job.stage?String(job.stage):'',text=prefix+(label||names[job.status]||statuses[job.status]||job.status||'等待状态');
    if(busy){const spinner=el('i','production-spinner');spinner.setAttribute('aria-hidden','true');row.append(spinner);}
    row.append(el('span','',text+(stage?' · '+stage:'')));
    if(busy){
      const raw=job.progress,known=raw!==undefined&&raw!==null&&raw!==''&&Number.isFinite(Number(raw)),value=known?Math.max(0,Math.min(100,Number(raw))):null;
      const bar=el('div','production-progress'+(known?'':' is-indeterminate'));bar.setAttribute('role','progressbar');bar.setAttribute('aria-label',stage?stage+'阶段进度':text+'阶段进度');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');
      const fill=el('i');if(known){bar.setAttribute('aria-valuenow',String(value));fill.style.width=value+'%';row.append(el('small','',`阶段 ${Math.round(value)}%`));}bar.append(fill);row.append(bar);
    }
    return row;
  }
  function preview(a,parent){if(!a?.mediaUrl)return;const kind=a.mediaType||a.kind;const player=el(kind==='image'?'img':kind==='video'?'video':'audio');player.src=safeUrl(a.mediaUrl);if(kind!=='image'){player.controls=true;player.preload='metadata';}else{player.alt=a.name;player.loading='lazy';}parent.append(player);}
  let fetchWarnings=[];
  async function refreshData(){
    const docs=await Promise.allSettled([call('/api/media'),call('/api/local-jobs'),call('/api/local-capabilities')]);
    if(docs.every(d=>d.status==='rejected'))throw docs[0].reason;
    fetchWarnings=[];const names=['素材库','任务记录','本地模型状态'];docs.forEach((d,i)=>{if(d.status==='rejected')fetchWarnings.push(names[i]+'暂时无法读取');});
    if(docs[0].status==='fulfilled')media=docs[0].value.assets||[];else if(!media.length)media=state.assets||[];
    if(docs[1].status==='fulfilled')localJobs=docs[1].value.jobs||[];
    if(docs[2].status==='fulfilled')caps=docs[2].value;
  }
  function showFailure(e){previewCleanup();clearTimeout(timer);tabs();body.replaceChildren();const panel=el('div','service-recovery');panel.append(el('h3','','服务未连接'),note('双击桌面“灵镜AI”启动服务。'),el('p','error-message',e.message),productionButton('重新连接','primary',async()=>{try{await refreshData();render();}catch(error){showFailure(error);}}));body.append(panel);}
  function tabs(){nav.replaceChildren();[['guide','使用流程'],['models','本机模型']].forEach(([key,label])=>nav.append(productionButton(label,page===key?'active':'',async()=>{try{if(dirty&&page==='edit'&&key!=='edit')await saveEdit();page=key;render();}catch(e){toast(e.message,true);}})));$$('[data-production]').forEach(b=>b.classList.toggle('active',b.dataset.production===page));$$('.view-tabs [data-view]').forEach(b=>b.classList.remove('active'));}
  function render(){if(!active)return;check();previewCleanup();clearTimeout(timer);title.querySelector('h2').textContent=({guide:'制作流程',generate:'生成素材',audio:'声音工作室',edit:'剪辑成片',jobs:'任务与结果',models:'本机模型'})[page]||'制作工作台';tabs();body.replaceChildren();if(fetchWarnings.length)body.append(el('p','partial-warning',fetchWarnings.join('；')+'。其他功能仍可使用。'));if(page==='generate')generate();if(page==='edit'){openProEditor();return;}if(page==='audio')audioTools();if(page==='guide')guide();if(page==='jobs')jobs();if(page==='models')modelManager();}
  function guide(){
    body.append(el('h3','','从剧本到成片'),note('每个项目都有自己的剧本、素材、生成记录和剪辑。先完成一个场景，再扩展全片。'));
    const steps=[['1','建立项目','左上角项目名 → 新建项目，导入或粘贴剧本。让 Codex 用镜序 skill 分析角色、场次与镜头。','打开剧本',()=>{if(dialog.close())chooseView('script');}],
      ['2','准备参考与声线','导入人物、场景、道具。导演工作簿中先听选角色声音，再按镜头连接参考。','查看角色',()=>{if(dialog.close())openDirection();}],
      ['3','制作对白与视频','在生成素材中选镜头、模型、参数与引用。先预演查看输入，再点击提交。画内对白须核对口型。','生成素材',()=>{page='generate';render();}],
      ['4','试听与审片','生成结果是候选。检查台词、动作、身份和前后镜连续性；满意的片段再放到时间线。','查看结果',()=>{page='jobs';render();}],
      ['5','剪辑与声音','裁入点/出点，分割、复制、调整顺序；对白、环境与音乐分别设开始时间和音量。','打开剪辑',()=>{page='edit';render();}],
      ['6','导出成片','保存时间线后本机渲染 MP4，同时导出 SRT 字幕。原始素材始终保留。','剪辑导出',()=>{page='edit';render();}]];
    steps.forEach(([n,h,t,label,fn])=>{const row=el('section','production-step');row.append(el('span','step-number',n),el('h4','',h),el('p','',t),productionButton(label,'secondary',fn));body.append(row);});
    body.append(note('生成与剪辑是两件事：参考图不能直接当成已经拍好的视频。还没有生成视频时，可以先导入现有视频完成剪辑。'));
  }
  function parameterFields(container,model,values){container.replaceChildren();Object.entries(model?.params||{}).forEach(([key,spec])=>{
    if(['voice_id','model_name','reference_audios','reference_videos','reference_images'].includes(key))return;
    if(values[key]===undefined&&spec.default!==undefined)values[key]=String(spec.default);
    let control;
    if(spec.options){control=select([...(spec.optional?[['','自动']]:[]),...spec.options.map(v=>[String(v),({'true':'开启','false':'关闭','reference':'多参考素材','first-last-frame':'首尾帧','video-extension':'续写视频'})[String(v)]||String(v)])],String(values[key]??''));}
    else if(spec.type==='slider'){control=input(values[key]??spec.min,'number');control.min=spec.min;control.max=spec.max;control.step=spec.step||.1;}
    else return;
    control.onchange=()=>values[key]=control.value;container.append(labeled(spec.label||key,control));
  });}
  function generate(){
    const connection=el('div','generation-connection');connection.append(el('strong','',state.designOnline?'MiniMax Design · 已连接':'MiniMax Design · 未连接'),note('Design 云端生成 · 按平台计费'));
    const connect=productionButton(state.designOnline?'刷新云端模型':'打开MiniMax Design','secondary',async()=>{connect.disabled=true;try{if(!state.designOnline){await call('/api/services/design/start','POST',{});toast('已打开Design。完成登录后点击刷新连接。');connect.textContent='刷新连接';}const status=await call('/api/status');setConnection(status.online);if(status.online){state.models=await call('/api/models/refresh','POST',{});generateRefresh();}else connect.textContent='刷新连接';}catch(e){toast(e.message,true);}finally{connect.disabled=false;}});
    function generateRefresh(){if(active&&page==='generate')render();}connection.append(connect,productionButton('使用本地配音','quiet',()=>{page='audio';audioMode='tts';render();}));body.append(connection);
    const type=select([['video','视频镜头'],['image','分镜图片'],['speech','固定音色配音'],['reference','参考声音配音'],['design','设计新声线'],['clone','录音克隆声线']],'video');
    const mode=el('div','generation-mode');mode.append(labeled('我要生成',type));const panel=el('div','generation-panel');body.append(mode,panel);
    const status=el('div','generation-status');body.append(status);let statusVersion=0;
    function submitted(result,version=statusVersion){
      if(version!==statusVersion||!active||page!=='generate'||!status.isConnected)return;
      const job=result.jobs?.[0]||result;clearTimeout(timer);
      status.replaceChildren(taskState(job,{label:job.status?undefined:'已提交'}),productionButton('查看任务','secondary',async()=>{await refreshData();await reloadProjectData();page='jobs';render();}));
      if(job.error)status.append(el('p','error-message',job.error));
      if(job.id&&busyStates.has(job.status))timer=setTimeout(async()=>{if(version!==statusVersion||!active||page!=='generate'||!status.isConnected)return;try{const latest=await call('/api/jobs/'+encodeURIComponent(job.id));submitted(latest,version);}catch(e){if(version!==statusVersion||!active||page!=='generate'||!status.isConnected)return;status.replaceChildren(el('p','error-message','状态读取失败：'+e.message),productionButton('重新连接','secondary',async()=>submitted(await call('/api/jobs/'+encodeURIComponent(job.id)),version)));}},3000);
    }
    async function submit(path,payload,buttonElement){
      const version=++statusVersion;buttonElement.disabled=true;status.replaceChildren(taskState({status:'submitting'}));
      try{const job=await call(path,'POST',{...payload,confirmed:true,requestId:requestId(path,payload)});submitted(job,version);await reloadProjectData();}
      catch(e){status.replaceChildren(el('p','error-message',e.message),note('若提示提交状态不确定，请在任务与结果中核对，勿重复提交。'));}finally{buttonElement.disabled=false;}
    }
    function build(){statusVersion++;clearTimeout(timer);panel.replaceChildren();status.replaceChildren();let values={},prepared=null;const kind=type.value;
      if(['video','image'].includes(kind)){
        const defaultShot=shotId||(videoNode(state.shot)?.type==='video'?state.shot:allShots().find(s=>videoNode(s.id)?.type==='video')?.id)||allShots()[0]?.id;
        const shotSelect=select(allShots().map(s=>[s.id,`${s.id} · ${s.title}${videoNode(s.id)?.type==='reuse'?'（复用）':videoNode(s.id)?.type==='edit'?'（后期）':''}`]),defaultShot);
        if(!allShots().length){panel.append(note('先从剧本创建一个镜头，或回到分镜点击新建镜头。'));return;}
        const models=(state.models[kind]||[]).filter(m=>kind!=='video'||m.referenceCapabilities?.adapterSupported),modelSelect=select(models.map(m=>[modelId(m),m.name||modelId(m)]));
        const prompt=el('textarea');prompt.rows=7;const params=el('div','production-params'),refs=el('div','generation-references');
        const recheck=el('input');recheck.type='checkbox';const recheckLabel=el('label','production-check');recheckLabel.append(recheck,document.createTextNode('我已复核本镜的参考用途与当前提示词'));
        const preflight=el('div','generation-preflight');const make=productionButton('提交生成 · 消耗积分','primary',async()=>{
          if(!prepared)return;const version=++statusVersion,buttonElement=make;buttonElement.disabled=true;status.replaceChildren(taskState({status:'submitting'}));
          try{const result=kind==='video'?await call('/api/workflow/run','POST',{nodeIds:[prepared.nodeId],requestId:prepared.requestId,confirmed:true}):await call('/api/jobs','POST',{...prepared,requestId:prepared.requestId,confirmed:true});submitted(result,version);await reloadProjectData();}
          catch(e){status.replaceChildren(el('p','error-message',e.message));}finally{buttonElement.disabled=false;}
        });make.disabled=true;
        function invalidate(){prepared=null;make.disabled=true;preflight.replaceChildren();}
        function loadShot(){const s=fullShot(shotSelect.value);shotId=s.id;prompt.value=kind==='video'?s.videoPrompt||s.action:s.prompt||s.action;
          if(models.some(m=>modelId(m)===s.modelId)&&kind==='video'){modelSelect.value=s.modelId;values={...s.params};}else{modelSelect.selectedIndex=0;values={};}
          parameterFields(params,models.find(m=>modelId(m)===modelSelect.value),values);refs.replaceChildren();shotAssets(s).filter(a=>a.mediaType==='image').forEach(a=>{const item=el('div');preview(a,item);item.append(el('small','',a.name));refs.append(item);});invalidate();recheck.checked=false;}
        shotSelect.onchange=loadShot;modelSelect.onchange=()=>{values={};parameterFields(params,models.find(m=>modelId(m)===modelSelect.value),values);invalidate();};prompt.oninput=invalidate;params.addEventListener('change',invalidate);
        const checkButton=productionButton('保存并检查生成输入','secondary',async()=>{
          checkButton.disabled=true;invalidate();try{
            const s=fullShot(shotSelect.value);
            if(kind==='video'){
              const updated=await patchShot(s.id,{modelId:modelSelect.value,params:values,videoPrompt:prompt.value});if(!updated)throw new Error('镜头保存失败');
              if(recheck.checked)await call('/api/direction/recheck','POST',{shotId:s.id,projectRevision:state.project.revision,reviewed:true});
              const node=videoNode(s.id);if(!node||node.type!=='video')throw new Error('本镜是复用或后期节点，请先完成它的来源镜头。');
              const result=await call('/api/workflow/compile','POST',{nodeIds:[node.id]});const task=result.tasks.find(t=>t.nodeId===node.id);
              preflight.append(note(`${task?.imagePaths?.length||0} 张图片 / ${task?.audioPaths?.length||0} 条声音 / ${task?.videoPaths?.length||0} 条视频参考`));
              if(result.errors.length||!task?.ready){result.errors.forEach(e=>preflight.append(el('p','error-message',typeof e==='string'?e:e.message)));throw new Error('输入尚未通过检查，请处理以上缺口。');}
              prepared={nodeId:node.id,requestId:'ui-video-'+uid()};
            }else{
              const current=await call('/api/project');const s2=current.shots.find(x=>x.id===s.id);const bound=editableBindings(s2).filter(b=>b.enabled!==false&&b.usage==='model_reference');
              const imagePaths=bound.map(b=>assetBy(b.assetId)).filter(a=>a?.mediaType==='image'&&a.reviewStatus==='ready').map(a=>a.path);
              prepared={requestId:'ui-image-'+uid(),shotId:s.id,kind:'image',modelId:modelSelect.value,prompt:prompt.value,params:values,imagePaths};
              const result=await call('/api/generation/preflight','POST',prepared);preflight.append(note(`已检查 ${result.referenceCount} 张图片参考。`));
            }
            preflight.append(el('p','coverage-ok','检查通过'));make.disabled=false;
          }catch(e){preflight.append(el('p','error-message',e.message));}finally{checkButton.disabled=false;}
        });
        const selectors=el('div','generation-selectors'),content=el('div','generation-content'),referencePanel=el('aside','generation-reference-panel'),actions=el('footer','generation-runbar');
        selectors.append(labeled('镜头',shotSelect),labeled('模型',modelSelect));
        referencePanel.append(el('h4','','参考素材'),refs,productionButton('参考连接','secondary',()=>{dialog.close();editShot(shotSelect.value,'audio');}));
        content.append(labeled('提示词',prompt),referencePanel);actions.append(checkButton,make);
        panel.append(selectors,params,content,recheckLabel,preflight,actions);loadShot();
      }else{
        const text=el('textarea');text.rows=4;text.placeholder='填写实际需要说出的台词';const name=input('新的声音素材');
        panel.append(labeled('素材名称',name));
        let voiceControl=null;let soundContext=()=>({});
        if(kind==='speech'||kind==='reference'){
          const target=select([['','独立素材（不关联镜头）'],...allShots().map(s=>[s.id,s.id+' · '+s.title])],shotId||'');
          const cueSelect=el('select');let cueList=[];
          soundContext=()=>{const cue=cueList.find(c=>(c.cueId||c.id)===cueSelect.value);return {...(target.value?{shotId:target.value}:{}),...(cue?{cueId:cue.cueId||cue.id,voiceId:cue.voiceId||cue.voice_id}:{})};};
          cueSelect.onchange=()=>{const cue=cueList.find(c=>(c.cueId||c.id)===cueSelect.value);if(cue){text.value=cue.text;name.value=target.value+' · '+(cue.cueId||cue.id);if(voiceControl){const voice=state.assets.find(a=>a.role==='voice_identity'&&a.voiceId===(cue.voiceId||cue.voice_id)&&a.vendorVoiceId&&a.reviewStatus==='ready')||state.assets.find(a=>a.role==='voice_identity'&&a.voiceId===(cue.voiceId||cue.voice_id)&&a.vendorVoiceId);if(voice)voiceControl.value=voice.id;}}};
          target.onchange=()=>{const shot=fullShot(target.value);cueList=shot?.audioPlan?.cues||[];cueSelect.replaceChildren();const custom=el('option','','自定义台词（不绑定原句）');custom.value='';cueSelect.append(custom);cueList.filter(c=>c.text&&c.kind!=='replay').forEach(c=>{const o=el('option','',(c.cueId||c.id)+' · '+c.text);o.value=c.cueId||c.id;cueSelect.append(o);});if(cueSelect.options.length>1)cueSelect.selectedIndex=1;cueSelect.onchange();};
          text.oninput=()=>{cueSelect.value='';};panel.append(labeled('关联镜头',target),labeled('剧本台词',cueSelect));target.onchange();
          queueMicrotask(()=>{if(active)cueSelect.onchange();});
        }
        if(kind==='design'){
          const description=el('textarea');description.rows=4;description.placeholder='年龄感、音区、质地、自然说话习惯';panel.append(labeled('声线描述',description),labeled('试听台词',text),details('设计音色返回音色ID，试听为声线候选；正式对白需另行合成。','声线用途'));
          const b=productionButton('生成声线候选 · 消耗积分','primary',()=>submit('/api/audio/voice-design',{name:name.value,description:description.value,previewText:text.value},b));panel.append(b);
        }else if(kind==='clone'){
          const refs=select(media.filter(a=>a.mediaType==='audio').map(a=>[a.id,`${a.name} · ${Number(a.duration||0).toFixed(1)}秒`]));panel.append(labeled('参考录音',refs),labeled('试听台词',text),note('录音 10 秒–5 分钟 · ≤20 MB'));
          const b=productionButton('克隆为新声线 · 消耗积分','primary',()=>submit('/api/audio/voice-clone',{name:name.value,referenceAssetId:refs.value,previewText:text.value},b));panel.append(b);
        }else if(kind==='speech'){
          const voices=state.assets.filter(a=>a.role==='voice_identity'&&a.vendorVoiceId&&!['blocked','missing'].includes(a.reviewStatus));
          const voice=select(voices.map(a=>[a.id,a.name])),models=speechModels(),model=select(models.map(m=>[modelId(m),m.name]));const params=el('div','production-params');voiceControl=voice;
          model.onchange=()=>{values={};parameterFields(params,models.find(m=>modelId(m)===model.value),values);};model.onchange();
          panel.append(labeled('语音模型',model),labeled('角色声线',voice),labeled('台词',text),params,details('固定音色用于保持角色身份。可在导演工作簿选音，或通过设计、克隆创建声线。','声线选择'));
          const b=productionButton('生成配音 · 消耗积分','primary',()=>submit('/api/audio/speech',{...soundContext(),name:name.value,voiceAssetId:voice.value,text:text.value,modelId:model.value,params:values},b));b.disabled=!voices.length||!models.length;panel.append(b);
        }else{
          const model=state.models.speech?.find(m=>m.id==='seed-audio-1.0');const chosen=[];const mapping=note('尚未选择声音参考');const list=el('div','reference-audio-picker');media.filter(a=>a.mediaType==='audio').forEach(a=>{const check=el('input');check.type='checkbox';check.onchange=()=>{if(check.checked&&chosen.length>=3){check.checked=false;return toast('最多3条声音参考');}if(check.checked)chosen.push(a.id);else chosen.splice(chosen.indexOf(a.id),1);mapping.textContent=chosen.map((id,i)=>'@音频'+(i+1)+' = '+media.find(a=>a.id===id)?.name).join('；')||'尚未选择声音参考';};const label=el('label');label.append(check,document.createTextNode(a.name));list.append(label);});
          const direction=el('textarea');direction.rows=3;direction.placeholder='对谁说、想让对方做什么、情境与节奏；可写 @音频1 指定音色参考';const params=el('div','production-params');parameterFields(params,model,values);
          panel.append(labeled('模型',select([['seed-audio-1.0','Seed Audio 1.0 · 参考式配音']])),el('h4','','声音参考（按勾选顺序编号）'),list,mapping,labeled('表演说明',direction),labeled('台词',text),params,note('最多 3 条参考 · 每条 ≤30 秒 · 须同时填写台词'));
          const b=productionButton('生成参考配音 · 消耗积分','primary',()=>submit('/api/audio/reference-speech',{...soundContext(),name:name.value,modelId:'seed-audio-1.0',referenceAssetIds:chosen,direction:direction.value,text:text.value,params:values},b));b.disabled=!model;panel.append(b);
        }
      }
    }type.onchange=build;build();
  }
  async function launchLocal(spec){if(launching)throw new Error('本机任务正在提交，请稍候。');launching=true;const pending=taskState({status:'submitting'}),id=requestId('/api/local-jobs',spec);body.append(pending);try{const job=await call('/api/local-jobs','POST',{...spec,requestId:id});await refreshData();page='jobs';render();requests.delete(JSON.stringify(['/api/local-jobs',spec]));return job;}finally{launching=false;pending.remove();}}

  function audioTools(){
    const modes=select([['transcribe','识别字幕'],['tts','参考声线读新台词'],['voice_convert','保留表演换声'],['speech_edit','局部声音精修'],['lip_sync','修复口型'],['extract','提轨与人声分离']],audioMode);
    const toolbar=el('div','audio-workbench-toolbar');toolbar.append(labeled('处理',modes),productionButton('配音调试台','secondary',()=>{if(dialog.close())openVoiceLab();}));
    const panel=el('div','local-audio-panel');body.append(toolbar,panel);
    modes.onchange=()=>{audioMode=modes.value;panel.replaceChildren();if(modes.value==='extract')extraction(panel);else localAudioForm(panel,modes.value);};modes.onchange();
  }
  function extraction(container){
    container.append(note(`FFmpeg ${caps.ffmpeg?'已就绪':'不可用'} · Demucs ${caps.demucsModelReady?'已部署':'尚未就绪'}${caps.cuda?' · '+caps.deviceName:' · CPU'}`));
    const files=media.filter(a=>['audio','video'].includes(a.mediaType));const source=select(files.map(a=>[a.id,`${a.name} · ${a.mediaType==='video'?'视频':'声音'}`]));const viewer=el('div','audio-tool-preview');
    const start=input(0,'number'),end=input(0,'number');start.min=0;start.step=.1;end.step=.1;
    source.onchange=()=>{const a=files.find(a=>a.id===source.value);viewer.replaceChildren();preview(a,viewer);start.value=0;end.value=a?.duration||0;};
    const range=el('div','local-options');range.append(labeled('开始 · 秒',start),labeled('结束 · 秒',end));container.append(labeled('源素材',source),viewer,range);source.onchange();
    const extract=productionButton('提取完整音轨 · 本机处理','primary',async()=>{try{await launchLocal({operation:'extract_audio',assetId:source.value,in:Number(start.value),out:Number(end.value)});}catch(e){toast(e.message,true);}});
    const separate=productionButton('分离人声与其余声音 · Demucs','secondary',async()=>{try{await launchLocal({operation:'separate_vocals',assetId:source.value,in:Number(start.value),out:Number(end.value),device:caps.cuda?'cuda':'cpu'});}catch(e){toast(e.message,true);}});extract.disabled=!files.length;separate.disabled=!files.length||!caps.demucsModelReady;container.append(extract,separate,
      details('提取保留完整声音。Demucs 尝试分离人声与其余声音，影视对白可能有残留；不支持按演员拆轨。','提取与分离'),
      productionButton('刷新工具状态','quiet',async()=>{await refreshData();render();}));
  }
  function localAudioForm(container,operation){
    const engine=(caps.models||[]).find(m=>m.operation===operation);
    const label={transcribe:'识别字幕',tts:'本地生成配音',voice_convert:'保留表演换声',speech_edit:'生成编辑候选',lip_sync:'生成口型候选'}[operation];
    const status=el('div','engine-state');status.append(el('strong','',engine?.name||engine?.modelId||label),taskState({status:engine?.ready?'succeeded':engine?.status},{label:engine?.ready?'已就绪':undefined}));if(engine?.message){if(engine.ready)status.title=engine.message;else status.append(note(engine.message));}container.append(status);
    const options=el('div','local-options'),device=select((engine?.devices||[engine?.device||'cpu']).filter(d=>d!=='cuda'||caps.cuda).map(d=>[d,d==='cuda'?'NVIDIA GPU':'CPU']),engine?.device||'cpu');options.append(labeled('设备',device));
    const language=select([['zh','中文'],['auto','自动'],['en','English'],['ja','日本語'],['ko','한국어'],['fr','Français'],['de','Deutsch'],['es','Español'],['ru','Русский'],['it','Italiano'],['pt','Português']],'zh');if(['tts','transcribe'].includes(operation))options.append(labeled('语种',language));container.append(options);
    const form=el('div','local-form-grid'),left=el('div'),right=el('div','local-form-reference');
    const sourceKinds=operation==='lip_sync'?['video']:operation==='transcribe'?['video','audio']:['audio'];
    const files=media.filter(a=>sourceKinds.includes(a.mediaType)),sounds=media.filter(a=>a.mediaType==='audio');
    const source=select(files.map(a=>[a.id,`${a.name} · ${Number(a.duration||0).toFixed(1)}秒`])),reference=select(sounds.map(a=>[a.id,`${a.name} · ${Number(a.duration||0).toFixed(1)}秒`]));
    const sourcePreview=el('div','audio-source-preview'),refPreview=el('div','audio-source-preview');
    const text=el('textarea');text.rows=5;text.maxLength=600;text.placeholder='输入实际要说的台词';const sourceText=el('textarea');sourceText.rows=4;sourceText.placeholder='填写或校对源音频的实际文字';
    let soundContext=()=>({});
    if(operation==='tts'){
      const target=select([['','独立声音素材'],...allShots().map(s=>[s.id,s.id+' · '+s.title])],shotId||''),cue=select([['','自定义台词']]);let cues=[];
      target.onchange=()=>{cues=fullShot(target.value)?.audioPlan?.cues||[];cue.replaceChildren(new Option('自定义台词',''));cues.filter(c=>c.text&&c.kind!=='replay').forEach(c=>cue.append(new Option((c.cueId||c.id)+' · '+c.text,c.cueId||c.id)));if(cue.options.length>1)cue.selectedIndex=1;cue.onchange();};
      cue.onchange=()=>{const c=cues.find(c=>(c.cueId||c.id)===cue.value);if(c){text.value=c.text;const eligible=sounds.filter(a=>a.voiceId===(c.voiceId||c.voice_id)&&a.role==='voice_identity'&&!['blocked','missing'].includes(a.reviewStatus));const ref=eligible.find(a=>a.reviewStatus==='ready')||eligible[0];if(ref){reference.value=ref.id;reference.onchange?.();}}};
      text.oninput=()=>{cue.value='';};soundContext=()=>({...target.value?{shotId:target.value}:{},...cue.value?{cueId:cue.value}:{}});
      left.append(labeled('关联镜头',target),labeled('剧本台词',cue));queueMicrotask(()=>{if(active)target.onchange();});
    }
    source.onchange=()=>{const a=files.find(a=>a.id===source.value);sourcePreview.replaceChildren();preview(a,sourcePreview);sourceText.value=a?.spokenText||'';};
    reference.onchange=()=>{const a=sounds.find(a=>a.id===reference.value);refPreview.replaceChildren();preview(a,refPreview);if(operation==='tts')sourceText.value=a?.spokenText||'';};
    if(operation!=='tts')left.append(labeled(operation==='lip_sync'?'需要修口型的视频':'源声音或视频',source),sourcePreview);
    if(['tts','voice_convert','lip_sync'].includes(operation))right.append(labeled(operation==='lip_sync'?'驱动口型的对白':'目标声线参考',reference),refPreview);
    if(operation==='tts'){left.append(labeled('新台词（最多600字）',text));right.append(labeled('参考录音中的原话',sourceText),details('参考原话须与录音一致。生成新朗读，视频口型需另行处理。','参考要求'));}
    const instruction=el('textarea');instruction.rows=4;instruction.placeholder='编辑指令由选中范围编译；也可使用已验证的模型标签。';
    const targetPhrase=input(''),replacement=input('');
    if(operation==='speech_edit'){
      const editMode=select([['sub','替换词句'],['emo','调整情绪'],['rate','调整语速'],['pause','添加停顿']],'sub'),emotion=select([['calm','平静'],['happy','开心'],['angry','生气'],['sad','悲伤'],['afraid','害怕'],['melancholic','忧郁'],['surprised','惊讶']],'calm'),level=select([[1,'轻'],[2,'中'],[3,'强']],'2'),rate=input(1.1,'number');rate.min=.5;rate.max=2;rate.step=.05;
      const options=el('div');left.append(labeled('源台词（先校对）',sourceText),labeled('编辑方式',editMode),labeled('选中的原文词句（须唯一）',targetPhrase),options);
      const advanced=el('details');advanced.append(el('summary','','查看模型编辑指令'),labeled('实际编辑指令',instruction));right.append(details('仅编辑选中范围，输出新候选。情绪、语速与停顿需试听核对。','编辑范围'),advanced);
      const compile=()=>{const original=sourceText.value,phrase=targetPhrase.value;const escape=v=>v.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');if(!phrase||original.split(phrase).length!==2){instruction.value='';return;}const span=escape(phrase);let tag='';
        if(editMode.value==='sub')tag=`<sub targ="${escape(replacement.value)}">${span}</sub>`;
        if(editMode.value==='emo')tag=`<emo type="${emotion.value}" level="${level.value}">${span}</emo>`;
        if(editMode.value==='rate')tag=`<rate, factor=${Number(rate.value)}>${span}</rate>`;
        if(editMode.value==='pause')tag=span+`<pause act="ins" level="${level.value}"/>`;
        instruction.value=escape(original).replace(span,tag);
      };
      const fields=()=>{options.replaceChildren();if(editMode.value==='sub')options.append(labeled('替换为',replacement));if(editMode.value==='emo')options.append(labeled('情绪',emotion));if(['emo','pause'].includes(editMode.value))options.append(labeled(editMode.value==='pause'?'在选中词句之后停顿':'情绪强度',level));if(editMode.value==='rate')options.append(labeled('语速倍数（0.5–2）',rate));compile();};
      editMode.onchange=fields;[sourceText,targetPhrase,replacement,emotion,level,rate].forEach(control=>control.addEventListener('input',compile));fields();
    }
    if(operation==='voice_convert')right.append(note('保留原台词与表演，替换声线。'));
    if(operation==='transcribe')right.append(details('识别稿独立保存，校对后可下载 SRT 或加入剪辑字幕。','识别结果'));
    if(operation==='lip_sync')right.append(note('单人正面视频 ≤15 秒 · 对白不长于视频'),details('输出按对白时长生成。近景可能损失纹理，请审查嘴部、身份和遮挡。','口型结果'));
    source.onchange();reference.onchange();form.append(left,right);container.append(form);
    const submit=productionButton(label+' · 本机处理','primary',async()=>{submit.disabled=true;try{
      const payload={operation,device:device.value||engine?.device||'cpu',assetId:source.value,referenceAssetId:reference.value};
      if(operation==='tts')Object.assign(payload,{text:text.value,referenceText:sourceText.value,language:language.value},soundContext());
      if(operation==='transcribe')payload.language=language.value;
      if(operation==='speech_edit')Object.assign(payload,{sourceText:sourceText.value,instruction:instruction.value});
      await launchLocal(payload);
    }catch(e){toast(e.message,true);submit.disabled=false;}});submit.disabled=!engine?.ready||(operation==='tts'?!sounds.length:!files.length);
    container.append(submit,productionButton('查看模型状态','secondary',()=>{page='models';render();}));
  }
  function modelManager(){
    body.append(el('h3','','本机模型'));if(caps.deviceMessage)body.append(note(caps.deviceMessage));
    const table=el('div','model-registry');(caps.models||[]).forEach(m=>{
      const row=el('section','model-registry-row'),info=el('div');info.append(el('h4','',m.name),note((m.device||'cpu').toUpperCase()));
      const infoText=[m.modelId||m.id,m.message].filter(Boolean).join(' · ');if(infoText)info.append(details(infoText,'详情'));
      const downloading=!m.ready&&['installing','downloading'].includes(m.status)&&Number(m.download?.totalBytes)>0,d=m.download;
      const label=m.ready?'已实测':({failed:'需处理',not_installed:'未安装',blocked:'暂不可用',device_unavailable:'设备未就绪'})[m.status];
      row.append(info,taskState({status:m.ready?'succeeded':m.status,stage:downloading?'下载':undefined,progress:downloading?Math.min(100,Math.max(0,Number(d.downloadedBytes||0)/Number(d.totalBytes)*100)):undefined},{label}));
      if(downloading)info.append(note(`${(Number(d.downloadedBytes||0)/1e9).toFixed(2)} / ${(Number(d.totalBytes)/1e9).toFixed(2)} GB`));
      if(!m.ready&&['failed','blocked','device_unavailable'].includes(m.status)&&m.message)info.append(el('p','error-message',m.message));
      if(m.ready)row.append(productionButton('打开','secondary',()=>{if(m.uiRoute==='voice-lab'){if(dialog.close())openVoiceLab();return;}audioMode=m.operation||'extract';page='audio';render();}));table.append(row);
    });body.append(table,productionButton('刷新状态','primary',async()=>{await refreshData();render();}),productionButton('刷新Design模型目录','secondary',async()=>{try{state.models=await call('/api/models/refresh','POST',{});toast('云端目录已更新');}catch(e){toast('Design暂不可用，本机功能不受影响：'+e.message,true);}}));
    if((caps.models||[]).some(m=>busyStates.has(m.status)))timer=setTimeout(async()=>{try{caps=await call('/api/local-capabilities');if(active&&page==='models')render();}catch(e){if(active&&page==='models')showFailure(e);}},6000);
  }
  function change(fn){history.push(structuredClone(timeline));if(history.length>40)history.shift();fn();dirty=true;edit();}
  async function saveEdit(){if(!timeline)return;timeline=await call('/api/timeline','PUT',timeline);dirty=false;toast('时间线已保存');}
  async function edit(){
    if(!timeline){body.replaceChildren(note('读取时间线…'));try{timeline=await call('/api/timeline');if(active&&page==='edit')edit();}catch(e){toast(e.message,true);}return;}
    previewCleanup();body.replaceChildren();const toolbar=el('div','edit-toolbar');toolbar.append(el('h3','','剪辑成片'),el('span','field-help',dirty?'有未保存修改':`版本 ${timeline.revision}`),productionButton('撤销','secondary',()=>{if(history.length){timeline=history.pop();dirty=true;edit();}}),productionButton('保存剪辑','secondary',async()=>{try{await saveEdit();edit();}catch(e){toast(e.message,true);}}),productionButton('渲染并导出 MP4','primary',async()=>{try{await saveEdit();await launchLocal({operation:'render',revision:timeline.revision});}catch(e){toast(e.message,true);}}));body.append(toolbar);
    const settings=el('div','production-params'),size=select([['1280x720','横屏 720p'],['1920x1080','横屏 1080p'],['720x1280','竖屏 720p'],['1080x1920','竖屏 1080p']],`${timeline.settings.width}x${timeline.settings.height}`),fps=select([[24,'24 fps'],[25,'25 fps'],[30,'30 fps']],String(timeline.settings.fps));size.onchange=()=>change(()=>{const [w,h]=size.value.split('x').map(Number);timeline.settings.width=w;timeline.settings.height=h;});fps.onchange=()=>change(()=>timeline.settings.fps=Number(fps.value));settings.append(labeled('导出画幅',size),labeled('帧率',fps));body.append(settings);
    const layout=el('div','edit-layout'),bin=el('aside','edit-bin'),tracks=el('div','edit-tracks');bin.append(el('h4','','素材箱'));const query=input('');query.placeholder='筛选视频或声音';bin.append(query);const items=el('div');bin.append(items);
    function renderBin(){items.replaceChildren();media.filter(a=>['video','audio'].includes(a.mediaType)&&a.name.toLowerCase().includes(query.value.toLowerCase())).forEach(a=>{const item=el('div','bin-item');item.append(el('strong','',a.name),el('small','',`${a.mediaType==='video'?'视频':'声音'} · ${Number(a.duration||0).toFixed(1)} 秒`));item.append(productionButton(a.mediaType==='video'?'添加视频':'添加音轨','secondary',()=>change(()=>{const row={id:uid(),assetId:a.id,in:0,out:Number(a.duration||1),gain:1,mute:false};if(a.mediaType==='audio'){row.at=0;timeline.audio.push(row);}else timeline.clips.push(row);})));items.append(item);});if(!items.childElementCount)items.append(note('还没有可剪素材。先生成或从资源库导入视频、声音。'));}query.oninput=renderBin;renderBin();
    previewCleanup=window.mountTimelinePreview(tracks,timeline,media);
    let cursor=0;const total=timeline.clips.reduce((sum,c)=>sum+c.out-c.in,0);tracks.append(el('h4','',`视频 · ${total.toFixed(2)} 秒`));
    const overview=el('div','v6-timeline'),ruler=el('div','timeline-ruler'),lane=el('div','timeline-lane');[0,.25,.5,.75,1].forEach(r=>ruler.append(el('span','',(total*r).toFixed(1)+'s')));
    timeline.clips.forEach(c=>{const a=media.find(a=>a.id===c.assetId),b=productionButton(a?.name||'片段',selectedClipId===c.id?'active':'',()=>{selectedClipId=c.id;edit();dialog.focus({preventScroll:true});});b.style.flexGrow=Math.max(.1,c.out-c.in);b.append(el('small','',(c.out-c.in).toFixed(2)+' 秒'));lane.append(b);});overview.append(ruler,lane);if(timeline.clips.length)tracks.append(overview);
    function clipRow(clip,index,field){const a=media.find(a=>a.id===clip.assetId),row=el('details','timeline-clip');row.open=clip.id===selectedClipId||(!selectedClipId&&field==='clips'&&index===0);row.ontoggle=()=>{if(row.open)selectedClipId=clip.id;};row.draggable=field==='clips';row.ondragstart=e=>e.dataTransfer.setData('text/plain',String(index));row.ondragover=e=>e.preventDefault();row.ondrop=e=>{e.preventDefault();const from=Number(e.dataTransfer.getData('text/plain'));if(field==='clips'&&Number.isInteger(from)&&from>=0&&from<timeline.clips.length)change(()=>{const moved=timeline.clips.splice(from,1)[0];timeline.clips.splice(index,0,moved);});};
      const heading=el('summary','clip-heading');heading.append(el('strong','',`${index+1}. ${a?.name||clip.assetId}`));if(field==='clips'){heading.append(el('span','',`${cursor.toFixed(2)} → ${(cursor+clip.out-clip.in).toFixed(2)} s`));cursor+=clip.out-clip.in;}row.append(heading);
      if(a?.waveform?.length){const wave=el('div','wave-mini');a.waveform.filter((_,i)=>i%2===0).forEach(value=>{const bar=el('i');bar.style.height=Math.max(2,value*38)+'px';wave.append(bar);});row.append(wave);}
      const controls=el('div','clip-controls');[['in','素材入点'],['out','素材出点'],...(field==='audio'?[['at','时间线起点']]:[]),['gain','音量倍数']].forEach(([key,label])=>{const x=input(clip[key],'number');x.step=.01;x.min=0;x.onchange=()=>change(()=>clip[key]=Number(x.value));controls.append(labeled(label,x));});const mute=el('input');mute.type='checkbox';mute.checked=clip.mute;mute.onchange=()=>change(()=>clip.mute=mute.checked);controls.append(labeled(field==='clips'?'静音原声':'静音',mute));row.append(controls);
      const actions=el('div','clip-actions');actions.append(productionButton('预览源素材','quiet',()=>{const p=el('dialog','clip-preview-dialog');p.append(productionButton('关闭','secondary',()=>p.close()));preview(a,p);p.addEventListener('close',()=>{p.querySelectorAll('audio,video').forEach(m=>m.pause());p.remove();});document.body.append(p);p.showModal();}),productionButton('复制','secondary',()=>change(()=>timeline[field].splice(index+1,0,{...clip,id:uid(),...(field==='audio'?{at:clip.at+clip.out-clip.in}:{})}))),productionButton('移除','quiet',()=>change(()=>timeline[field].splice(index,1))));
      if(field==='clips'){
        const cut=input(((clip.in+clip.out)/2).toFixed(2),'number');cut.step=.01;cut.setAttribute('aria-label','分割素材时间');actions.append(cut,productionButton('在此处分割','secondary',()=>{const t=Number(cut.value);if(t<=clip.in+.04||t>=clip.out-.04)return toast('分割点须在片段内部',true);change(()=>timeline.clips.splice(index,1,{...clip,out:t},{...clip,id:uid(),in:t}));}));
        actions.append(productionButton('前移','quiet',()=>{if(index>0)change(()=>[timeline.clips[index-1],timeline.clips[index]]=[timeline.clips[index],timeline.clips[index-1]]);}),productionButton('后移','quiet',()=>{if(index<timeline.clips.length-1)change(()=>[timeline.clips[index+1],timeline.clips[index]]=[timeline.clips[index],timeline.clips[index+1]]);}));
      }row.append(actions);return row;
    }
    timeline.clips.forEach((c,i)=>tracks.append(clipRow(c,i,'clips')));if(!timeline.clips.length)tracks.append(note('从左侧素材箱添加视频。片段可拖动排序，分割/复制不会改动原文件。'));
    tracks.append(el('h4','','独立音轨 · 对白 / 环境 / 音乐'));timeline.audio.forEach((c,i)=>tracks.append(clipRow(c,i,'audio')));
    const subtitles=el('details','timeline-subtitles');subtitles.append(el('summary','','字幕（随成片导出 SRT）'));(timeline.subtitles||[]).forEach((c,i)=>{const row=el('div','subtitle-row'),start=input(c.start,'number'),end=input(c.end,'number'),text=input(c.text);start.onchange=()=>change(()=>c.start=Number(start.value));end.onchange=()=>change(()=>c.end=Number(end.value));text.onchange=()=>change(()=>c.text=text.value);row.append(labeled('开始秒',start),labeled('结束秒',end),labeled('字幕',text),productionButton('移除','quiet',()=>change(()=>timeline.subtitles.splice(i,1))));subtitles.append(row);});subtitles.append(productionButton('添加字幕','secondary',()=>change(()=>timeline.subtitles.push({start:0,end:Math.min(2,total),text:''}))));tracks.append(subtitles);
    layout.append(bin,tracks);body.append(layout,note('音轨位置独立于视频排序；移动视频后请检查对白起点。导出会混合原声与独立音轨，替换配音时请静音对应视频原声。'));
  }
  function jobs(){body.append(el('h3','','任务与结果'));const entries=[...localJobs.map(j=>({...j,local:true})),...state.jobs].sort((a,b)=>String(b.createdAt).localeCompare(String(a.createdAt)));
    if(!entries.length)body.append(note('暂无任务'));
    entries.slice(0,60).forEach(j=>{const row=el('article','production-job');row.append(el('strong','',j.name||({render:'剪辑导出',extract_audio:'提取音轨',separate_vocals:'人声分离',transcribe:'字幕识别',tts:'参考配音',voice_convert:'保留表演换声',speech_edit:'局部声音精修',lip_sync:'口型修复'}[j.operation])||j.shotId||'生成任务'),taskState(j,{prefix:j.local?'本机 · ':''}));if(j.error)row.append(el('p','error-message',j.error));if(j.attachmentWarning)row.append(note(j.attachmentWarning));if(j.projectAttached)row.append(note('已关联镜头 · 待试听'));
      if(j.inputs?.length){const sources=el('details','job-inputs');sources.append(el('summary','','回听原素材与目标参考'));j.inputs.forEach(ref=>{const a=media.find(a=>a.id===ref.assetId);if(a){const block=el('div','job-output');block.append(el('small','',ref.label+' · '+a.name));preview(a,block);sources.append(block);}});row.append(sources);}if(j.spokenText)row.append(el('p','job-script',j.spokenText));
      const outputs=j.assets||(j.mediaUrl?[{name:j.name||'结果',mediaType:j.kind,mediaUrl:j.mediaUrl}]:[]);outputs.forEach(a=>{const block=el('div','job-output');block.append(el('small','',a.name||'结果'));preview(a,block);const link=el('a','secondary','保存文件');link.href=safeUrl(a.mediaUrl);link.download='';block.append(link);
        if(a.id&&['audio','video'].includes(a.mediaType))block.append(productionButton('送到剪辑素材箱','secondary',async()=>{try{await call('/api/editor/import-assets','POST',{assetIds:[a.id]});if(dialog.close()){await openProEditor();const editor=ProEditors.get(workspace);if(editor?.frame)editor.send('sync').catch(()=>{});}}catch(e){toast(e.message,true);}}));row.append(block);});
      if(j.subtitleUrl){const a=el('a','','下载 SRT 字幕');a.href=safeUrl(j.subtitleUrl);a.download='film.srt';row.append(a);}
      if(j.transcript){const result=el('div','transcript-result'),offset=input(0,'number');offset.step=.1;result.append(el('strong','','识别稿 · 请核对人名、同音字与时间'),el('p','',j.transcript.text||j.transcript.segments.map(s=>s.text).join('')),productionButton('复制文字','secondary',async()=>{try{await navigator.clipboard.writeText(j.transcript.text||j.transcript.segments.map(s=>s.text).join(''));toast('已复制识别稿');}catch(e){toast('复制失败，请直接选择文字复制',true);}}),labeled('在时间线中的起点（秒）',offset),productionButton('加入剪辑字幕','secondary',async()=>{try{const current=await call('/api/editor/project');await call('/api/editor/captions/apply','POST',{jobId:j.id,revision:current.revision,offset:Number(offset.value),position:'bottom',replaceAuto:false});if(dialog.close()){await openProEditor();ProEditors.get(workspace)?.send('sync').catch(()=>{});}toast('字幕已加入专业剪辑，可在画面中选择和拖动');}catch(e){toast(e.message,true);}}));row.append(result);}body.append(row);});
    clearTimeout(timer);if(active&&page==='jobs'&&entries.some(j=>busyStates.has(j.status)))timer=setTimeout(async()=>{try{await refreshData();await reloadProjectData();if(page==='jobs'){const playing=[...body.querySelectorAll('audio,video')].some(m=>!m.paused&&!m.ended);if(!playing)render();else {const resume=()=>{if(!active||page!=='jobs')return;if([...body.querySelectorAll('audio,video')].some(m=>!m.paused&&!m.ended))timer=setTimeout(resume,2000);else render();};timer=setTimeout(resume,2000);}}}catch(e){toast(e.message,true);}},3000);
  }
  tabs();body.append(taskState({status:'processing'},{label:'读取素材'}));try{await refreshData();render();}catch(e){showFailure(e);}
}
document.addEventListener('DOMContentLoaded',()=>{
  const nav=document.querySelector('.view-tabs');if(nav)[['generate','生成'],['audio','声音'],['edit','剪辑'],['jobs','任务']].forEach(([page,label])=>{const b=productionButton(label,'production-entry',()=>openProduction(page));b.dataset.production=page;nav.append(b);});
  const actions=document.querySelector('.creative-actions');if(actions)actions.prepend(productionButton('生成素材','primary',()=>openProduction('generate')));
});
