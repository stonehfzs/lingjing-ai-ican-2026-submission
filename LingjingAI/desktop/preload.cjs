const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('studioDesktop',{selectMedia:()=>ipcRenderer.invoke('studio:select-media'),reveal:file=>ipcRenderer.invoke('studio:reveal',file)});
window.addEventListener('DOMContentLoaded',()=>{document.documentElement.dataset.desktop='true';});
