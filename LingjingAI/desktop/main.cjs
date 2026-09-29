const {app,BrowserWindow,ipcMain,dialog,shell,Menu}=require('electron');

const path=require('node:path'),fs=require('node:fs'),{spawn}=require('node:child_process');

const config=JSON.parse(fs.readFileSync(path.join(__dirname,'studio-config.json'),'utf8'));

const root=path.resolve(__dirname,config.appRoot),base='http://127.0.0.1:8766';let win,starting=false;

if(process.env.STUDIO_DEBUG_PORT)app.commandLine.appendSwitch('remote-debugging-port',process.env.STUDIO_DEBUG_PORT);

app.setPath('userData',process.env.STUDIO_TEST_PROFILE==='1'?path.join(root,'.runtime/desktop-test-profile'):path.join(app.getPath('appData'),'镜序 Studio'));app.setName('灵镜AI');app.setAppUserModelId('com.jingxu.studio');

const single=app.requestSingleInstanceLock();if(!single)app.quit();

async function health(){try{const r=await fetch(base+'/api/health',{signal:AbortSignal.timeout(1500)});const d=await r.json();return d.ok&&path.resolve(d.appRoot).toLowerCase()===root.toLowerCase()?d:null;}catch{return null;}}

async function startBackend(){if(await health())return;const net=require('node:net');const occupied=await new Promise(resolve=>{const s=net.connect(8766,'127.0.0.1');s.on('connect',()=>{s.destroy();resolve(true);});s.on('error',()=>resolve(false));});if(occupied)throw new Error('8766端口已被其他服务占用，请先处理端口冲突。');const data=path.join(root,'data');fs.mkdirSync(data,{recursive:true});const out=fs.openSync(path.join(data,'desktop-server.log'),'a'),err=fs.openSync(path.join(data,'desktop-server-error.log'),'a');const child=spawn(config.python,[ '-u',path.join(root,'server.py'),'--port','8766'],{cwd:root,detached:true,windowsHide:true,stdio:['ignore',out,err]});child.unref();fs.closeSync(out);fs.closeSync(err);for(let i=0;i<100;i++){if(await health())return;await new Promise(r=>setTimeout(r,200));}throw new Error('后台启动超时，详见data/desktop-server-error.log');}

function validSender(event){try{return new URL(event.senderFrame.url).origin===base;}catch{return false;}}

ipcMain.handle('studio:select-media',async event=>{if(!validSender(event))throw new Error('Unauthorized window');return (await dialog.showOpenDialog(win,{title:'选择照片、声音或视频',properties:['openFile','multiSelections'],filters:[{name:'媒体文件',extensions:['png','jpg','jpeg','webp','mp4','mov','webm','wav','mp3','m4a']}]})).filePaths;});

ipcMain.handle('studio:reveal',async(event,file)=>{if(!validSender(event))throw new Error('Unauthorized window');const p=path.resolve(String(file));if(!p.startsWith(root+path.sep))throw new Error('仅打开工作台文件');shell.showItemInFolder(p);});

async function createWindow(){if(starting)return;starting=true;try{await startBackend();win=new BrowserWindow({width:1560,height:980,minWidth:1040,minHeight:720,title:'灵镜AI',icon:path.join(root,'web/branding/lingjing.ico'),backgroundColor:'#171c1c',webPreferences:{preload:path.join(__dirname,'preload.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,backgroundThrottling:false}});win.webContents.setWindowOpenHandler(({url})=>{if(/^https:\/\//.test(url))shell.openExternal(url);return {action:'deny'};});win.webContents.on('will-navigate',(e,url)=>{if(new URL(url).origin!==base)e.preventDefault();});win.webContents.session.setPermissionRequestHandler((_,permission,callback)=>callback(['clipboard-sanitized-write'].includes(permission)));win.webContents.on('will-prevent-unload',async()=>{const r=await dialog.showMessageBox(win,{type:'question',buttons:['继续编辑','退出'],defaultId:0,cancelId:0,message:'仍有未保存更改或导出任务，是否退出？'});if(r.response===1)win.destroy();});await win.loadURL(base+'/?desktop=1');win.on('closed',()=>win=null);}catch(e){await dialog.showMessageBox({type:'error',title:'灵镜AI启动失败',message:e.message});app.quit();}finally{starting=false;}}

app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.focus();}});

if(single)app.whenReady().then(()=>{Menu.setApplicationMenu(Menu.buildFromTemplate([{label:'文件',submenu:[{label:'重新加载',accelerator:'Ctrl+R',click:()=>win?.reload()},{type:'separator'},{label:'退出',role:'quit'}]},{label:'编辑',submenu:[{label:'撤销文字',role:'undo',accelerator:'',registerAccelerator:false},{label:'重做文字',role:'redo',accelerator:'',registerAccelerator:false},{type:'separator'},{label:'剪切文字',role:'cut',accelerator:'',registerAccelerator:false},{label:'复制文字',role:'copy',accelerator:'',registerAccelerator:false},{label:'粘贴文字',role:'paste',accelerator:'',registerAccelerator:false},{label:'全选文字',role:'selectAll',accelerator:'',registerAccelerator:false}]},{label:'视图',submenu:[{role:'togglefullscreen'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'}]},{label:'帮助',submenu:[{label:'使用指南',click:()=>shell.openPath(path.join(root,'使用指南.md'))},{label:'关于灵镜AI',click:()=>dialog.showMessageBox(win,{message:'灵镜AI 0.12',detail:'本机AI视频创作工作台\n剪辑内核基于MIT授权的FableCut。\n本地生成任务可在后台继续；剪辑导出时请保持窗口打开。'})}]}]));createWindow();});

app.on('window-all-closed',()=>app.quit());

