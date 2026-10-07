/* Group-wide denylist; no paid provider calls from this form. */
(()=>{'use strict';const slot=document.getElementById('webEvidenceSlot');if(!slot||document.getElementById('webSourceSettings'))return;
const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
const panel=make('details');panel.id='webSourceSettings';panel.className='research-web-panel';
const heading=make('summary','来源排除设置 · 全组通用'),body=make('div');body.className='research-web-body';panel.append(heading,body);
const label=make('label','不允许搜索或读取的网站（每行一个）'),input=make('textarea');input.id='webDeniedDomains';input.rows=5;input.maxLength=20000;input.spellcheck=false;input.placeholder='example.com\n不填写则允许其它公开网站';label.htmlFor=input.id;input.setAttribute('aria-label','不允许搜索或读取的网站');
const help=make('p','排除名单会同时限制搜索结果与正文读取，并匹配子域名。支持粘贴网址，保存时提取域名。留空表示不排除网站；本机、内网与非公开 HTTPS 限制仍有效。原 config.yaml 的来源白名单不再用于本工具。');
const actions=make('div');actions.className='row';const reload=make('button','读取当前设置'),save=make('button','保存来源排除设置');for(const b of [reload,save]){b.type='button';b.className='secondary';actions.append(b);}
const status=make('p','展开后读取设置；只有管理员可保存，全组成员均可查看。');status.id='webSourceSettingsStatus';status.setAttribute('role','status');body.append(help,label,input,actions,status);slot.prepend(panel);
let revision=null,editable=false,busy=false,dirty=false;
function lock(){input.disabled=busy||!editable||!revision;save.disabled=busy||!editable||!revision;reload.disabled=busy;}
input.addEventListener('input',()=>{dirty=true;status.textContent='已编辑，尚未保存。保存后将影响全组三人的后续工具请求。';});
async function load(){if(busy)return;if(dirty&&!window.confirm('重新读取将放弃尚未保存的网站列表，继续吗？'))return;busy=true;lock();try{const data=await window.api('/api/team/web-source-policy',undefined,{timeoutMs:5000,retries:0});revision=data.revision;editable=!!data.can_edit;input.value=(data.deny_domains||[]).join('\n');dirty=false;status.textContent=(editable?'管理员可编辑。':'仅可查看；请由管理员修改。')+' 当前排除 '+(data.deny_domains||[]).length+' 个网站。'+(data.notice||'');}catch(e){status.textContent='来源设置读取失败：'+e.message+'；已显示的内容保留。';}finally{busy=false;lock();}}
async function persist(){if(busy||!editable||!revision)return;busy=true;lock();try{const data=await window.api('/api/team/web-source-policy',{deny_domains:input.value,expected_revision:revision},{timeoutMs:5000,retries:0});revision=data.revision;input.value=(data.deny_domains||[]).join('\n');dirty=false;status.textContent=data.notice||'已保存。';}catch(e){status.textContent='来源设置未确认保存：'+e.message+'；编辑内容保留，可重新读取后再保存。';}finally{busy=false;lock();}}
reload.onclick=load;save.onclick=persist;panel.addEventListener('toggle',()=>{if(panel.open&&!revision)load();});lock();window.PhysMasterSourceSettings=Object.freeze({panel,load,save:persist});
})();

/* Per-task web request budgets, separate from model token / fee budgets. */
(()=>{'use strict';const slot=document.getElementById('webEvidenceSlot');if(!slot||document.getElementById('webBudgetSettings'))return;
const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;},taskID=()=>{try{return typeof task==='string'?task:null}catch{return null}};
const panel=make('details'),body=make('div');panel.id='webBudgetSettings';panel.className='research-web-panel';body.className='research-web-body';panel.append(make('summary','联网次数预算 · 当前研究'),body);slot.append(panel);
const info=make('p'),scope=make('p','搜索与正文读取分别计数；所有角色、节点与并行候选共用。与模型 token、费用预算及 Tavily 账户额度不同。缓存命中不增加次数。');
const fields={};for(const [key,label] of [['search','网页搜索次数上限'],['read','来源正文读取次数上限']]){const wrap=make('label',label),input=make('input');input.type='number';input.min='0';input.max='1000';input.step='1';input.id='webBudget-'+key;input.setAttribute('aria-label',label);wrap.htmlFor=input.id;wrap.append(input);body.append(wrap);fields[key]=input;}
const help=make('p','0 表示不允许新的联网请求；提高上限会允许更多可能付费的请求，但不会重置已用次数或自动重试。新条件版本分别计数。');const actions=make('div');actions.className='row';const reload=make('button','读取次数与上限'),save=make('button','保存联网上限');for(const b of [reload,save]){b.type='button';b.className='secondary';actions.append(b);}
const status=make('p','选择研究任务后展开查看；任务所有者或管理员可修改。');status.setAttribute('role','status');body.prepend(info,scope);body.append(help,actions,status);
let selected=null,generation=0,editable=false,ready=false,busy=false,dirty=false;
function lock(){for(const e of Object.values(fields))e.disabled=busy||!editable||!ready;save.disabled=busy||!editable||!ready;reload.disabled=busy||!taskID();}
function reset(){selected=taskID();generation++;editable=false;ready=false;dirty=false;busy=false;info.textContent='';for(const e of Object.values(fields))e.value='';status.textContent=selected?'展开后读取联网预算。':'请先选择研究任务。';lock();}
async function load(){if(selected!==taskID())reset();const id=selected;if(!id||busy)return;if(dirty&&!window.confirm('重新读取将放弃尚未保存的次数设置，继续吗？'))return;const seq=generation;busy=true;lock();try{const data=await window.api('/api/tasks/'+id+'/web-budget',undefined,{timeoutMs:5000,retries:0});if(seq!==generation||id!==taskID())return;editable=!!data.can_edit;ready=true;for(const [key,e] of Object.entries(fields))e.value=data.limits[key];dirty=false;info.textContent=(data.current||[]).map(r=>(r.action==='search'?'搜索':'正文')+' '+r.used+'/'+r.max_requests+' 次（剩余 '+r.remaining+'）').join(' · ')+' · 条件版本 '+data.revision;scope.textContent=data.scope;status.textContent=editable?'可调整上限；已用次数保留。':'共享任务：只有所有者或管理员可以调整。';}catch(e){if(seq===generation)status.textContent='联网预算读取失败：'+e.message+'；原内容保留。';}finally{if(seq===generation){busy=false;lock();}}}
async function persist(){if(selected!==taskID()){reset();return;}const id=selected,seq=generation;if(!id||busy||!ready||!editable)return;const limits={};for(const [key,e] of Object.entries(fields)){const value=Number(e.value);if(!e.value.trim()||!Number.isInteger(value)||value<0||value>1000){status.textContent='上限须为 0～1000 的整数。';return;}limits[key]=value;}
busy=true;lock();try{const data=await window.api('/api/tasks/'+id+'/web-budget',limits,{timeoutMs:5000,retries:0});if(seq!==generation||id!==taskID())return;dirty=false;info.textContent=(data.current||[]).map(r=>(r.action==='search'?'搜索':'正文')+' '+r.used+'/'+r.max_requests+' 次（剩余 '+r.remaining+'）').join(' · ');status.textContent=data.notice||'已保存，不重置已用次数。';}catch(e){if(seq===generation)status.textContent='联网预算未确认保存：'+e.message+'；编辑内容保留。';}finally{if(seq===generation){busy=false;lock();}}}
for(const e of Object.values(fields))e.addEventListener('input',()=>{dirty=true;status.textContent='上限已编辑，尚未保存。';});reload.onclick=load;save.onclick=persist;panel.addEventListener('toggle',()=>{if(panel.open)load();});
if(typeof render==='function'){const previous=render;render=function(data){previous(data);if(selected!==taskID()){reset();if(panel.open)load();}};}
document.getElementById('newTask')?.addEventListener('click',reset);reset();window.PhysMasterWebBudget=Object.freeze({panel,load,save:persist});
})();
