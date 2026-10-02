// Run alongside test_round_dashboard.cjs. Fixtures are synthetic UI test data.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require(process.env.PHY_JSDOM_PATH||'jsdom');
const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8'),{
 url:'http://127.0.0.1:8765/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
  w.setInterval=()=>0;w.fetch=async url=>({ok:true,json:async()=>url==='/api/capabilities'?{defaults:{},options:[],critic_defaults:{accept_threshold:.85,redraft_threshold:.6,retrieval_threshold:.5,retrieval_enabled:false}}:{tasks:[]}});
 }});
const w=dom.window,d=w.document;
const dim=(score,basis='synthetic check',applicable=true)=>({score,valid:score!==null,basis,applicable});
const fixture={node_id:2,parent_id:1,subtask_id:2,node_type:'revise',stage:'finished',status:'completed',
 result:{core_results:'Synthetic evidence; no real physics result.'},knowledge:'Revise scientific assumptions.',
 evaluation:{reward:.795,composite_reward:.795,composite_valid:true,science_reward:.59,score_valid:true,
  score_dimensions:{science:dim(.59),artifact:dim(1),compliance:dim(1),communication:dim(1)},
  model_decision:'complete',decision:'to_redraft',verdict:'reject',critic_policy:{accept_threshold:.85,redraft_threshold:.6},
  integrity_audit:{status:'pass'},blocking_issues:['Scientific review incomplete.']},
 failure_categories:[{category:'Review',reason:'Scientific review incomplete.'}],
 artifacts:[{path:'node_2/model.py',bytes:10,status:'verified',sha256:'ab'}],
 timeline:[{stage:'solving',at:100},{stage:'evaluating',at:110},{stage:'finished',at:120}]};
function load(nodes){w.eval('task="'+'a'.repeat(32)+'";taskState="partial";roundData='+JSON.stringify([{round_index:0,round:1,revision:0,state:'finished',goal:'Synthetic research test',nodes}]));w.displayRound();}
(async()=>{try{
 await new Promise(r=>setImmediate(r));load([fixture]);
 const card=d.querySelector('.node-card');
 assert.equal(card.querySelector('.node-expander').open,false);
 assert.match(card.querySelector('.node-score').textContent,/0.80/);
 assert.match(card.querySelector('.node-score').textContent,/科学分 0.59/);
 assert.match(card.querySelector('.decision-note').textContent,/0.59 低于.*0.6/);
 assert.doesNotMatch(card.querySelector('.decision-note').textContent,/0.80 低于/);
 assert.equal(card.querySelectorAll('.score-dimension').length,4);
 assert.match(card.querySelector('.node-file').textContent,/文件已核对，未验收/);
 assert.match(card.querySelector('.node-failures').textContent,/待解决评审问题/);
 console.log('PASS: dimensions, science-based decision reasons, composite search reward and independent acceptance.');

 const incomplete=structuredClone(fixture);incomplete.evaluation.composite_reward=null;incomplete.evaluation.composite_valid=false;
 incomplete.evaluation.science_reward=.95;incomplete.evaluation.score_dimensions.science=dim(.95);
 incomplete.evaluation.score_dimensions.compliance=dim(null);incomplete.evaluation.integrity_audit={status:'unchecked'};
 incomplete.evaluation.decision='to_revise';incomplete.evaluation.reward=0;
 load([incomplete]);assert.match(d.querySelector('.node-score strong').textContent,/未评分/);
 assert.match(d.querySelector('.node-score').textContent,/科学分 0.95/);
 assert.match(d.querySelector('.score-dimensions').textContent,/未评分/);
 assert.doesNotMatch(d.querySelector('.node-card-header').textContent,/已验收/);
 console.log('PASS: missing evidence never displays a fallback zero as a valid composite score.');

 load([fixture]);assert.equal(d.querySelectorAll('#researchTimeline li').length,3);
 assert.match(d.querySelector('#researchTimeline').textContent,/求解中/);
 assert.match(d.querySelector('#researchTimeline').textContent,/评审中/);
 fixture.timeline.push({stage:'published',at:130});fixture.artifacts[0].status='published';fixture.artifacts[0].download_path='final/model.py';load([fixture]);
 assert.match(d.querySelector('#researchTimeline').textContent,/文件已发布/);
 assert.match(d.querySelector('.node-file').textContent,/已发布至 final/);
 assert.ok(d.querySelector('.node-file').getAttribute('href').endsWith('final%2Fmodel.py'));
 console.log('PASS: actual-stage timeline and published artifact labels.');

 w.renderOutcome({id:'a'.repeat(32),completion_report:{status:'partial',stop_reason:'budget',subtasks:[],reasons:[],
  artifacts:[{path:'model.py',download_path:'final/model.py',source:'node_2/model.py',status:'available'}],
  additional_artifacts:[{path:'log.txt',download_path:'final/log.txt',source:'node_2/log.txt',status:'available'}]},
  final_manifest:{status:'partial'},finalization:{state:'partial',reason:'Reviewer rejected',attempts:[{node_id:2}]}});
 const deliverables=d.getElementById('deliverables');
 assert.ok(deliverables.querySelector('a[href$="final%2Fmodel.py"]'));
 assert.ok(deliverables.querySelector('a[href$="final%2Flog.txt"]'));
 assert.ok(deliverables.querySelector('a[href$="/manifest"]'));
 assert.match(deliverables.textContent,/来源 node_2\/model.py/);
 assert.match(deliverables.textContent,/额外节点 1 个/);
 console.log('PASS: provenance, supplementary files, final manifest download and report-attempt status.');

 const malicious=structuredClone(fixture);malicious.failure_categories[0].reason='<img src=x onerror=alert(1)>';
 malicious.evaluation.score_dimensions.science.basis='<script>throw 1</script>';
 load([malicious]);assert.equal(d.querySelector('.node-failures img'),null);assert.equal(d.querySelector('.score-dimensions script'),null);
 assert.deepEqual(errors,[]);console.log('PASS: all new fields remain plain text; no runtime DOM errors.');
}finally{w.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
