/* Read-only translations of displayed research prose; no score or study changes. */
(()=>{
'use strict';
const account=window.PHY_TEAM?.user?.username||'local',key='physmaster.reading.language:'+account;
let chinese=false;try{chinese=localStorage.getItem(key)==='zh';}catch{}
let busy=false,queue=[],current=null;const states=new WeakMap(),records=new Map();
const make=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const taskID=()=>{try{return typeof task==='string'?task:null;}catch{return null;}};
const css=make('style');css.textContent=`
 .translation-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:#fff;border:1px solid var(--line,#dde5ef);padding:12px 16px;border-radius:10px;margin:12px 0}
 .translation-toolbar small{flex-basis:100%;color:#69768a}.translation-toolbar label{display:flex;align-items:center;gap:6px}
 .translation-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:7px 0;color:#69768a;font-size:12px}
 .translation-controls button{padding:4px 9px;min-height:30px;font-size:12px}.translation-status{overflow-wrap:anywhere}
 .translated-view{white-space:pre-wrap;overflow-wrap:anywhere;max-height:440px;overflow:auto;border-left:3px solid #315de7;padding:12px;background:#f7f9fc;color:#253247;border-radius:5px}
 .translated-summary{white-space:normal;max-height:700px}.translation-hidden{display:none!important}
 .translated-view pre{white-space:pre-wrap}.translated-view table{border-collapse:collapse;min-width:100%;margin:12px 0}.translated-view th,.translated-view td{border:1px solid #dce5ef;padding:9px;text-align:left}
 .translated-view .summary-table-scroll{max-width:100%;overflow-x:auto}.translated-view h1{font-size:24px}.translated-view h2{font-size:19px}.translated-view p{line-height:1.75}
 .translated-view .summary-equation{display:block;overflow-x:auto;padding:8px 4px;max-width:100%}
 .translated-view .katex-display{width:max-content;min-width:100%;margin:0;padding:8px 0}
 .translated-view .summary-inline-equation{display:inline-block;max-width:100%;overflow-x:auto;vertical-align:middle}
 .translated-view .math-fallback,.translated-view .math-source{white-space:pre-wrap;overflow-wrap:anywhere}
 @media(max-width:600px){.translation-toolbar{padding:10px}.translation-controls{gap:5px}}
`;document.head.append(css);
const toolbar=make('section','translation-toolbar');toolbar.setAttribute('aria-label','研究内容翻译');
const title=make('strong','','阅读语言'),label=make('label'),toggle=make('input');toggle.type='checkbox';toggle.id='translationChinese';toggle.checked=chinese;
label.append(toggle,document.createTextNode('显示中文译文'));const translatePage=make('button','secondary','翻译当前可见内容'),status=make('span','translation-status');status.setAttribute('role','status');status.id='translationPageStatus';
toolbar.append(title,label,translatePage,status,make('small','','点击翻译才调用当前登录者的默认模型 API，可能产生费用；缓存可复用。译文辅助阅读，原文及评分为准。'));
(document.querySelector('main .workspace')||document.querySelector('main section')||document.querySelector('main')||document.body).prepend(toolbar);
function note(text){status.textContent=text;}
function cacheKey(state){return state.task+'\0'+state.scope+'\0'+state.text;}
function visible(element){if(!element.isConnected)return false;for(let e=element;e;e=e.parentElement){if(e.hidden||getComputedStyle(e).display==='none')return false;if(e.tagName==='DETAILS'&&!e.open&&!e.querySelector('summary')?.contains(element))return false;}return true;}
function show(state){
 const translated=chinese&&(state.translated!==null||state.partial);state.stop.hidden=!state.pending;
 state.source.classList.toggle('translation-hidden',translated);state.view.classList.toggle('translation-hidden',!translated);
 state.button.textContent=state.pending?'翻译中…':state.translated!==null?'显示中文':'翻译成中文';state.button.disabled=!!state.pending;
 state.original.hidden=state.translated===null&&!state.partial;state.download.hidden=state.translated===null;
}
function preview(state,text){
 state.view.replaceChildren();
 if(state.scope==='summary'&&typeof window.renderMarkdownSafe==='function'){
  window.renderMarkdownSafe(text,state.view);
  try{const raw=document.getElementById('summary')?.textContent,target=document.getElementById('summaryMarkup');if(raw&&target)window.renderMarkdownSafe(raw,target);}catch{}
 }else state.view.textContent=text;
 state.partial=true;state.partialText=text;show(state);
}
function paint(state,text,cached=false){
 state.translated=text;state.cached=cached;state.pending=false;preview(state,text);state.partial=false;
 state.message.textContent=(cached?'已复用缓存':'中文翻译完成')+' · 辅助阅读'+(state.model?' · '+state.model:'');records.set(cacheKey(state),{text,cached:true});while(records.size>100)records.delete(records.keys().next().value);show(state);
}
async function stop(state){
 state.stopped=true;queue=queue.filter(s=>s!==state);state.pending=false;show(state);state.message.textContent='已停止等待；原文与已完成内容保留。';
 if(state.jobID){try{await request('/api/tasks/'+state.task+'/translations/'+state.jobID+'/cancel',{});}catch(e){state.message.textContent='停止请求未确认，已提交的翻译可能仍在后台：'+e.message;}}
}

async function request(path,body){
 if(typeof window.api!=='function')throw Error('当前网页接口不兼容');
 return window.api(path,body,{timeoutMs:body===undefined?5000:10000,retries:body===undefined?2:0});
}
async function process(){
 if(busy)return;busy=true;
 try{
 while(queue.length){
  const state=queue.shift();if(!state.source.isConnected||state.task!==taskID()){state.pending=false;continue;}
  current=state;state.message.textContent='提交翻译…';show(state);note('正在翻译；你可以继续查看其他研究内容。');
  try{
   let job=await request('/api/tasks/'+state.task+'/translations',{text:state.text,scope:state.scope});
   state.jobID=job.id;const started=Date.now(),deadline=started+6*60*1000;
   if(state.stopped){await stop(state);throw Error('已停止等待翻译');}
   while(job.state==='queued'||job.state==='running'){
    if(state.stopped)throw Error('已停止等待翻译');
    if(Date.now()>deadline)throw Error('等待翻译超时，原文保留；可稍后重新点击获取结果');
    if(job.partial_text&&job.partial_text!==state.partialText)preview(state,job.partial_text);
    const elapsed=Math.max(0,Math.floor(Date.now()/1000-(job.request_started_at||started/1000)));
    state.message.textContent=(job.state==='queued'?'排队等待':'正在翻译')+' · 已完成 '+job.done+'/'+job.total+' · 已等待 '+elapsed+' 秒 · '+job.model;
    await new Promise(r=>setTimeout(r,1500));
    job=await request('/api/tasks/'+state.task+'/translations/'+job.id);
   }
   if(state.stopped)throw Error('已停止等待翻译');
   if(job.partial_text&&job.partial_text!==state.partialText)preview(state,job.partial_text);
   if(job.state!=='done'||typeof job.text!=='string')throw Error(job.error||'翻译未完成，原文已保留');
   state.model=job.model;
   if(state.source.isConnected&&state.task===taskID())paint(state,job.text,job.cached);
   else records.set(cacheKey(state),{text:job.text,cached:true});
  }catch(e){state.pending=false;state.message.textContent=e.message||'翻译失败，原文已保留，可重试';show(state);note('有内容未翻译成功；原文保留，可点击重试。');}
 }
 }finally{busy=false;current=null;const hasError=[...document.querySelectorAll('.translation-controls .translation-status')].some(e=>/失败|超时|无效|未完成|请检查|请先/.test(e.textContent));note(hasError?'部分内容未翻译成功，可点击相应按钮重试。':'');}
}
function enqueue(state){
 if(state.translated!==null){chinese=true;toggle.checked=true;remember();showAll();return;}
 if(state.pending)return;
 if(!state.task){state.message.textContent='请先选择研究任务';return;}
 if(!window.PHY_TEAM){state.message.textContent='翻译需要使用小组账号登录';return;}
 state.stopped=false;state.pending=true;state.message.textContent='等待翻译';queue.push(state);show(state);process();
}
function remember(){try{localStorage.setItem(key,chinese?'zh':'original');}catch{}}
function showAll(){for(const source of document.querySelectorAll('[data-translation-ready]')){const state=states.get(source);if(state)show(state);}}
function attach(source,scope,text){
 if(!text?.trim()||text.length>40000)return;
 let state=states.get(source);const ident=taskID();
 if(state&&state.text===text&&state.task===ident)return;
 if(state){state.controls.remove();state.view.remove();source.classList.remove('translation-hidden');}
 const controls=make('div','translation-controls'),button=make('button','secondary','翻译成中文'),original=make('button','secondary','显示原文'),download=make('button','secondary','下载译文'),stopButton=make('button','secondary','停止等待'),message=make('span','translation-status');message.setAttribute('role','status');original.hidden=true;download.hidden=true;
 const view=make('div','translated-view translation-hidden'+(scope==='summary'?' translated-summary':''));view.setAttribute('lang','zh-CN');view.tabIndex=0;
 stopButton.hidden=true;controls.append(button,original,download,stopButton,message);source.after(controls,view);source.dataset.translationReady='1';
 state={source,scope,text,task:ident,controls,button,original,download,stop:stopButton,message,view,translated:null,pending:false,partial:false,stopped:false};states.set(source,state);
 stopButton.onclick=()=>stop(state);
 button.onclick=()=>{chinese=true;toggle.checked=true;remember();enqueue(state);showAll();};
 download.onclick=()=>{if(state.translated===null)return;const content='> 中文辅助译文；原始研究、评分与验收以原文为准。\n\n'+state.translated;const url=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));const link=make('a');link.href=url;link.download='PhysMaster-'+state.scope+'-'+state.task.slice(0,8)+'-zh.'+(state.scope==='summary'?'md':'txt');document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);};
 original.onclick=()=>{chinese=false;toggle.checked=false;remember();showAll();};
 const record=records.get(cacheKey(state));if(record)paint(state,record.text,true);show(state);
}
let scheduled=false;
function detach(source){const state=states.get(source);if(state){state.controls.remove();state.view.remove();states.delete(source);source.classList.remove('translation-hidden');delete source.dataset.translationReady;}}
function scan(){scheduled=false;
 if(!taskID()){for(const source of document.querySelectorAll('[data-translation-ready]'))detach(source);return;}
 const summary=document.getElementById('summaryMarkup'),raw=document.getElementById('summary')?.textContent;
 if(summary&&(!summary.textContent||document.getElementById('summaryNotice')?.textContent.includes('旧版总结生成异常')))detach(summary);
 if(summary&&summary.textContent&&raw&&!document.getElementById('summaryNotice')?.textContent.includes('旧版总结生成异常'))attach(summary,'summary',raw.replace(/\n\[网页原文预览已截断；请下载完整总结\]$/,'').slice(0,40000));
 for(const source of document.querySelectorAll('.round-goal,.node-text,.node-brief,.message > div:first-child')){
  if(source.closest('.translation-toolbar,.translation-controls,.translated-view,#profileDialog,#passwordDialog'))continue;
  const scope=source.classList.contains('round-goal')?'goal':source.classList.contains('node-brief')?'brief':source.dataset.viewKey||'message';
  if(['goal','brief','work','critic','knowledge','decision-reason','audit','message'].includes(scope))attach(source,scope,source.textContent);
 }
}
const observer=new MutationObserver(records=>{if(!records.some(r=>!r.target.parentElement?.closest('.translation-controls,.translation-toolbar,.translated-view')&&!r.target.closest?.('.translation-controls,.translation-toolbar,.translated-view')))return;if(!scheduled){scheduled=true;queueMicrotask(scan);}});
observer.observe(document.querySelector('main')||document.body,{subtree:true,childList:true,characterData:true});
toggle.onchange=()=>{chinese=toggle.checked;remember();showAll();if(chinese)note('没有译文的区域请点击“翻译成中文”；此开关只切换已有译文。');else note('已切换到原文。');};
translatePage.onclick=()=>{chinese=true;toggle.checked=true;remember();scan();let n=0;for(const source of document.querySelectorAll('[data-translation-ready]')){const state=states.get(source);if(state&&(visible(source)||visible(state.view))){enqueue(state);n++;}}showAll();if(!n)note('当前页面没有可翻译的研究文本；先选择任务并打开 Summary 或展开节点。');};
document.addEventListener('change',e=>{if(e.target.id==='summaryMathToggle'){const target=document.getElementById('summaryMarkup'),raw=document.getElementById('summary')?.textContent||'',warning=document.getElementById('summaryNotice')?.textContent||'';if(target){if(!taskID())window.renderMarkdownSafe('',target);else if(warning.includes('旧版总结生成异常'))target.textContent=warning;else window.renderMarkdownSafe(raw,target);}for(const source of document.querySelectorAll('[data-translation-ready]')){const state=states.get(source);if(state?.scope==='summary'&&state.translated!==null)paint(state,state.translated,true);}}});
scan();window.PhysMasterTranslation=Object.freeze({scan,version:'v9.5.2-cloud-translation-2'});
})();
