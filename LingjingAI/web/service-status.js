'use strict';
document.addEventListener('DOMContentLoaded',()=>{
  const indicator=el('span','local-connection','本机连接中');indicator.id='localConnection';document.querySelector('.top-actions')?.prepend(indicator);
  const banner=el('div','service-banner');banner.hidden=true;banner.append(el('span','','本地服务未连接。双击桌面“镜序 Studio”可启动服务。'),button('重新连接','secondary',()=>checkService(true)));
  document.querySelector('.topbar').after(banner);
  let checking=false,wasOnline=false;
  async function checkService(reload=false){if(checking)return;checking=true;try{
    const response=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(3000)});const health=await response.json();if(!response.ok||!health.ok)throw new Error('service unavailable');
    indicator.textContent='本机 '+health.version;indicator.classList.remove('offline');banner.hidden=true;
    if(reload){if(!state.project)await bootCreator();else if(window.ProductionController)await window.ProductionController.navigate('models');else await reloadProjectData();toast('本地服务已连接');}
    wasOnline=true;
  }catch{indicator.textContent='本机未连接';indicator.classList.add('offline');banner.hidden=false;wasOnline=false;}finally{checking=false;}}
  window.addEventListener('studio:offline',()=>{indicator.textContent='本机未连接';indicator.classList.add('offline');banner.hidden=false;});
  checkService();setInterval(()=>checkService(),15000);
});
