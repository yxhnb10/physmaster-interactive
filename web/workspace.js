/* PHY_WORKSPACE_V961_SCRIPT: layout and navigation only; no new paid API calls. */
(()=>{'use strict';
const get=id=>document.getElementById(id),main=document.querySelector('main'),sidebar=document.querySelector('.workspace-sidebar'),dock=get('conversationDock'),backdrop=get('workspaceBackdrop');document.body.classList.add('workspace-v961');
const header=document.querySelector('header'),team=get('teamBar');if(team)header.append(team);header.querySelector('.release-tag').textContent='v9.6.1';document.querySelector('footer').textContent='PhysMaster 非官方交互扩展 · v9.6.1 研究工作台 · 私网小组使用 · 节点评审不等同于独立科学验证';
const viewKey='physmaster-workspace-view-v961-'+(window.PHY_TEAM?.user?.username||'local');let sideOpen=false,chatWanted=window.innerWidth>1280;
const taskID=()=>{try{return typeof task==='string'?task:null}catch{return null}},small=()=>window.innerWidth<=900,chatOverlay=()=>window.innerWidth<=1280;
function sync(){const id=taskID(),chat=!!id&&chatWanted;main.dataset.mode=id?'research':'new';main.classList.toggle('sidebar-shown',sideOpen);dock.hidden=!chat;sidebar.hidden=small()&&!sideOpen;main.classList.toggle('conversation-hidden',!chat||chatOverlay());backdrop.hidden=!((small()&&sideOpen)||(chatOverlay()&&chat));get('conversationToggle').disabled=!id;get('conversationToggle').setAttribute('aria-expanded',String(chat));get('taskPanelToggle').setAttribute('aria-expanded',String(!sidebar.hidden));get('researchEmptyState').hidden=!!id;}
function toggleSide(open){sideOpen=open;if(open&&chatOverlay())chatWanted=false;sync();if(open)get('closeTaskPanel').focus();}
function toggleChat(open){chatWanted=open;if(open&&small())sideOpen=false;sync();if(open)get('update').focus();}
get('taskPanelToggle').onclick=()=>toggleSide(!sideOpen);get('closeTaskPanel').onclick=()=>toggleSide(false);get('conversationToggle').onclick=()=>toggleChat(!chatWanted);get('closeConversation').onclick=()=>{toggleChat(false);get('conversationToggle').focus()};backdrop.onclick=()=>{sideOpen=false;chatWanted=false;sync()};
window.addEventListener('resize',sync);document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]')){sideOpen=false;chatWanted=false;sync()}});
get('history').addEventListener('change',()=>{sideOpen=false;sync()});get('newTask').addEventListener('click',()=>{get('researchEditor').open=true;sideOpen=small();chatWanted=window.innerWidth>1280;activateView('rounds');sync()});get('beginNewResearch').onclick=()=>{if(!get('newTask').disabled)get('newTask').click();else toggleSide(true)};
const activateBefore=activateView;activateView=function(key){activateBefore(key);try{localStorage.setItem(viewKey,key)}catch{}if(key==='tools'){/* Modules fetch only when their folds are opened. */}};
let first=true;const renderBefore=render;render=function(data){renderBefore(data);if(first&&data.id){get('researchEditor').open=false;first=false}if(data.id&&small())sideOpen=false;get('conversationToggle').textContent='条件对话'+(data.messages?.length?' · '+data.messages.length:'');sync()};
const emptyBefore=renderEmpty;renderEmpty=function(...args){emptyBefore(...args);if(!taskID()){get('researchEditor').open=true;first=true}sync()};
try{const key=localStorage.getItem(viewKey);if(['rounds','delivery','summary','timing','tools','logs'].includes(key))activateView(key)}catch{}sync();
window.PhysMasterWorkspace=Object.freeze({version:'v9.6.1-web-workspace-1',sync,toggleSide,toggleChat});
})();
