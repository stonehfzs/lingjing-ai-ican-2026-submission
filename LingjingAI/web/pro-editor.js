'use strict';
const ProEditors=new Map();
async function openProEditor(){
 if(!state.project||state.switching)return toast('请先打开项目');
 const workspace=state.workspaceId;
 if(window.ProductionController?.isPro&&window.ProductionController.workspace===workspace)return;
 if(window.ProductionController&&!window.ProductionController.close())return;
 closeReview();closeInspector();
 let entry=ProEditors.get(workspace);
 const request=async(path,body,method)=>{const response=await fetch(path,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json','X-Studio-Request':'1','X-Studio-Workspace':workspace},...(body?{body:JSON.stringify(body)}:{})});const value=await response.json();if(!response.ok)throw new Error(value.error||'剪辑接口失败');return value;};
 if(!entry){
  const section=el('section','pro-editor-surface');section.innerHTML='<div class="pro-loading">正在打开多轨剪辑…</div>';document.querySelector('.work-area').append(section);
  entry={section,workspace,frame:null,exporting:false,dirty:false,request,pending:new Map()};ProEditors.set(workspace,entry);
  entry.send=(action,payload)=>new Promise((resolve,reject)=>{if(!entry.frame)return reject(new Error('剪辑尚未打开'));const id=crypto.randomUUID();entry.pending.set(id,{resolve,reject});entry.frame.contentWindow.postMessage({studioEditor:true,id,action,payload},entry.url);setTimeout(()=>{if(entry.pending.has(id)){entry.pending.delete(id);reject(new Error('剪辑窗口暂未响应'));}},15000);});
  entry.receive=async event=>{if(event.source!==entry.frame?.contentWindow||event.origin!==entry.url||!event.data?.studioEditor)return;const d=event.data;if(d.event==='status'){entry.exporting=d.exporting;entry.dirty=d.dirty;return;}if(d.reply){const p=entry.pending.get(d.id);if(p){entry.pending.delete(d.id);d.error?p.reject(new Error(d.error)):p.resolve(d.result);}return;}let result,error;
   try{if(['materials','library','tools'].includes(d.action)&&state.workspaceId!==workspace)throw new Error('项目已切换，请回到原项目再选择素材');switch(d.action){
    case 'tools':await openEditorTools(entry,d.payload);result=true;break;
    case 'materials':await pickEditorMedia(entry);result=true;break;
    case 'library':await entry.send('flush');await openPersonalLibrary({onSelect:async asset=>{await entry.request('/api/editor/import-assets',{assetIds:[asset.id]});await entry.send('sync');}});result=true;break;
    case 'proxy':result=await request('/api/editor/proxy',d.payload);break;
    case 'proxies':result=await request('/api/editor/proxies');break;
    case 'captions':result=await request('/api/editor/captions',d.payload);break;
    case 'caption-status':{const jobs=await request('/api/local-jobs');result=jobs.jobs.find(j=>j.id===d.payload.jobId);if(!result)throw new Error('找不到字幕任务');break;}
    case 'apply-captions':result=await request('/api/editor/captions/apply',d.payload);break;
    case 'recover':result=await request('/api/editor/recover',d.payload);if(state.workspaceId===workspace){if(state.dirty&&!await saveGraph(false))throw new Error('成片已保存；请保存画布后刷新资源库');await reloadProjectData();showEditorExport(result);}break;
    default:throw new Error('未支持的剪辑操作');
   }}catch(e){error=e.message;}
   entry.frame.contentWindow.postMessage({studioEditor:true,reply:true,id:d.id,result,error},entry.url);
  };window.addEventListener('message',entry.receive);
  try{const session=await request('/api/editor/open',{});entry.url=session.url;entry.frame=el('iframe','pro-editor-frame');entry.frame.title='灵镜AI多轨剪辑器';entry.frame.src=session.url+'/?parent='+encodeURIComponent(location.origin);entry.frame.allow='autoplay; clipboard-read; clipboard-write';section.replaceChildren(entry.frame);}catch(e){section.replaceChildren(el('p','error-message',e.message),button('重试','primary',()=>{ProEditors.delete(workspace);section.remove();window.ProductionController=null;openProEditor();}));}
 }
 entry.section.style.display='block';document.body.classList.add('production-open','pro-edit-open');
 const close=()=>{if(entry.exporting){toast('正在导出，请完成或取消导出后再离开');return false;}entry.send('pause').catch(()=>{});entry.send('flush').catch(e=>toast('剪辑草稿保留在窗口中：'+e.message,true));entry.section.style.display='none';document.body.classList.remove('production-open','pro-edit-open');window.ProductionController=null;return true;};
 window.ProductionController={isPro:true,workspace,close,save:()=>entry.send('flush').then(()=>toast('剪辑已保存')).catch(e=>toast(e.message,true)),navigate:async next=>{if(next==='edit')return;if(close())await openProduction(next);}};
 $$('[data-production]').forEach(b=>b.classList.toggle('active',b.dataset.production==='edit'));$$('.view-tabs [data-view]').forEach(b=>b.classList.remove('active'));
}
async function pickEditorMedia(entry){
 const doc=await entry.request('/api/media'),dialog=el('dialog','editor-media-picker');dialog.innerHTML='<div class="library-dialog-head"><h2>加入剪辑素材箱</h2><button class="secondary picker-close">关闭</button></div><p class="field-help">选择本项目中的图片、视频和声音，加入后拖动到时间线。</p><input class="picker-search" placeholder="搜索项目素材"><div class="picker-list"></div><button class="primary picker-add">加入所选素材</button>';document.body.append(dialog);dialog.showModal();const selected=new Set(),list=dialog.querySelector('.picker-list');function draw(q=''){list.replaceChildren();doc.assets.filter(a=>['video','audio','image'].includes(a.mediaType)&&a.name.toLowerCase().includes(q.toLowerCase())).forEach(a=>{const row=el('label','picker-row'),check=el('input');check.type='checkbox';check.checked=selected.has(a.id);check.onchange=()=>check.checked?selected.add(a.id):selected.delete(a.id);const info=el('span','',a.name+' · '+a.mediaType);row.append(check,info);list.append(row);});}draw();dialog.querySelector('.picker-search').oninput=e=>draw(e.target.value);dialog.querySelector('.picker-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());dialog.querySelector('.picker-add').onclick=async()=>{try{await entry.send('flush');await entry.request('/api/editor/import-assets',{assetIds:[...selected]});await entry.send('sync');dialog.close();toast('已加入剪辑素材箱');}catch(e){toast(e.message,true);}};
}

function showEditorExport(asset){
 const dialog=el('dialog','editor-export-result');dialog.innerHTML='<div class="library-dialog-head"><h2>成片已保存</h2><button class="secondary export-close">关闭</button></div>';
 const video=el('video');video.controls=true;video.src=safeUrl(asset.mediaUrl);video.preload='metadata';const actions=el('div','dialog-actions');
 const download=el('a','primary','保存副本');download.href=safeUrl(asset.mediaUrl);download.download=(asset.name||'灵镜AI成片')+'.'+(asset.path?.split('.').pop()||'mp4');actions.append(download);
 if(window.studioDesktop)actions.append(button('打开文件位置','secondary',()=>window.studioDesktop.reveal(asset.path).catch(e=>toast(e.message,true))));
 dialog.append(video,el('p','field-help','已登记到当前项目资源库，原素材保留。'),actions);document.body.append(dialog);dialog.showModal();dialog.querySelector('.export-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{video.pause();dialog.remove();});
}
