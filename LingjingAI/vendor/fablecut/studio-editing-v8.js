/* Editing commands, quick effects and delivery controls for 灵镜AI. */
'use strict';
let studioEditTool='select',studioMenu=null;
function studioRefreshEdit(){scheduleSave();rebuildClips();renderInspector();drawFrame(state.time);}
function studioSetTool(tool){studioEditTool=tool;document.body.classList.toggle('razor-tool',tool==='razor');document.querySelector('#studioSelectTool')?.classList.toggle('on',tool==='select');document.querySelector('#studioRazorTool')?.classList.toggle('on',tool==='razor');}
studioButton('studioCut','剪切',()=>{if(selectedClips().length){studioCopy();deleteSelected();rebuildClips();}},toolbar);
studioButton('studioSelectTool','选择 V',()=>studioSetTool('select'),toolbar);
studioButton('studioRazorTool','剃刀 C',()=>studioSetTool('razor'),toolbar);
function studioTimeAtPointer(event){const row=event.target.closest('[data-track]')||els.tracks;return Math.max(0,Math.round((event.clientX-row.getBoundingClientRect().left)/state.pps*projectFps())/projectFps());}
function studioSplitAll(){const ids=[...state.selIds];setSelection([]);splitAtPlayhead();setSelection(ids);rebuildClips();}
function studioCloseMenu(){studioMenu?.remove();studioMenu=null;}
function studioOpenMenu(event){
 if(!els.tracks.contains(event.target))return;event.preventDefault();event.stopImmediatePropagation();window.focus();studioCloseMenu();
 const element=event.target.closest('.clip'),clip=element?getClip(element.dataset.id):null,at=studioTimeAtPointer(event);
 if(clip&&!state.selIds.has(clip.id))setSelection([clip.id]);
 const menu=document.createElement('div');menu.className='studio-context-menu';menu.setAttribute('role','menu');studioMenu=menu;
 const add=(label,key,fn,disabled=false)=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','menuitem');b.disabled=disabled;b.append(document.createTextNode(label));const k=document.createElement('kbd');k.textContent=key;b.append(k);b.onclick=()=>{studioCloseMenu();Promise.resolve(fn()).catch(e=>toast(e.message));};menu.append(b);};
 const sep=()=>menu.append(document.createElement('hr'));const selected=selectedClips().length>0;
 add('剪切','Ctrl+X',()=>{studioCopy();deleteSelected();rebuildClips();},!selected);
 add('复制','Ctrl+C',studioCopy,!selected);add('粘贴到播放头','Ctrl+V',studioPaste,!studioClipboard?.length);
 add('粘贴到此处','',()=>{setTime(at);studioPaste();},!studioClipboard?.length);sep();
 add('在播放头处分割','Ctrl+K / S',splitAtPlayhead,!selected);
 add('在鼠标位置分割','C 剃刀',()=>{setTime(at);splitAtPlayhead();rebuildClips();},!clip);
 add('所有轨道在播放头处分割','Ctrl+Shift+K',studioSplitAll);add('删除','Delete',()=>{deleteSelected();rebuildClips();},!selected);sep();
 add('音画分离','Ctrl+Shift+L',studioUnlink,!selected);add('片段效果…','',studioEffects,!selected);add('本机模型处理…','',studioOpenAI,!clip||!['audio','video'].includes(clip.kind));sep();
 add('导出所选范围…','',async()=>{await openExportSetup();setTimeout(()=>{document.querySelector('#exportRangeSel').value='selected';syncExportRangeUi();studioExportSummary();},100);},!selected);
 document.body.append(menu);menu.style.left=Math.min(event.clientX,innerWidth-menu.offsetWidth-8)+'px';menu.style.top=Math.max(8,Math.min(event.clientY,innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button:not(:disabled)')?.focus();
 menu.onkeydown=e=>{const buttons=[...menu.querySelectorAll('button:not(:disabled)')];const n=buttons.indexOf(document.activeElement);if(e.key==='Escape'){studioCloseMenu();e.preventDefault();}else if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();buttons[(n+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}e.stopPropagation();};
}
window.addEventListener('contextmenu',studioOpenMenu,true);
window.addEventListener('pointerdown',e=>{if(studioMenu&&!studioMenu.contains(e.target))studioCloseMenu();if(studioEditTool!=='razor'||e.button!==0||!els.tracks.contains(e.target))return;const node=e.target.closest('.clip');if(!node)return;e.preventDefault();e.stopImmediatePropagation();setSelection([node.dataset.id]);setTime(studioTimeAtPointer(e));splitAtPlayhead();rebuildClips();},true);
window.addEventListener('keydown',e=>{if(isTypingTarget(document.activeElement)||studioMenu||document.querySelector('dialog[open]'))return;const mod=e.ctrlKey||e.metaKey,k=e.key.toLowerCase();let done=true;if(mod&&k==='k')e.shiftKey?studioSplitAll():splitAtPlayhead();else if(mod&&e.shiftKey&&k==='l')studioUnlink();else if(mod&&e.altKey&&k==='d'){studioCopy();studioPaste();}else if(!mod&&!e.altKey&&k==='c')studioSetTool('razor');else if(!mod&&!e.altKey&&k==='v')studioSetTool('select');else done=false;if(done){e.preventDefault();e.stopImmediatePropagation();}},true);
function studioApplyEffect(effect){
 const clips=(effect==='audio-fade'?withLinked(selectedClips()):selectedClips()).filter(c=>effect==='audio-fade'?['audio','video'].includes(c.kind):c.kind!=='audio');if(!clips.length)return toast('先选择适合此效果的片段');pushUndo();
 for(const c of clips){const p=c.props,d=Math.min(.3,c.duration/3);
  if(effect.startsWith('look:'))p.filterPreset=effect.slice(5);
  else if(effect==='dissolve'){c.transitionIn={type:'fade',duration:d};c.transitionOut={type:'fade',duration:d};}
  else if(effect.startsWith('transition:'))c.transitionIn={type:effect.slice(11),duration:Math.min(.4,c.duration/2)};
  else if(effect==='push-in'||effect==='pull-out'){const base=p.scale||1;c.keyframes={...c.keyframes,scale:[{t:0,v:effect==='push-in'?base:base*1.1},{t:c.duration,v:effect==='push-in'?base*1.1:base,ease:'ease-in-out'}]};}
  else if(effect==='handheld')p.shake=3;
  else if(effect==='grain')p.grain=12;
  else if(effect==='audio-fade'){const v=p.volume??1;c.keyframes={...c.keyframes,volume:[{t:0,v:0},{t:d,v,ease:'linear'},{t:c.duration-d,v},{t:c.duration,v:0,ease:'linear'}]};}
  else if(effect.startsWith('text:')&&c.kind==='text')p.textAnim=effect.slice(5);
  else if(effect==='reset'){p.filterPreset='none';p.grain=0;p.shake=0;delete c.transitionIn;delete c.transitionOut;}
 }
 studioRefreshEdit();toast('效果已应用，可撤销或在属性里细调');
}
function studioEffects(){
 if(!selectedClips().length)return toast('先选择时间线片段');const d=document.createElement('dialog');d.className='studio-effects-dialog';d.innerHTML='<header><h2>片段效果</h2><button class="btn effect-close">关闭</button></header><p>应用到当前选择；支持撤销，右侧属性可继续微调。</p>';
 const groups=[['调色',[['原色','look:none'],['电影','look:cinematic'],['青橙','look:teal-orange'],['黑白','look:noir'],['暖阳','look:sunset'],['冷夜','look:midnight'],['复古','look:vintage'],['柔光','look:dreamy']]],['转场与运镜',[['淡入淡出 · Ctrl+D','dissolve'],['左滑入','transition:slide-left'],['缩放入场','transition:zoom'],['擦除入场','transition:wipe'],['缓慢推近','push-in'],['缓慢拉远','pull-out'],['轻微手持','handheld'],['胶片颗粒','grain']]],['声音与字幕',[['声音淡入淡出','audio-fade'],['打字字幕','text:typewriter'],['逐词弹入','text:word-pop'],['卡拉OK字效','text:karaoke'],['清除调色与转场','reset']]]];
 for(const [title,items] of groups){const h=document.createElement('h3');h.textContent=title;const row=document.createElement('div');row.className='effect-grid';for(const [label,key] of items){const b=document.createElement('button');b.className='btn';b.textContent=label;b.dataset.effect=key;b.disabled=key.startsWith('text:')&&!selectedClips().some(c=>c.kind==='text');b.onclick=()=>studioApplyEffect(key);row.append(b);}d.append(h,row);}d.querySelector('.effect-close').onclick=()=>d.close();document.body.append(d);d.showModal();d.addEventListener('close',()=>d.remove());
}
async function studioOpenAI(){const c=getClip(state.selId)||selectedClips()[0];if(!c||!['video','audio'].includes(c.kind))return toast('先选一个视频或声音片段');pause();await studioFlush();return studioRequest('tools',{clipId:c.id,revision:project.revision,clipName:c.name,kind:c.kind,start:c.start,in:c.in,duration:c.duration,mediaId:c.mediaId});}
studioButton('studioEffects','片段效果',studioEffects,studioTopActions);
studioButton('studioAI','本机模型',studioOpenAI,studioTopActions);
window.addEventListener('message',async event=>{if(event.source!==parent||event.origin!==studioParent||!event.data?.studioEditor||event.data.action!=='place-result')return;const d=event.data;try{const source=getClip(d.payload.context.id),context=d.payload.context;if(!source||['mediaId','start','in','duration','track','kind'].some(k=>source[k]!==context[k]))throw new Error('原片段已移动或裁切，结果已在素材箱，请手动放入时间线');const m=getMedia(d.payload.assetId);if(!m)throw new Error('结果未进入素材箱');const kind=m.kind==='audio'?'audio':'video';let track=TRACKS.find(t=>t.kind===kind&&!project.clips.some(c=>c.track===t.id&&c.start<source.start+m.duration&&clipEnd(c)>source.start));if(!track)throw new Error('没有空闲轨道，请添加轨道后从素材箱拖入');addClipFromMedia(m,track.id,source.start);const added=selectedClips();if(d.payload.muteOriginal)for(const c of withLinked([source])){c.props.volume=0;if(c.keyframes)delete c.keyframes.volume;}for(const c of added)c.props.studioUnlinked=true;await studioFlush();renderInspector();toast('处理结果已放到独立轨道，原素材保留');parent.postMessage({studioEditor:true,id:d.id,reply:true,result:true},studioParent);}catch(e){parent.postMessage({studioEditor:true,id:d.id,reply:true,error:e.message},studioParent);}},true);
// Delivery size is applied after compositing, without changing timeline geometry.
const studioOriginalExportSource=exportSourceCanvas;let studioScaledCanvas=null;
function studioOutputSize(){const f=getExportFrame(),w=f?.w||project.width,h=f?.h||project.height,n=Number(document.querySelector('#studioOutputSize')?.value)||0;if(!n)return {w,h};const scale=n/Math.max(w,h);return {w:Math.max(2,Math.round(w*scale/2)*2),h:Math.max(2,Math.round(h*scale/2)*2)};}
exportSourceCanvas=function(){const source=studioOriginalExportSource(),size=studioOutputSize();if(size.w===source.width&&size.h===source.height)return source;if(!studioScaledCanvas)studioScaledCanvas=document.createElement('canvas');if(studioScaledCanvas.width!==size.w||studioScaledCanvas.height!==size.h){studioScaledCanvas.width=size.w;studioScaledCanvas.height=size.h;}studioScaledCanvas.getContext('2d',{alpha:false}).drawImage(source,0,0,size.w,size.h);return studioScaledCanvas;};
const studioBaseExportRange=exportRange;
exportRange=function(){if(document.querySelector('#exportRangeSel')?.value!=='selected')return studioBaseExportRange();const clips=selectedClips();if(!clips.length)return {start:0,end:0,dur:0,frames:0};const start=Math.min(...clips.map(c=>c.start)),end=Math.max(...clips.map(clipEnd)),frames=Math.round((end-start)*projectFps());return {start,end:start+frames/projectFps(),dur:frames/projectFps(),frames};};
const delivery=document.createElement('div');delivery.className='studio-delivery-fields';delivery.innerHTML='<label>成片名称<input id="studioExportName" maxlength="100" placeholder="留空使用项目名称"></label><label>输出尺寸<select id="studioOutputSize"><option value="0">跟随画布</option><option value="1280">长边1280 · 预览</option><option value="1920">长边1920 · 高清</option><option value="3840">长边3840 · 4K</option></select></label><p id="studioExportSummary"></p><p class="dim">输出尺寸保持比例；放大不会增加源片细节。字幕与效果合成进画面。导出期间保持窗口打开。</p>';
document.querySelector('#exportSetup h2').after(delivery);document.querySelector('#exportRangeSel').append(new Option('所选片段范围','selected'));
function studioExportSummary(){const size=studioOutputSize(),range=exportRange();document.querySelector('#studioExportSummary').textContent=`${size.w} × ${size.h} · ${projectFps()} fps · ${range.dur.toFixed(2)} 秒 · ${range.frames} 帧`;const resizing=!!Number(document.querySelector('#studioOutputSize').value);if(resizing){els.engineFast.checked=true;els.engineRealtime.checked=false;els.engineRealtime.disabled=true;}else els.engineRealtime.disabled=!(state.connected&&state.ffmpeg&&state.webCodecs&&!getExportFrame());syncExportProfileVisibility();}
delivery.addEventListener('change',studioExportSummary);document.querySelector('#exportRangeSel').addEventListener('change',studioExportSummary);document.querySelector('#btnExport').addEventListener('click',()=>setTimeout(studioExportSummary,200));document.querySelector('#btnStartExport').addEventListener('click',()=>{if(Number(document.querySelector('#studioOutputSize').value)){els.engineFast.checked=true;els.engineRealtime.checked=false;}},true);
const studioBaseRangeNote=exportRangeNoteText;exportRangeNoteText=function(){if(document.querySelector('#exportRangeSel')?.value==='selected'){const r=exportRange();return '所选范围 · '+fmt(r.start)+' → '+fmt(r.end)+'（包含范围内其他启用轨道）';}return studioBaseRangeNote();};
studioSetTool('select');
