/* PHY_FORCE_STOP_V963: task-specific emergency controls; no provider calls. */
(()=>{'use strict';
const get=id=>document.getElementById(id),taskID=()=>{try{return typeof task==='string'?task:null}catch{return null}};
const actions=document.createElement('div');actions.className='row force-stop-actions';
const button=document.createElement('button');button.id='forceStopResearch';button.type='button';button.className='secondary';button.textContent='强制停止研究';button.hidden=true;
const note=document.createElement('p');note.id='forceStopNotice';note.className='muted';note.setAttribute('role','status');note.setAttribute('aria-live','polite');note.hidden=true;
actions.append(button);get('researchIdentity').after(actions,note);
const style=document.createElement('style');style.textContent='#forceStopResearch{color:var(--phy-red,#bb3544);border-color:currentColor}.force-stop-actions:has(button[hidden]){display:none}';document.head.append(style);
const active=new Set(['starting','running','paused','replanning','summarizing','reconnecting','stopping']);let displayed=null,pending=null,timer=null,pollBusy=false,viewSeq=0;
states.stopping='正在强制停止';
function clearTimer(){if(timer){clearTimeout(timer);timer=null}}
function renderStop(data){
 const id=taskID();if(id!==displayed){viewSeq++;displayed=id;pending=null;clearTimer();note.hidden=true}
 const own=data&&data.id===id;button.hidden=!own||!active.has(data.state)||data.can_edit===false;
 button.disabled=!own||data.state==='stopping'||pending===id;button.textContent=data?.state==='stopping'?'正在终止研究进程…':'强制停止研究';
 if(own&&data.force_stop?.state==='stopped'&&data.state==='interrupted'){
  get('status').textContent='已强制停止';note.hidden=false;note.textContent='已停止研究及计算子进程；保留已落盘成果。可用“恢复中断研究”从安全断点继续，未完成的请求或计算需要重做。';
 }else if(own&&data.force_stop?.state==='failed'){
  note.hidden=false;note.textContent='尚未确认停止成功：'+(data.force_stop.error||'请重试或检查服务器日志');
 }else if(own&&data.state==='stopping'){
  note.hidden=false;note.textContent='正在核对并终止该任务的研究进程及计算子进程；尚未确认停止成功。';
  for(const key of ['send','update','pause','resume','recoverResearch'])get(key).disabled=true;
  schedule(id);
 }else if(pending!==id){note.hidden=true}
}
function schedule(id){if(timer||pollBusy||taskID()!==id)return;const seq=viewSeq;timer=setTimeout(()=>{timer=null;poll(id,seq)},800)}
async function poll(id,seq){
 if(taskID()!==id||seq!==viewSeq)return;pollBusy=true;
 try{
  const data=await api('/api/tasks/'+id,undefined,{headers:{'X-PhysMaster-View':'progress'},timeoutMs:8000,retries:0});
  if(taskID()!==id||seq!==viewSeq)return;
  taskData={...(taskData?.id===id?taskData:{}),...data};render(taskData);
  if(data.state==='stopping')schedule(id);else{pending=null;await refresh(true)}
 }catch(e){if(taskID()===id&&seq===viewSeq){note.hidden=false;note.textContent='停止状态暂时读取失败；没有确认停止成功。将继续核对。';}}
 finally{pollBusy=false;if(taskID()===id&&seq===viewSeq&&(pending===id||taskData?.state==='stopping'))schedule(id)}
}
async function stop(){
 const id=taskID();if(!id||button.disabled)return;
 const name=get('researchIdentity').textContent||id.slice(0,8);
 if(!confirm('强制停止“'+name+'”？这会立即终止研究及计算子进程。已落盘文件保留，但未完成的模型请求、计算和未保存的进度可能丢失；已发送的 API 请求可能仍计费。'))return;
 if(taskID()!==id)return;
 const seq=viewSeq;pending=id;button.disabled=true;note.hidden=false;note.textContent='正在提交强制停止请求…';
 try{
  const result=await api('/api/tasks/'+id+'/force-stop',{confirm:true},{timeoutMs:10000});
  if(taskID()!==id||seq!==viewSeq)return;
  if(result.state==='stopping'){
   taskData={...(taskData?.id===id?taskData:{}),id,state:'stopping',force_stop:result.force_stop};taskState='stopping';renderStop(taskData);get('status').textContent=states.stopping;schedule(id);
  }else{pending=null;await refresh(true)}
 }catch(e){
  if(taskID()!==id||seq!==viewSeq)return;
  note.hidden=false;note.textContent='未确认强制停止：'+e.message+'。将读取任务状态；不要把请求超时当作停止成功。';
  if(['timeout','network'].includes(e.kind)){schedule(id)}else{pending=null;button.disabled=false;}
 }
}
button.onclick=stop;
const before=render;render=function(data){before(data);renderStop(data)};
const empty=renderEmpty;renderEmpty=function(...args){empty(...args);renderStop(null)};
get('history').addEventListener('change',()=>renderStop(null));get('newTask').addEventListener('click',()=>renderStop(null));
const release=document.querySelector('.release-tag');if(release)release.textContent='v9.6.3';
const footer=document.querySelector('footer');if(footer)footer.textContent=footer.textContent.replace(/v9\.6\.[12]/g,'v9.6.3');
const info=document.createElement('p');info.className='muted';info.textContent='网页中文翻译固定使用 deepseek-flash，沿用你的个人 API 地址和密钥；研究角色模型按原设置运行。';get('profileHint').before(info);
renderStop(typeof taskData==='object'?taskData:null);
window.PhysMasterForceStop=Object.freeze({version:'v9.6.3-flash-force-stop-1',render:renderStop,stop,button,note});
})();
