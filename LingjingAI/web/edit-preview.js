/* Real single-video-track preview with independent audio tracks; sources stay immutable. */
'use strict';
window.mountTimelinePreview=function(host,timeline,media){
  const clips=timeline.clips||[];let at=0;
  const sequence=clips.map(c=>{const entry={...c,start:at,end:at+c.out-c.in,asset:media.find(a=>a.id===c.assetId)};at=entry.end;return entry;});
  if(!sequence.length)return ()=>{};
  const wrap=el('section','timeline-monitor'),frame=el('div','timeline-monitor-frame'),video=el('video'),caption=el('div','timeline-caption'),controls=el('div','timeline-monitor-controls');
  video.preload='metadata';video.playsInline=true;frame.append(video,caption);wrap.append(frame,controls);host.append(wrap);
  const title=el('strong','','剪辑预览'),info=el('p','field-help','按当前入出点、静音、音量和独立音轨播放。'),range=el('input'),time=el('span','monitor-time','0.00 / '+at.toFixed(2)+' 秒');range.type='range';range.min=0;range.max=at;range.step=.01;range.value=0;range.setAttribute('aria-label','预览播放位置');
  let position=0,index=-1,playing=false,disposed=false,raf=null,context=null,seeking=false,serial=0;
  const audioTracks=(timeline.audio||[]).map(c=>{const a=media.find(a=>a.id===c.assetId),player=new Audio();player.preload='metadata';if(a?.mediaUrl)player.src=safeUrl(a.mediaUrl);return {clip:c,player,node:null};});
  let videoGain=null;
  function connectAudio(){if(context)return;const AudioCtx=window.AudioContext||window.webkitAudioContext;if(!AudioCtx)return;context=new AudioCtx();videoGain=context.createGain();context.createMediaElementSource(video).connect(videoGain);videoGain.connect(context.destination);audioTracks.forEach(t=>{t.node=context.createGain();context.createMediaElementSource(t.player).connect(t.node);t.node.connect(context.destination);});}
  function gain(player,node,clip){const value=clip.mute?0:Math.max(0,Math.min(2,clip.gain??1));if(node)node.gain.value=value;else player.volume=Math.min(1,value);}
  function syncAudio(force=false){audioTracks.forEach(t=>{const c=t.clip,local=c.in+position-c.at,on=playing&&position>=c.at&&local<c.out;gain(t.player,t.node,c);if(on){if(force||Math.abs(t.player.currentTime-local)>.15)t.player.currentTime=Math.max(c.in,local);if(t.player.paused)t.player.play().catch(()=>{});}else t.player.pause();});}
  function paint(){range.value=String(position);time.textContent=position.toFixed(2)+' / '+at.toFixed(2)+' 秒';caption.textContent=(timeline.subtitles||[]).filter(c=>position>=c.start&&position<c.end).map(c=>c.text).join('\n');play.textContent=playing?'暂停预览':'预览剪辑';}
  function pause(){playing=false;video.pause();audioTracks.forEach(t=>t.player.pause());cancelAnimationFrame(raf);paint();}
  function setPosition(value,force=false){position=Math.max(0,Math.min(at,value));let next=sequence.findIndex(c=>position<c.end-.002);if(next<0)next=sequence.length-1;const c=sequence[next],changed=next!==index,token=++serial;index=next;gain(video,videoGain,c);seeking=true;
    const seek=()=>{if(disposed||token!==serial)return;video.currentTime=Math.max(c.in,Math.min(c.out-.005,c.in+position-c.start));seeking=false;if(playing)video.play().catch(e=>{if(!disposed){pause();info.textContent='预览未能播放：'+e.message;}});};
    if(changed){video.pause();video.src=safeUrl(c.asset?.mediaUrl||'');if(video.readyState>=1)seek();else video.addEventListener('loadedmetadata',seek,{once:true});}else if(force)seek();else seeking=false;
    syncAudio(true);paint();
  }
  function tick(){if(!playing||disposed)return;const c=sequence[index];if(!seeking&&!video.seeking){position=c.start+video.currentTime-c.in;if(video.currentTime>=c.out-.015||video.ended){if(index+1<sequence.length)setPosition(c.end,true);else{position=at;pause();return;}}syncAudio();paint();}raf=requestAnimationFrame(tick);}
  const play=button('预览剪辑','secondary',async()=>{if(playing){pause();return;}try{connectAudio();if(context)await context.resume();if(disposed)return;playing=true;if(position>=at-.02)position=0;setPosition(position,true);cancelAnimationFrame(raf);raf=requestAnimationFrame(tick);}catch(e){pause();info.textContent=e.message;}});
  range.oninput=()=>setPosition(Number(range.value),true);controls.append(title,info,play,range,time);setPosition(0,true);
  return ()=>{disposed=true;playing=false;serial++;cancelAnimationFrame(raf);video.pause();video.removeAttribute('src');video.load();audioTracks.forEach(t=>{t.player.pause();t.player.removeAttribute('src');t.player.load();});context?.close().catch(()=>{});};
};
