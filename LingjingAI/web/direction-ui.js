/* Director's notebook: screenplay evidence, casting A/B, live reference gaps. */
'use strict';
async function openDirection(initial='casting') {
  const workspace=state.workspaceId;
  const dialog=el('dialog','direction-dialog');
  const header=el('div','dialog-heading');
  header.append(el('div','', '导演工作簿'),button('关闭','secondary',()=>dialog.close()));
  const tabs=el('div','direction-tabs'),content=el('div','direction-content');
  dialog.append(header,tabs,content);document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  content.append(el('p','','正在读取当前项目…'));
  try {
    const [plan,report,assetsDoc,project]=await Promise.all([api('/api/direction'),api('/api/direction/report'),api('/api/assets'),api('/api/project')]);
    if(state.workspaceId!==workspace){dialog.close();return;}
    const assets=new Map(assetsDoc.assets.map(a=>[a.id,a]));
    function imageReference(a,container){if(!a)return;const img=el('img');img.src=safeUrl(a.mediaUrl);img.alt=a.name;img.loading='lazy';container.append(img);}
    function audioReference(a,container,label){if(!a){container.append(el('p','field-help','尚无可播放文件'));return;}const block=el('div','casting-take');block.append(el('strong','',label),el('small','',a.name));const audio=el('audio');audio.src=safeUrl(a.mediaUrl);audio.controls=true;audio.preload='metadata';block.append(audio);container.append(block);}
    function selectTab(name){
      tabs.replaceChildren();[['casting','角色与声音'],['coverage','逐镜参考'],['spaces','空间与轴线']].forEach(([id,label])=>tabs.append(button(label,id===name?'primary':'secondary',()=>selectTab(id))));
      content.replaceChildren();
      if(name==='casting'){
        content.append(el('h2','', '先选演员，再排这一场戏'),el('p','field-help','人物依据与导演解释分别记录。A/B 用同一句、同模型、同参数比较；试听满意后再选用，正式对白另行制作。'));
        if(!plan.characters.length)content.append(el('p','','当前剧本尚未剖析。让 Codex 使用 minimax-storyboard-studio skill 完成角色、场次和镜头分析。'));
        plan.characters.forEach(character=>{
          const card=el('article','casting-card'),portrait=el('div','casting-portrait'),copy=el('div','casting-copy');
          (character.visualAssetIds||[]).forEach(id=>imageReference(assets.get(id),portrait));
          copy.append(el('h3','',character.name),el('p','casting-facts',character.facts),el('p','',character.interpretation),el('p','field-help',character.arc));
          const evidence=el('small','',`${character.evidenceBlockIds?.length||0} 段剧本依据 · ${character.castingStatus==='selected'?'已选声线':'待试演/选音'}`);copy.append(evidence);
          const cases=el('details');cases.append(el('summary','','查看试演情境与表演指导'));
          (character.auditionCases||[]).forEach(c=>{const block=el('div','casting-case');block.append(button(c.shotId,'quiet',()=>{dialog.close();openReview(c.shotId);}),el('blockquote','',c.text),el('p','',c.situation),el('p','',c.intention),el('small','',`语速 ${c.params?.speed??1} · 音高保持本声线 · 台词原文不加入导演指令`));cases.append(block);});copy.append(cases);
          const takes=el('div','casting-takes');
          if(character.comparison?.takes?.length){character.comparison.takes.forEach(t=>audioReference(assets.get(t.assetId),takes,`${t.label} · ${t.label==='A'?'原声线':'导演版声线'} · 同句试演`));}
          else (character.candidateAssetIds||[]).forEach(id=>audioReference(assets.get(id),takes,'音色预览 · 仍待场景试演'));
          copy.append(takes);
          const actions=el('div','casting-actions');
          (character.candidateAssetIds||[]).forEach((id,index)=>{const a=assets.get(id);if(!a)return;const selected=character.selectedAssetId===id;
            const b=button(selected?'已选用':`选用${index?'导演版':'原'}声线`,'secondary',async()=>{
              if(state.workspaceId!==workspace)return toast('项目已切换，请重新打开',true);
              b.disabled=true;try{await api('/api/direction/cast',{method:'POST',body:JSON.stringify({revision:plan.revision,projectRevision:project.revision,voiceId:character.id,assetId:id})});await reloadProjectData();dialog.close();await openDirection();toast('声线已绑定到该角色各镜；正式对白仍待制作');}catch(e){b.disabled=false;toast(e.message,true);}
            });b.disabled=selected||['blocked','missing'].includes(a.reviewStatus);actions.append(b);
          });copy.append(actions,el('small','field-help','听评：形象与年龄 / 像在对人说话 / 角色辨识度 / 情境转折 / 台词 / 呼吸节奏'));
          card.append(portrait,copy);content.append(card);
        });
      } else if(name==='coverage'){
        content.append(el('h2','',`${report.summary.referenceReady} / ${report.summary.shots} 镜参考覆盖`),el('p','field-help','检查剧本依据、真实文件、引用用途、版本与连续性。参考备齐后，首帧、动作和声音仍需逐镜审阅。'));
        const missing=el('label','direction-filter'),check=el('input');check.type='checkbox';missing.append(check,document.createTextNode('只看缺口或过期分析'));content.append(missing);
        const list=el('div');content.append(list);
        function render(){list.replaceChildren();report.shots.filter(s=>!check.checked||!s.referenceReady).forEach(s=>{
          const row=el('details','coverage-row'),summary=el('summary');summary.append(el('strong','',`${s.shotId} · ${s.title}`),el('span',s.referenceReady?'coverage-ok':'coverage-gap',s.referenceReady?'参考已覆盖 · 画面待审':'需要补齐'));row.append(summary,el('p','',s.direction));
          [...s.errors,...s.warnings].forEach(message=>row.append(el('p','field-help',message)));
          const refs=el('div','coverage-assets');s.coverage.forEach(r=>{const item=el('div');item.append(el('strong','',`${r.covered?'✓':'缺'} ${r.label}`),el('small','',`${r.mediaType} · ${r.usage}`));if(r.mediaType==='image')r.resolvedAssetIds.forEach(id=>imageReference(assets.get(id),item));item.append(el('p','field-help',r.reason||''));refs.append(item);});row.append(refs);
          if(Object.keys(s.continuityIn||{}).length){const stateTable=el('pre','continuity-state');stateTable.textContent=JSON.stringify({入镜:s.continuityIn,出镜:s.continuityOut},null,2);row.append(stateTable);}
          row.append(button('打开本镜审阅','secondary',()=>{dialog.close();openReview(s.shotId);}));list.append(row);
        });if(!list.childElementCount)list.append(el('p','','当前筛选没有缺口。'));}check.onchange=render;render();
      } else {
        content.append(el('h2','','同一空间，共用母版'),el('p','field-help','这里是拍摄空间约定；反打、日夜和人物尺度需要在首帧中验证。'));
        plan.spaces.forEach(space=>{const row=el('article','space-card');row.append(el('h3','',space.id),el('p','',space.geometryAndAxis));const refs=el('div','coverage-assets');(space.anchorAssetIds||[]).forEach(id=>imageReference(assets.get(id),refs));row.append(refs);content.append(row);});
      }
    }
    selectTab(initial);
  } catch(e){content.replaceChildren(el('p','',e.message));}
}
document.addEventListener('DOMContentLoaded',()=>{
  const actions=document.querySelector('.creative-actions');
  if(actions)actions.prepend(button('导演工作簿','secondary',()=>openDirection()));
});
