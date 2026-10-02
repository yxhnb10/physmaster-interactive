// Actual page, synthetic records; no screenshots or live model claims.
const assert=require('node:assert/strict'),fs=require('fs'),path=require('path');
const {JSDOM,VirtualConsole}=require(process.env.PHY_JSDOM_PATH||'jsdom');
const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8'),{url:'http://127.0.0.1:8765',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){w.setInterval=()=>0;w.fetch=async url=>({ok:true,json:async()=>url==='/api/capabilities'?{defaults:{},options:[],critic_defaults:{accept_threshold:.85,redraft_threshold:.6,retrieval_threshold:.5,retrieval_enabled:false}}:{tasks:[]}});}});
const w=dom.window,d=w.document;
const dim=x=>({score:x,valid:x!==null,applicable:true,basis:'Synthetic receipt'});
const first={node_id:1,parent_id:0,node_type:'draft',subtask_id:1,stage:'finished',status:'completed',research_stage:'SIMULATION',repair_attempt:0,repair_child_id:2,repair_status:'retried',repair_outcome:{node_id:2,accepted:true},result:{analysis:'Failed synthetic code execution'},evaluation:{decision:'to_redraft',model_decision:'complete',score_valid:true,composite_valid:true,composite_reward:.7,science_reward:.9,verdict:'reject',integrity_audit:{status:'violation',issues:['执行失败']},failure_class:'CODE_FAILURE',failure_reasons:['最后一次执行失败'],decision_reasons:['程序证据阻止验收'],score_dimensions:{science:dim(.9),artifact:dim(1),execution:dim(0),compliance:dim(0),communication:dim(1)},reward_weights:{science:.5,artifact:.2,execution:.1,compliance:.15,communication:.05}},review_history:[{attempt:1,valid:false,started_at:1,finished_at:3,error:'invalid protocol'},{attempt:2,valid:true,started_at:3,finished_at:4}],artifacts:[{path:'node_1/model.py',status:'verified',state:'VERIFIED',lifecycle:['CREATED','REGISTERED','VERIFIED']}]};
const second={...structuredClone(first),node_id:2,parent_id:1,repair_parent_id:1,repair_child_id:null,repair_attempt:1,repair_status:'succeeded',repair_outcome:null,review_history:[],result:{analysis:'Corrected code executed'},evaluation:{...structuredClone(first.evaluation),decision:'complete',model_decision:'complete',verdict:'accept',integrity_audit:{status:'pass'},failure_class:'NONE',failure_reasons:[]}};
const report={...structuredClone(second),node_id:3,research_stage:'FINALIZATION',repair_attempt:0,repair_parent_id:null,repair_status:'not_needed'};
function load(){w.eval('task="'+ 'b'.repeat(32)+'";taskState="finished";roundData='+JSON.stringify([{round:1,round_index:0,revision:0,state:'finished',goal:'Synthetic fixtures',nodes:[first,second,report]}]));w.displayRound();}
(async()=>{try{await new Promise(r=>setImmediate(r));load();
assert.equal(d.querySelectorAll('[role=tab]').length,5);assert.equal(d.getElementById('view-rounds').hidden,false);assert.equal(d.getElementById('view-logs').hidden,true);
d.getElementById('tab-delivery').click();assert.equal(d.getElementById('view-delivery').hidden,false);assert.equal(d.getElementById('view-rounds').hidden,true);
d.getElementById('tab-delivery').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(d.getElementById('tab-summary').getAttribute('aria-selected'),'true');
console.log('PASS: actual workspace tabs, keyboard navigation and visibility.');
w.activateView('rounds');assert.equal(d.querySelectorAll('.research-stage').length,5);assert.match(d.querySelector('.research-stages').textContent,/2 个节点/);assert.equal(d.querySelectorAll('.score-dimension').length,15);
const card=d.querySelector('.node-card');assert.equal(card.querySelector('details').open,false);assert.match(card.querySelector('.repair-panel').textContent,/后继节点 2.*原失败结论/);assert.equal(card.querySelectorAll('.repair-history tr').length,3);assert.match(card.querySelector('.decision-note').textContent,/程序证据阻止验收/);assert.match(card.querySelector('.artifact-lifecycle').textContent,/已生成 · 已登记 · 文件已核对/);
console.log('PASS: five dimensions, actual stage, chain outcome, review retries and lifecycle evidence.');
d.getElementById('stageFilter').value='FINALIZATION';d.getElementById('stageFilter').dispatchEvent(new w.Event('change'));assert.equal(d.querySelectorAll('.node-card').length,1);assert.match(d.querySelector('.node-card-header').textContent,/节点 3/);
d.getElementById('stageFilter').value='all';d.getElementById('nodeFilter').value='repairs';w.displayRound();assert.equal(d.querySelectorAll('.node-card').length,2);
d.querySelector('.repair-panel a').click();const repaired=[...d.querySelectorAll('.node-card')].find(c=>c.dataset.nodeKey.endsWith(':2'));assert.equal(repaired.querySelector('details').open,true);
console.log('PASS: stage/status filters and navigable repair links.');
first.evaluation.failure_reasons=['<img src=x onerror=alert(1)>'];first.review_history[0].error='<script>bad</script>';load();assert.equal(d.querySelector('.repair-panel img'),null);assert.equal(d.querySelector('.repair-panel script'),null);assert.deepEqual(errors,[]);
console.log('PASS: repair payloads remain escaped text and page has no DOM runtime errors.');
}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
