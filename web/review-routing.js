/* Independent review/repair UI extension. Existing account and domain UI is retained. */
(() => {
 'use strict';
 const routes={reevaluate:'待评审：补齐证据并重新评审原成果',report_repair:'报告修订：对照已验收数据修订报告',research_revision:'科学修订：修复受影响的模型或验证',repair_then_retry:'工程修复：修正交付、执行或程序核对问题',accept:'验收通过'};
 const originalReasons=window.acceptanceReasons;
 window.acceptanceReasons=function(node){
  const ev=node.evaluation||{};
  if(ev.acceptance_state==='review_pending')return ['评审读取、引用或协议未完成；保留原始成果，不自动重算模型'];
  const reasons=originalReasons(node);
  if(Array.isArray(ev.blocking_issue_details))return reasons.map(x=>/^有 \d+ 项/.test(x)?'有 '+ev.blocking_issue_details.length+' 项阻断问题（已去重）':x);
  return reasons;
 };
 const originalState=window.nodeState;
 window.nodeState=function(node,result){
  const ev=node.evaluation||{};
  if(ev.acceptance_state==='review_pending')return ['待评审 · 原成果保留','warn'];
  if(ev.repair_route==='report_repair'&&ev.integrity_audit?.status==='pass')return ['需要修订报告','warn'];
  return originalState(node,result);
 };
 const originalCard=window.buildNodeCard;
 window.buildNodeCard=function(node,...args){
  const card=originalCard(node,...args),ev=node.evaluation||{};
  if(!ev.repair_route)return card;
  const panel=document.createElement('section');panel.className='repair-panel review-route-panel';panel.setAttribute('aria-label','验收阻断与修复分流');
  const title=document.createElement('h4');title.textContent='验收阻断与修复分流';panel.append(title);
  const route=document.createElement('p');route.textContent=routes[ev.repair_route]||ev.repair_route;panel.append(route);
  if(ev.acceptance_state==='review_pending'){
   const note=document.createElement('p');note.className='muted';note.textContent='评审重试额度已用尽或已关闭。此状态不代表科学结论失败，也不代表已验收；原节点文件、分数与评审记录保留。';panel.append(note);
  }
  const issues=ev.blocking_issue_details||[];
  if(issues.length){
   const fold=document.createElement('details');fold.dataset.viewKey='review-routing-issues';
   const summary=document.createElement('summary');summary.textContent='阻断明细 · '+issues.length+' 项（已去重）';fold.append(summary);
   for(const issue of issues){
    const row=document.createElement('p');row.textContent=(issue.category==='review_protocol'?'评审读取／协议':issue.category==='integrity'?'程序核对':'实质问题')+' · '+({review:'重新评审',report:'报告',summary:'总结',model:'模型',validation:'验证'}[issue.target]||issue.target)+'：'+issue.detail;
    if(issue.aliases?.length){const extra=document.createElement('small');extra.className='muted';extra.textContent='同一问题的其他记录：'+issue.aliases.join('；');row.append(document.createElement('br'),extra);}
    fold.append(row);
   }
   panel.append(fold);
  }
  if(ev.advisory_issues?.length){const note=document.createElement('p');note.className='muted';note.textContent='已披露的限制（单独列示）：'+ev.advisory_issues.map(x=>x.detail).join('；');panel.append(note);}
  (card.querySelector('.node-body')||card.querySelector('.node-expander')||card).append(panel);
  const old=args[1]?.querySelector?.('[data-view-key="review-routing-issues"]');
  if(old)panel.querySelector('details')?.setAttribute('open',old.open?'':'false');
  if(old&&!old.open)panel.querySelector('details')?.removeAttribute('open');
  return card;
 };
 const originalScope=window.renderValidationScope;
 window.renderValidationScope=function(report,data){
  originalScope(report,data);
  const audit=report?.final_consistency;
  if(audit?.repair_route){const line=document.createElement('p');line.textContent='后续处理：'+(routes[audit.repair_route]||audit.repair_route);document.getElementById('validationScope').append(line);}
  if(report?.stop_reason==='review_pending'){const line=document.createElement('p');line.textContent='搜索停止原因：原成果待评审；未另建模型或报告节点绕过验收。';document.getElementById('validationScope').append(line);}
  if(report?.node_review_status==='review_pending'){const line=document.createElement('p');line.textContent='节点评审：待评审，最终一致性签收顺延。';document.getElementById('validationScope').append(line);}
 };
})();
