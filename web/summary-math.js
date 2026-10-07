/* PhysMaster summary math UI, independent of account and research policy patches. */
(() => {
 'use strict';
 const STORAGE_KEY='physmaster.summary.math.enabled', LIMIT=40000;
 let enabled=true, sequence=0, last=null;
 try{enabled=localStorage.getItem(STORAGE_KEY)!=='false';}catch{}
 const element=(tag,cls='',text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
 const escaped=(s,i)=>{let n=0;for(let j=i-1;j>=0&&s[j]==='\\';j--)n++;return n%2===1;};
 const countRun=(s,i,c)=>{let j=i;while(s[j]===c)j++;return j-i;};

 function protect(source){
  let prefix='\uE000PHYMATH'+(++sequence)+'X';while(source.includes(prefix))prefix+='x';
  const entries=[],out=[];let i=0,fence=null;
  const token=(entry)=>{const key=prefix+entries.length+'\uE001';entries.push({...entry,key});return key;};
  while(i<source.length){
   const lineStart=i===0||source[i-1]==='\n';
   if(lineStart){
    const end=source.indexOf('\n',i),stop=end<0?source.length:end+1,line=source.slice(i,stop);
    const match=line.match(/^ {0,3}(`{3,}|~{3,})([^\n]*)/);
    if(fence){
     out.push(line);i=stop;
     if(match&&match[1][0]===fence.char&&match[1].length>=fence.length&&!match[2].trim())fence=null;
     continue;
    }
    if(match){fence={char:match[1][0],length:match[1].length};out.push(line);i=stop;continue;}
   }
   if(source[i]==='`'&&!escaped(source,i)){
    const size=countRun(source,i,'`'),marker='`'.repeat(size);let end=source.indexOf(marker,i+size);
    while(end>=0&&(source[end-1]==='`'||source[end+size]==='`'))end=source.indexOf(marker,end+size);
    if(end>=0){out.push(token({kind:'code',source:source.slice(i+size,end),raw:source.slice(i,end+size)}));i=end+size;continue;}
   }
   let open=null,close=null,display=false;
   if(!escaped(source,i)){
    if(source.startsWith('\\[',i)){open='\\[';close='\\]';display=true;}
    else if(source.startsWith('\\(',i)){open='\\(';close='\\)';}
    else if(source.startsWith('$$',i)){open=close='$$';display=true;}
    else if(source[i]==='$'&&source[i+1]&&!/\s/.test(source[i+1]))open=close='$';
   }
   if(open){
    let end=source.indexOf(close,i+open.length);
    while(end>=0&&(escaped(source,end)||close==='$'&&(source[end-1]&&/\s/.test(source[end-1])||/\d/.test(source[end+1]||'')||source[end+1]==='$')))end=source.indexOf(close,end+close.length);
    const body=end>=0?source.slice(i+open.length,end):'';
    if(end>=0&&body.trim()&&(display||!body.includes('\n'))){
     out.push(token({kind:'math',source:body.trim(),raw:source.slice(i,end+close.length),display}));i=end+close.length;continue;
    }
   }
   out.push(source[i++]);
  }
  return {text:out.join(''),entries,prefix};
 }

 function mathElement(entry,stats,inlineContext=false){
  stats.total++;
  const block=entry.display,node=element(block&&!inlineContext?'div':'span',block?'summary-equation':'summary-inline-equation');
  node.dataset.tex=entry.source;node.title=entry.source;
  if(!enabled){node.classList.add('math-source');node.textContent=entry.raw;return node;}
  try{
   if(!window.katex?.render)throw new Error('公式引擎未加载');
   if(entry.source.length>8000||stats.total>300)throw new Error('公式超出网页排版范围，请查看或下载原文');
   let denied=null;
   window.katex.render(entry.source,node,{displayMode:block,throwOnError:true,trust:context=>{denied=context.command;return false;},strict:'ignore',
    output:'htmlAndMathml',maxSize:8,maxExpand:300,macros:{}});
   // KaTeX intentionally refuses resource/HTML commands even with a parseable input.
   if(denied)throw new Error('此公式命令未启用：'+denied);
   stats.rendered++;
  }catch(error){
   stats.errors++;node.replaceChildren();node.classList.add('math-fallback');
   node.append(element('code','',entry.raw));
   const details=element('details','math-error-detail'),summary=element('summary','','公式未排版，原文已保留');
   details.append(summary,element('span','',String(error?.message||'公式格式无法识别').slice(0,600)));node.append(details);
  }
  if(block){node.tabIndex=0;node.setAttribute('aria-label','独立公式，长公式可横向滚动');}
  return node;
 }

 function plainInline(target,text){
  const parts=text.split(/(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_)/g);
  for(const part of parts){
   if((part.startsWith('**')&&part.endsWith('**'))||(part.startsWith('__')&&part.endsWith('__')))target.append(element('strong','',part.slice(2,-2)));
   else if((part.startsWith('*')&&part.endsWith('*'))||(part.startsWith('_')&&part.endsWith('_')))target.append(element('em','',part.slice(1,-1)));
   else target.append(document.createTextNode(part.replace(/\\([\\`*_$|])/g,'$1')));
  }
 }

 function append(target,text,state,stats,depth=0){
  // Parse an emphasis span before its inner placeholders; TeX remains opaque.
  const emphasis=depth<8?/(\*\*|__)([^\n]+?)\1|(\*|_)([^\n]+?)\3/.exec(text):null;
  const firstToken=text.indexOf(state.prefix);
  if(emphasis&&(firstToken<0||emphasis.index<firstToken)&&!escaped(text,emphasis.index)){
   append(target,text.slice(0,emphasis.index),state,stats,depth+1);
   const strong=!!emphasis[1],node=element(strong?'strong':'em');append(node,strong?emphasis[2]:emphasis[4],state,stats,depth+1);target.append(node);
   append(target,text.slice(emphasis.index+emphasis[0].length),state,stats,depth+1);return;
  }
  let offset=0;
  while(offset<text.length){
   const start=text.indexOf(state.prefix,offset);
   if(start<0){plainInline(target,text.slice(offset));break;}
   plainInline(target,text.slice(offset,start));
   const end=text.indexOf('\uE001',start);
   if(end<0){plainInline(target,text.slice(start));break;}
   const key=text.slice(start,end+1),entry=state.entries[Number(key.slice(state.prefix.length,-1))];
   if(!entry||entry.key!==key)plainInline(target,key);
   else if(entry.kind==='code')target.append(element('code','',entry.source));
   else target.append(mathElement(entry,stats,true));
   offset=end+1;
  }
 }

 function tableCells(line){
  const inner=line.trim().replace(/^\|/,'').replace(/\|$/,'');let cell='',cells=[];
  for(let i=0;i<inner.length;i++){
   if(inner[i]==='|'&&!escaped(inner,i)){cells.push(cell.trim());cell='';}else cell+=inner[i];
  }
  cells.push(cell.trim());return cells;
 }

 function render(text,target){
  last={text,target};target.replaceChildren();
  const state=protect(String(text).slice(0,LIMIT)),lines=state.text.split('\n'),stats={total:0,rendered:0,errors:0};
  let code=null,fence=null,list=null,table=null;
  for(let index=0;index<lines.length;index++){
   const line=lines[index],match=line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
   if(code){
    if(match&&match[1][0]===fence.char&&match[1].length>=fence.length&&!match[2].trim()){target.append(code);code=null;fence=null;}
    else code.textContent+=line+'\n';continue;
   }
   if(match){code=element('pre','');fence={char:match[1][0],length:match[1].length};list=null;table=null;continue;}
   if(!line.trim())continue;
   const whole=state.entries.find(e=>e.kind==='math'&&e.display&&e.key===line.trim());
   if(whole){target.append(mathElement(whole,stats));list=null;table=null;continue;}
   const heading=line.match(/^(#{1,6})\s+(.*)$/);
   if(heading){const h=element('h'+heading[1].length);append(h,heading[2],state,stats);target.append(h);list=null;table=null;continue;}
   if(/^\s*\|.*\|\s*$/.test(line)){
    const cells=tableCells(line);
    if(cells.every(c=>/^:?-{3,}:?$/.test(c)))continue;
    if(!table){table=element('table');const wrap=element('div','summary-table-scroll');wrap.tabIndex=0;wrap.setAttribute('aria-label','研究结果表格，宽表格可横向滚动');wrap.append(table);target.append(wrap);}
    const row=element('tr'),next=lines[index+1]||'',header=/^\s*\|.*\|\s*$/.test(next)&&tableCells(next).every(c=>/^:?-{3,}:?$/.test(c));
    for(const cell of cells){const td=element(header?'th':'td');append(td,cell,state,stats);row.append(td);}table.append(row);list=null;continue;
   }
   table=null;
   const item=line.match(/^\s*(?:([-*+])|(\d+)[.)])\s+(.*)$/);
   if(item){const tag=item[2]?'ol':'ul';if(!list||list.tagName.toLowerCase()!==tag){list=element(tag);if(item[2])list.start=Number(item[2]);target.append(list);}const li=element('li');append(li,item[3],state,stats);list.append(li);continue;}
   list=null;const block=element(line.startsWith('>')?'blockquote':'p');append(block,line.startsWith('>')?line.slice(1).trim():line,state,stats);target.append(block);
  }
  if(code)target.append(code);
  if(String(text).length>LIMIT)target.append(element('p','muted','网页仅展示前 40,000 字符；完整内容请下载原文件。'));
  const status=document.getElementById('summaryMathStatus');
  if(status)status.textContent=!enabled?'公式以原文显示':stats.errors?'已排版 '+stats.rendered+' 个公式；'+stats.errors+' 个公式已保留原文':stats.total?'已排版 '+stats.rendered+' 个公式':'支持行内与独立公式';
  target.dataset.mathErrors=String(stats.errors);target.dataset.mathCount=String(stats.total);
  return stats;
 }

 const css=document.createElement('style');css.textContent=`
 #summaryMarkup .summary-equation{display:block;max-width:100%;overflow-x:auto;overflow-y:hidden;padding:8px 4px;margin:15px 0;overscroll-behavior-x:contain;scrollbar-width:thin}
 #summaryMarkup .summary-equation .katex-display{margin:0;padding:8px 0;width:max-content;min-width:100%}
 #summaryMarkup .summary-inline-equation{display:inline-block;max-width:100%;font-size:1em;overflow-x:auto;overflow-y:hidden;vertical-align:middle;padding:2px 0}
 #summaryMarkup .katex{font-size:1.08em}
 #summaryMarkup .math-source,#summaryMarkup .math-fallback{white-space:pre-wrap;overflow-wrap:anywhere}
 #summaryMarkup .math-fallback{color:#935923;background:#fff8ed;border-radius:6px;padding:5px}
 #summaryMarkup .math-error-detail{font-size:11px;white-space:normal}
 #summaryMarkup .summary-table-scroll{max-width:100%;overflow-x:auto;margin:14px 0;scrollbar-width:thin}
 #summaryMarkup .summary-table-scroll table{min-width:100%;width:auto;margin:0}
 #summaryMarkup .summary-table-scroll th,#summaryMarkup .summary-table-scroll td{min-width:100px}
 #summaryMarkup .summary-table-scroll .katex{white-space:nowrap}
 .summary-math-controls{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:8px 0 16px;font-size:12px;color:#69768a}
 .summary-math-controls label{display:flex;align-items:center;gap:7px}
 @media(max-width:600px){#summaryMarkup .katex{font-size:1em}.summary-math-controls{gap:8px;font-size:11px}}
 `;document.head.append(css);
 const target=document.getElementById('summaryMarkup');
 if(target&&!document.getElementById('summaryMathToggle')){
  const toolbar=element('div','summary-math-controls'),label=element('label'),toggle=element('input');toggle.type='checkbox';toggle.id='summaryMathToggle';toggle.checked=enabled;
  label.append(toggle,document.createTextNode('排版公式'));const status=element('span','','支持行内与独立公式');status.id='summaryMathStatus';status.setAttribute('role','status');toolbar.append(label,status);target.before(toolbar);
  toggle.addEventListener('change',()=>{enabled=toggle.checked;try{localStorage.setItem(STORAGE_KEY,String(enabled));}catch{}if(last&&last.target.isConnected)render(last.text,last.target);});
 }
 window.renderMarkdownSafe=render;
 const originalSummary=window.renderSummary;
 if(typeof originalSummary==='function')window.renderSummary=function(data){
  originalSummary(data);
  const text=data?.summary,broken=typeof text==='string'&&text.includes('## Trajectory')&&!text.includes('## Final Outcome');
  if(typeof text==='string'&&!broken&&(!last||last.text!==text)&&target)render(text,target);
 };
 window.PhysMasterSummaryMath=Object.freeze({version:'v9.5.3.1-summary-math',render,protect});
})();
