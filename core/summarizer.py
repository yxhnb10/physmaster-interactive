"""Bounded scientific summaries with explicit, readable failure status."""
import json
import re
from pathlib import Path
from typing import Any, Dict, List
from utils.llm_client import call_model_without_tools
from utils.live_updates import atomic_json, atomic_text
from utils.research_integrity import parse_output

SECTIONS = ('## Task', '## Key Reasoning Steps', '## Final Outcome', '## Open Issues')
INPUT_BUDGET = 48000  # Character budget, not a claimed token count.


def bounded(value, chars=1200, depth=0):
    if isinstance(value,str):
        if len(value)>chars:return value[:chars]+'\n[excerpt truncated; not complete evidence]'
        return value
    if isinstance(value,list):return [bounded(v,chars,depth+1) for v in value[:20]]
    if isinstance(value,dict):
        if depth>4:return '[nested evidence omitted]'
        return {k:bounded(v,chars,depth+1) for k,v in value.items()
                if k not in ('code','review_attempts','observed_file_evidence','review_history','tools','arguments')}
    return value


def summary_input(task_description, trajectory, budget=INPUT_BUDGET):
    try:
        contract=json.loads(task_description) if isinstance(task_description,str) else task_description
    except (TypeError,ValueError):contract={'task_description':str(task_description)}
    if not isinstance(contract,dict):contract={'task_description':str(contract)}
    completion=contract.get('completion_report') or {}
    selected={s.get('node_id') for s in completion.get('subtasks',[]) if s.get('node_id') is not None}
    nodes=[n for n in trajectory if n.get('node_id') in selected]
    if not nodes:nodes=trajectory[-8:]
    context=[]
    for n in nodes:
        result=parse_output(n.get('result'))
        feedback=n.get('critic_feedback') or n.get('evaluation') or {}
        context.append(dict(node_id=n.get('node_id'),subtask_id=n.get('subtask_id'),
            accepted=any(s.get('node_id')==n.get('node_id') and s.get('status')=='passed' for s in completion.get('subtasks',[])),
            result={k:bounded(result[k],1600) for k in ('core_results','analysis','numerical_results','primary_parameters','files','delivery_status') if k in result},
            review={k:bounded(feedback[k],450) for k in ('decision','reward','blocking_issues','opinion') if k in feedback}))
    source=contract.get('adopted_artifact_evidence') or {}
    evidence=[{k:bounded(row.get(k),1800) for k in ('path','source','sha256','coverage','truncated','extraction_error','excerpt') if k in row}
              for row in source.get('files',[])[:20]]
    for row,original in zip(evidence,source.get('files',[])[:20]):
        if len(str(original.get('excerpt','')))>1800:
            row.update(truncated=True,coverage='summary_excerpt',original_coverage=original.get('coverage'))
    payload=dict(topic=contract.get('topic'),task_description=bounded(contract.get('task_description',''),6500),
        authoritative_user_inputs=bounded(contract.get('authoritative_user_inputs',[]),2500),
        hard_parameters=contract.get('hard_parameters',[]),constraints=bounded(contract.get('constraints',[]),450),
        expected_output=[{k:bounded(r.get(k),300) for k in ('path','description','format')} for r in contract.get('expected_output',[])[:30]],
        subtasks=[{k:bounded(r.get(k),400) for k in ('id','description','expected_output')} for r in contract.get('subtasks',[])[:30]],
        completion_report={k:bounded(completion.get(k),500) for k in ('status','stop_reason','subtasks','artifacts','reasons')},
        adopted_artifact_evidence=dict(files=evidence,errors=bounded(source.get('errors',[]),300)),
        summary_corrections=bounded(contract.get('summary_corrections',[]),600),source_files_omitted=max(0,len(source.get('files',[]))-len(evidence)),
        selected_nodes=context,history_nodes_omitted=max(0,len(trajectory)-len(context)),
        evidence_policy='Excerpts are incomplete. Rejected nodes are diagnostic only. Missing adoption is not proof of absence on disk.')
    # Bound repeated source excerpts before touching authoritative numeric locks.
    while len(json.dumps(payload,ensure_ascii=False))>budget and evidence:
        largest=max(evidence,key=lambda r:len(str(r.get('excerpt',''))))
        text=largest.pop('excerpt','')
        largest.update(truncated=True,summary_excerpt_omitted=True)
        if not text:break
    if len(json.dumps(payload,ensure_ascii=False))>budget:
        payload['selected_nodes']=bounded(context,450)
        payload['authoritative_user_inputs']=[]
        payload['input_coverage_note']='Original input omitted for size; use the explicit current task and locked parameters. No complete review is claimed.'
    if len(json.dumps(payload,ensure_ascii=False))>budget:
        raise ValueError('Summary input remains over its bounded character budget')
    return payload,contract


def safe_diagnostic(exc):
    diagnostic={'type':type(exc).__name__}
    if isinstance(exc,ValueError) and str(exc).startswith('Missing llm config fields: '):
        diagnostic['reason']='missing_llm_config'
        diagnostic['missing_fields']=[k for k in ('base_url','api_key','model') if k in str(exc).split(': ',1)[1].split(', ')]
    known={'Summary input remains over its bounded character budget':'input_budget_exceeded',
           'Summary output failed the Markdown structure/size check':'invalid_summary_structure'}
    if isinstance(exc,ValueError) and str(exc) in known:diagnostic['reason']=known[str(exc)]
    if isinstance(getattr(exc,'status_code',None),int):diagnostic['http_status']=exc.status_code
    if getattr(exc,'code',None) in ('context_length_exceeded','rate_limit_exceeded','invalid_api_key'):
        diagnostic['code']=exc.code
    details=getattr(exc,'diagnostic',None)
    if isinstance(details,dict):
        diagnostic.update({k:details[k] for k in ('reason','finish_reason','round') if k in details})
    # API error strings/bodies can contain secrets. Do not publish them verbatim.
    return diagnostic


class TrajectorySummarizer:
    def __init__(self,prompts_path='prompts/',config_path='config.yaml'):
        self.prompts_path=Path(prompts_path);self.config_path=config_path
        self.summarizer_prompt=self._load_prompt('summarizer_prompt.txt')
        self.summarizer_system_prompt=self._load_prompt('summarizer_system_prompt.txt')
        from utils.task_scope import RULES
        self.summarizer_system_prompt+='\n\n'+RULES
        self.generation=None

    def _load_prompt(self,filename):
        return (self.prompts_path/filename).read_text(encoding='utf-8')

    def build_summary_markdown(self,*,task_description:str,trajectory:List[Dict[str,Any]]) -> str:
        attempts=[];contract={};payload={}
        for attempt in range(2):
            try:
                payload,contract=summary_input(task_description,trajectory or [],INPUT_BUDGET if attempt==0 else 24000)
                prompt=self.summarizer_prompt.format(task_description=json.dumps(payload,ensure_ascii=False),trajectory='See selected_nodes in the bounded evidence packet above.')
                text=call_model_without_tools(system_prompt=self.summarizer_system_prompt,
                    user_prompt=prompt,role='summarizer',config_path=self.config_path).strip()
                if (not text.startswith('# Summary') or len(text)>100000 or
                        not all(section in text for section in SECTIONS)):
                    raise ValueError('Summary output failed the Markdown structure/size check')
                self.generation=dict(version='v9.5.2',status='generated',attempts=attempts,attempt_count=attempt+1,
                    input_chars=len(prompt),history_nodes_omitted=payload.get('history_nodes_omitted',0))
                return text
            except Exception as exc:
                info=safe_diagnostic(exc);attempts.append(dict(attempt=attempt+1,error=info))
                print('[Summarizer] Summary generation failed: '+json.dumps(info,ensure_ascii=False),flush=True)
                # Smaller-input retry helps malformed/truncated/context failures.
                # SDK already retries connection failures; do not multiply those calls.
                retry=isinstance(exc,ValueError) or info.get('reason') in ('truncated_response','empty_response') or info.get('code')=='context_length_exceeded'
                if not retry:break
        if not contract:
            try:contract=json.loads(task_description)
            except (ValueError,TypeError):contract={'task_description':str(task_description)}
        if not isinstance(contract,dict):contract={'task_description':str(contract)}
        self.generation=dict(version='v9.5.2',status='fallback',attempts=attempts,attempt_count=len(attempts),
            reason='模型总结生成未成功；以下为程序生成的进展说明，不是最终科学结论')
        return self._fallback(contract)

    def _fallback(self,contract):
        report=contract.get('completion_report') or {}
        lines=['# Summary','','> 总结生成未成功。以下为程序生成的进展说明；已保存研究文件与节点记录仍可查看。','','## Task',
            str(contract.get('topic') or contract.get('task_description') or '当前研究任务').split('\n\n## REPAIR ATTEMPT',1)[0][:2000],
            '', '## Key Reasoning Steps']
        labels={'passed':'本子任务已通过节点验收','needs_revision':'仍需修订／核验','not_started':'尚未执行'}
        for row in report.get('subtasks',[]):
            lines.append('- 子任务 '+str(row.get('id'))+'：'+labels.get(row.get('status'),str(row.get('status','未记录')))+
                         ('；节点 '+str(row['node_id']) if row.get('node_id') is not None else ''))
        if not report.get('subtasks'):lines.append('- 尚无可核对的子任务完成记录。')
        lines+=['','## Final Outcome','- 研究完成状态：'+str(report.get('status','未核对'))+'。',
                '- 停止原因：'+str(report.get('stop_reason','未记录'))+'。',
                '- 本说明未生成新的数值结论，也不替代最终研究报告。']
        for a in report.get('artifacts',[]):
            if a.get('status')=='available':lines.append('- 已采纳交付：`'+str(a.get('download_path') or a.get('path'))+'`。')
        lines+=['','## Open Issues','- 模型总结失败；原因类别与尝试次数见 `summary_generation.json`。']
        for a in report.get('artifacts',[]):
            if a.get('status')!='available':lines.append('- `'+str(a.get('path'))+'` 尚未进入最终采纳成果集合；可能未生成，也可能已有文件尚未通过验收。')
        lines += ['- '+str(r)[:500] for r in report.get('reasons',[])[:20]]
        return '\n'.join(lines)+'\n'

    def write_summary_markdown(self,output_path,*,task_description,trajectory):
        out=Path(output_path);out.parent.mkdir(parents=True,exist_ok=True)
        text=self.build_summary_markdown(task_description=task_description,trajectory=trajectory)
        atomic_text(out,text+('' if text.endswith('\n') else '\n'))
        atomic_json(out.parent/'summary_generation.json',self.generation)
        return out
