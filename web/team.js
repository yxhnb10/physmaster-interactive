(()=>{
const user=window.PHY_TEAM.user, bar=$('teamBar');bar.hidden=false;
$('memberName').textContent=user.username+(user.admin?' · 管理员':' · 成员');
const originalApi=api;
api=async function(...args){try{return await originalApi(...args)}catch(e){if(e.status===401){$('sessionExpired').hidden=false;$('teamStatus').textContent='登录已失效';}throw e}};
$('loginAgain').onclick=()=>location.reload();
$('signOut').onclick=async()=>{if($('update').value.trim()&&!confirm('还有未发送的条件，退出前请先复制保存。仍要退出吗？'))return;try{await api('/api/team/logout',{});location.reload()}catch(e){error(e)}};
let apiReady=false;
function readiness(){if(!task)$('start').disabled=!capsLoaded||!apiReady}
const profileDialog=$('profileDialog'), fields=['profileUrl','profileModel','profileKey','profileSearchKey'];
$('openProfile').onclick=async()=>{try{const p=await api('/api/team/profile');$('profileUrl').value=p.base_url;$('profileModel').value=p.model;$('profileOverrides').value=JSON.stringify(p.model_overrides,null,2);$('profileKey').value='';$('profileSearchKey').value='';$('deleteModelKey').checked=false;$('deleteSearchKey').checked=false;$('profileHint').textContent=(p.has_api_key?'模型密钥已保存；留空可保留。':'尚未保存模型密钥。')+(p.has_search_key?' 搜索密钥已保存。':' 尚未保存搜索密钥。')+' 允许的 API 来源：'+p.allowed_origins.join('、');$('profileMessage').textContent='';profileDialog.showModal()}catch(e){error(e)}};
$('closeProfile').onclick=()=>{profileDialog.close();fields.slice(2).forEach(id=>$(id).value='')};
profileDialog.addEventListener('close',()=>fields.slice(2).forEach(id=>$(id).value=''));
$('saveProfile').onclick=async()=>{const b=$('saveProfile');b.disabled=true;try{const p=await api('/api/team/profile',{base_url:$('profileUrl').value,model:$('profileModel').value,api_key:$('profileKey').value,search_key:$('profileSearchKey').value,delete_api_key:$('deleteModelKey').checked,delete_search_key:$('deleteSearchKey').checked,model_overrides:JSON.parse($('profileOverrides').value||'{}')});apiReady=p.has_api_key;readiness();$('profileMessage').textContent='已保存。用于之后提交的任务；运行和排队任务的配置保持不变。';$('profileHint').textContent=(p.has_api_key?'模型密钥已保存。':'未保存模型密钥。')+(p.has_search_key?' 搜索密钥已保存。':' 未保存搜索密钥。');$('profileKey').value='';$('profileSearchKey').value='';$('deleteModelKey').checked=false;$('deleteSearchKey').checked=false}catch(e){$('profileMessage').textContent=e.message}finally{b.disabled=false}};
$('testProfile').onclick=async()=>{const b=$('testProfile');b.disabled=true;try{const data=await api('/api/team/test',{});$('profileMessage').textContent=data.message+(data.models.length?'\n可见模型：'+data.models.join('、'):'')}catch(e){$('profileMessage').textContent=e.message}finally{b.disabled=false}};
$('passwordDialogOpen').onclick=()=>{$('passwordMessage').textContent='';$('passwordDialog').showModal()};
$('passwordClose').onclick=()=>{$('passwordDialog').close();$('oldPassword').value='';$('newPassword').value='';$('newPasswordConfirm').value=''};
$('passwordSave').onclick=async()=>{const b=$('passwordSave');b.disabled=true;try{if($('newPassword').value!==$('newPasswordConfirm').value)throw Error('两次新密码输入不一致');if($('newPassword').value.length<10||$('newPassword').value.length>200)throw Error('新密码长度须为 10～200 个字符');await api('/api/team/password',{current_password:$('oldPassword').value,new_password:$('newPassword').value});location.reload()}catch(e){$('passwordMessage').textContent=e.message}finally{b.disabled=false;$('oldPassword').value='';$('newPassword').value='';$('newPasswordConfirm').value=''}};
let currentJob=null;
const originalRender=render;
render=function(data){originalRender(data);currentJob=data;const own=data.can_edit!==false, queued=data.state==='queued';$('taskActions').hidden=false;$('taskOwner').textContent='所有者：'+(data.owner_id||'旧任务 · 未分配')+(data.shared?' · 已共享给小组':' · 私有');$('shareTask').checked=!!data.shared;$('shareTask').disabled=!own;$('archiveTask').disabled=!own||!['finished','partial','failed','interrupted'].includes(data.state);$('archiveTask').textContent=data.archived?'取消归档':'归档任务';$('cancelQueue').hidden=!queued;$('cancelQueue').disabled=!own;
if(!own||queued){['send','pause','resume','recoverResearch','update','hardParameters','resetCapabilities','loadParameterDefaults','researchTopic','criticAccept','criticRedraft','criticRetrieval','retrievalCriticEnabled'].forEach(id=>$(id).disabled=true);$('capabilities').querySelectorAll('input').forEach(x=>x.disabled=true)}
$('newTask').disabled=!capsLoaded;
if(!own)$('hint').textContent='共享只读视图：你可以查看成果和下载文件。条件、暂停、续研由所有者或管理员操作。';
if(queued){$('status').textContent='排队中 · 第 '+data.queue_position+' 位';$('hint').textContent='等待前面的研究结束。排队时间不计入运行时长；尚未调用模型 API。可取消后重新提交。'}
if(data.cancelled){$('status').textContent='已取消';$('hint').textContent='这项排队研究未启动，可以使用原问题重新提交。'}
};
const originalEmpty=renderEmpty;
renderEmpty=function(...args){originalEmpty(...args);currentJob=null;$('taskActions').hidden=true;readiness();if(!apiReady)$('hint').textContent='开始前，请在顶部“个人 API 设置”保存自己的模型与密钥。'};
$('shareTask').onchange=async()=>{try{await api('/api/tasks/'+task+'/sharing',{shared:$('shareTask').checked});await refresh()}catch(e){$('shareTask').checked=!!currentJob?.shared;error(e)}};
$('archiveTask').onclick=async()=>{try{await api('/api/tasks/'+task+'/archive',{archived:!currentJob.archived});await loadHistory()}catch(e){error(e)}};
$('cancelQueue').onclick=async()=>{if(!confirm('取消这项尚未开始的研究？原问题和已保存目录仍会保留。'))return;try{await api('/api/tasks/'+task+'/cancel',{});await refresh()}catch(e){error(e)}};
loadHistory=async function(){
 const data=await api('/api/tasks'),mode=$('historyScope').value;
 historyNames=new Map(data.tasks.map(j=>[j.id,j.display_name||j.query.slice(0,24)]));
 const rows=data.tasks.filter(j=>mode==='archived'?j.archived:!j.archived&&(mode==='team'||j.owner_id===user.username));
 if(task&&!data.tasks.some(j=>j.id===task)){task=null;newTaskDraft=false}
 if(!task&&!newTaskDraft&&rows.length)task=rows[rows.length-1].id;
 const current=data.tasks.find(j=>j.id===task);if(current&&!rows.some(j=>j.id===task))rows.unshift(current);
 $('history').replaceChildren(new Option('选择研究任务',''));
 for(const j of rows)$('history').add(new Option((j.display_name||j.query.slice(0,24))+' · '+(states[j.state]||j.state)+' · '+(j.owner_id||'旧任务'),j.id));
 $('history').value=task||'';if(!task)renderEmpty(false);await refresh();
};
$('historyScope').onchange=()=>loadHistory().catch(error);
async function workspace(){try{const data=await api('/api/team/workspace');$('teamStatus').textContent=data.active?'正在研究 · '+data.queued+' 项等待':'可启动研究 · '+data.members+' 名成员'}catch(e){$('teamStatus').textContent=e.status===401?'登录已失效':'小组状态暂不可用'}}
workspace();setInterval(workspace,12000);
api('/api/team/profile').then(p=>{apiReady=p.has_api_key;readiness()}).catch(error);
$('query').placeholder='输入问题、已知条件与希望得到的成果。任务默认私有，启动后可共享给小组。';
})();
