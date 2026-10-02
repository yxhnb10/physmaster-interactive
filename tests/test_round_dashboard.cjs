// Development dependency: npm install jsdom; then node tests/test_round_dashboard.cjs
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.PHY_JSDOM_PATH||'jsdom');
const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const errors=[],requests=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
    w.setInterval=()=>0;
    w.fetch=async url=>{requests.push(url);return {ok:true,json:async()=>url==='/api/capabilities'?{defaults:{},options:[],critic_defaults:{accept_threshold:.85,redraft_threshold:.6,retrieval_threshold:.5,retrieval_enabled:false}}:{tasks:[]}};};
}});
const w=dom.window,d=w.document;
const nodes=[
 {node_id:18,parent_id:16,subtask_id:2,node_type:'revise',stage:'finished',status:'completed',goal:'修正米与千米转换，校验总质量。',
  result:JSON.stringify({core_results:'模型统一使用米，主参数 total_mass=190 kg。轨迹文件已生成；最大高度尚未计算。'}),
  evaluation:{reward:.72,score_valid:true,decision:'to_revise',model_decision:'to_revise',verdict:'refine',blocking_issues:['缺少收敛检查。'],opinion:'单位问题已修正，但不能据此认定最终最大高度。',integrity_audit:{status:'pass',issues:[],unchecked:[]}},
  knowledge:'[UNACCEPTED ATTEMPT — do not reuse as a verified conclusion]\n米与千米转换应集中在大气模型入口；下一步补齐收敛检查。',tools:[{tool:'Python_code_interpreter',arguments:{code:'print(190)'}}],artifacts:[{path:'node_18/model.py',bytes:29000}]},
 {node_id:19,parent_id:16,subtask_id:2,node_type:'revise',stage:'finished',status:'completed',
  result:JSON.stringify({delivery_status:'failed',delivery_error:{reason:'empty_response'}}),
  evaluation:{reward:0,score_valid:true,decision:'to_redraft',model_decision:'to_redraft',verdict:'reject',opinion:'最终回答为空，缺少可评审的成果声明。',integrity_blocked:true,integrity_audit:{status:'unchecked',unchecked:['缺少主参数证据。']}},
  knowledge:'文件写出后仍需明确交付结果和证据。',tools:[],artifacts:[{path:'node_19/model.py',bytes:1200}]},
 {node_id:20,parent_id:16,subtask_id:2,node_type:'draft',stage:'finished',status:'completed',
  result:{core_results:'返回了初步模型。'},evaluation:{reward:0,score_valid:false,decision:'to_revise',policy_reason:'评分缺失或无效。'},knowledge:'',tools:[],artifacts:[]},
 {node_id:21,parent_id:18,subtask_id:2,node_type:'revise',stage:'finished',status:'completed',
  result:{core_results:'通过当前子任务的单位、参数与基础执行核对。后续热约束仍需独立研究。'},evaluation:{reward:.88,score_valid:true,decision:'complete',verdict:'accept',opinion:'当前子任务的成果和证据充分。',blocking_issues:[],integrity_audit:{status:'pass'}},knowledge:'模型入口单位和代码参数可以通过明确证据核对。',tools:[],artifacts:[]},
 {node_id:22,parent_id:18,subtask_id:2,node_type:'draft',stage:'solving',status:'open',result:null,evaluation:null,knowledge:''},
 {node_id:23,parent_id:18,subtask_id:2,node_type:'draft',stage:'finished',result:{core_results:'旧记录。'},evaluation:{reward:.99,decision:'complete'},knowledge:'旧知识。'},
];
const rounds=[{round_index:0,round:1,revision:0,state:'running',started_at:Date.now()/1000,goal:'本轮任务\n'+('检查单位与主参数。\n'.repeat(60)),nodes}];
function load(data){w.eval('task="'+'a'.repeat(32)+'"; taskState="running"; roundData='+JSON.stringify(data));d.getElementById('roundSelect').value='latest';w.displayRound();}
const cards=()=>Array.from(d.querySelectorAll('.node-card'));
(async()=>{try{
 await new Promise(r=>setImmediate(r));
 load(rounds);
 assert.equal(cards().length,6);
 assert.equal(cards()[0].querySelector('.node-score strong').textContent,'0.72');
 assert.equal(cards()[1].querySelector('.node-score strong').textContent,'0.00');
 assert.match(cards()[1].textContent,/交付失败/);
 assert.equal(cards()[2].querySelector('.node-score strong').textContent,'未评分');
 assert.equal(cards()[2].querySelector('[role="meter"]'),null);
 assert.match(cards()[3].textContent,/本节点已验收/);
 assert.match(cards()[4].textContent,/求解中/);
 assert.match(cards()[5].textContent,/历史评分 · 有效性未标记/);
 assert.equal(cards()[5].querySelector('[role="meter"]'),null);
 assert.deepEqual(Array.from(d.querySelectorAll('.round-metric strong')).map(x=>x.textContent),['6','1','3','2']);
 assert.match(d.querySelector('.round-guide').textContent,/0.53/); // Legacy .99 and invalid 0 excluded.
 assert.match(cards()[0].querySelector('[data-view-key="work"]').textContent,/190 kg/);
 assert.match(cards()[0].querySelector('[data-view-key="critic"]').textContent,/缺少收敛/);
 assert.match(cards()[0].querySelector('.node-panel:nth-child(3)').textContent,/未验收经验/);
 assert.doesNotMatch(cards()[0].querySelector('[data-view-key="knowledge"]').textContent,/UNACCEPTED/);
 assert.equal(cards()[0].querySelector('.node-file').getAttribute('href'),'/api/tasks/'+'a'.repeat(32)+'/artifact?path=node_18%2Fmodel.py');
 assert.ok(cards().every(c=>!c.querySelector('.node-details').open));
 assert.ok(cards().every(c=>!c.querySelector('.node-expander').open));
 const toggle=cards()[0].querySelector('.node-card-header');
 assert.equal(toggle.tagName,'SUMMARY');
 assert.doesNotMatch(toggle.textContent,/单位问题已修正/);
 toggle.click();assert.equal(cards()[0].querySelector('.node-expander').open,true);
 toggle.click();assert.equal(cards()[0].querySelector('.node-expander').open,false);
 assert.equal(d.querySelector('#logs').closest('details').open,false);
 console.log('PASS: four node panels, valid zero vs unscored, pending and legacy states, acceptance and mean, downloads, collapsed raw records.');

 const first=cards()[0],goal=d.querySelector('.round-goal');
 first.querySelector('.node-expander').open=true;
 first.querySelector('.node-details').open=true;
 first.querySelector('[data-view-key="critic"]').scrollTop=47;
 goal.scrollTop=70;goal.focus();w.displayRound();
 assert.equal(cards()[0],first);
 assert.equal(d.querySelector('.round-goal'),goal);
 assert.equal(goal.scrollTop,70);assert.equal(d.activeElement,goal);
 rounds[0].nodes[0].knowledge+='\n补充经验。';load(rounds);
 assert.ok(cards()[0].querySelector('.node-details').open);
 assert.ok(cards()[0].querySelector('.node-expander').open);
 assert.equal(cards()[0].querySelector('[data-view-key="critic"]').scrollTop,47);
 const work=cards()[0].querySelector('[data-view-key="work"]');work.focus();rounds[0].nodes[0].result=JSON.stringify({core_results:'新成果'});load(rounds);
 assert.equal(d.activeElement.dataset.viewKey,'work');
 console.log('PASS: native node fold/unfold, default compact headers, polling preserves both detail levels, panel scroll, task scroll and focus.');

 const mismatch=(evaluation)=>({...nodes[0],evaluation});
 const violation=mismatch({reward:.78,score_valid:true,model_decision:'to_revise',decision:'to_redraft',verdict:'reject',integrity_blocked:true,
    critic_policy:{redraft_threshold:.6,accept_threshold:.85},integrity_audit:{status:'violation',issues:['声称的文件未核实：model.py；文件为空或位于本节点目录之外']}});
 load([{...rounds[0],nodes:[violation]}]);
 assert.match(cards()[0].querySelector('.node-card-header').textContent,/决定已调整/);
 assert.match(cards()[0].querySelector('.decision-note').textContent,/Critic 原始建议：需要修订；规则处理后决定：需要重做/);
 assert.match(cards()[0].querySelector('.decision-note').textContent,/程序核对发现违规/);
 assert.match(cards()[0].querySelector('.decision-note').textContent,/model.py/);
 assert.doesNotMatch(cards()[0].querySelector('.decision-note').textContent,/低于.*重做阈值/);
 const low=mismatch({reward:.4,score_valid:true,model_decision:'to_revise',decision:'to_redraft',critic_policy:{redraft_threshold:.6},integrity_audit:{status:'pass'}});
 load([{...rounds[0],nodes:[low]}]);assert.match(cards()[0].querySelector('.decision-note').textContent,/0.40 低于.*0.6/);
 const unchecked=mismatch({reward:.9,score_valid:true,model_decision:'complete',decision:'to_revise',integrity_blocked:true,
    integrity_audit:{status:'unchecked',unchecked:['缺少参数证据']}});
 load([{...rounds[0],nodes:[unchecked]}]);assert.match(cards()[0].querySelector('.decision-note').textContent,/缺少参数证据/);
 const policy=mismatch({reward:0,score_valid:false,model_decision:'complete',decision:'to_revise',policy_reason:'评分缺失或无效，不能认定完成。'});
 load([{...rounds[0],nodes:[policy]}]);assert.match(cards()[0].querySelector('.decision-note').textContent,/评分缺失或无效/);
 const unknown=mismatch({reward:.2,model_decision:'to_revise',decision:'to_redraft'});
 load([{...rounds[0],nodes:[unknown]}]);assert.match(cards()[0].querySelector('.decision-note').textContent,/没有提供明确/);
 assert.doesNotMatch(cards()[0].querySelector('.decision-note').textContent,/低于.*0.6/);
 load([{...rounds[0],nodes:[nodes[0]]}]);assert.equal(cards()[0].querySelector('.decision-note'),null);
 const missingOriginal=mismatch({reward:.78,score_valid:true,decision:'to_revise'});
 load([{...rounds[0],nodes:[missingOriginal]}]);assert.match(cards()[0].querySelector('.node-panel:nth-child(2)').textContent,/Critic 原始建议：未记录/);
 console.log('PASS: disagreement reasons distinguish thresholds, audited file violations, unchecked evidence and invalid scores; absent reasons are not guessed.');
 const corrected={...nodes[0],result:{core_results:'成果路径已修正。',path_corrections:[{
    declared_path:'query/model.py',node_path:'model.py'}]}};
 load([{...rounds[0],nodes:[corrected]}]);
 assert.match(cards()[0].querySelector('[data-view-key="audit"]').textContent,/程序已核实并修正文件声明/);
 assert.match(cards()[0].querySelector('[data-view-key="audit"]').textContent,/query\/model.py → 当前节点 model.py/);
 load(rounds);

 const injection='<img src=x onerror="window.compromised=1">';
 rounds[0].nodes[0].result=JSON.stringify({core_results:injection});rounds[0].nodes[0].knowledge=injection;rounds[0].nodes[0].evaluation.opinion=injection;load(rounds);
 assert.match(cards()[0].textContent,/<img/);assert.equal(cards()[0].querySelector('img'),null);assert.equal(w.compromised,undefined);
 console.log('PASS: model outputs and Critic text remain escaped plain text.');

 const second={...rounds[0],round_index:1,round:2,revision:1,goal:'新的任务',nodes:[]};load([second]);
 assert.equal(d.querySelector('.round-goal').scrollTop,0);assert.equal(cards().length,0);assert.match(d.getElementById('roundResults').textContent,/正在派发节点/);
 load([]);assert.match(d.getElementById('roundResults').textContent,/暂无本轮节点记录/);
 console.log('PASS: switching rounds resets the task scroller and handles empty node/round history.');

 load(rounds);
 const failed={...nodes[0],node_id:99,stage:'failed',evaluation:{reward:0,analysis:'Worker failed'}};
 load([{...rounds[0],nodes:[failed]}]);assert.equal(cards()[0].querySelector('.node-score strong').textContent,'未评分');assert.match(cards()[0].textContent,/执行失败/);
 const noResult={...nodes[0],result:'non-JSON output',knowledge:''};load([{...rounds[0],nodes:[noResult]}]);assert.match(cards()[0].textContent,/未解析为结构化成果/);
 console.log('PASS: execution failures and non-JSON output are not displayed as accepted numerical results.');

 let resolveFetch;
 w.fetch=()=>new Promise(resolve=>{resolveFetch=resolve});
 const pending=w.refreshRounds();w.eval('task="'+'b'.repeat(32)+'"');
 resolveFetch({ok:true,json:async()=>({rounds:[{...rounds[0],goal:'STALE RESPONSE'}]})});await pending;
 assert.doesNotMatch(d.getElementById('roundResults').textContent,/STALE RESPONSE/);
 assert.deepEqual(errors,[]);
 console.log('PASS: late responses from a previous task cannot overwrite the selected task.');
 console.log('Round dashboard DOM verification passed (7 groups, including path-correction display). No live LLM calls.');
}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1});

// Fixtures are illustrative interface examples, not PhysMaster run evidence.
module.exports={nodes,rounds};
