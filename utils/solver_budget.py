"""Task-scoped model interaction budgets and non-authorizing delivery reminders."""
from pathlib import Path
from utils.node_delivery import node_inventory
from utils.live_updates import atomic_json

DEFAULTS = dict(draft_model_rounds=8, revise_model_rounds=16, repair_model_rounds=16,
                inspection_rounds=3, delivery_tool_rounds=3)


def solver_settings(config):
    raw=config.get('solver', {})
    if raw is None:raw={}
    if not isinstance(raw,dict):raise ValueError('solver 必须为对象')
    unknown=set(raw)-set(DEFAULTS)
    if unknown:raise ValueError('未知 solver 配置：'+', '.join(sorted(unknown)))
    values=dict(DEFAULTS, **raw)
    for key,value in values.items():
        if isinstance(value,bool) or not isinstance(value,int):raise ValueError('solver.'+key+' 必须是整数')
        low,high=(4,64) if key.endswith('_model_rounds') else (1,12)
        if not low<=value<=high:raise ValueError(f'solver.{key} 必须在 {low}～{high} 之间')
    minimum=min(values[k] for k in ('draft_model_rounds','revise_model_rounds','repair_model_rounds'))
    if values['inspection_rounds']+values['delivery_tool_rounds']>minimum-1:
        raise ValueError('solver 的检查轮数与交付轮数合计必须小于最小模型预算；最后一轮用于返回结果')
    return values


def budget_for(settings, metadata):
    repair=metadata.get('repair_attempt',0)
    kind='repair' if isinstance(repair,int) and repair>0 else 'draft' if metadata.get('node_type','draft')=='draft' else 'revise'
    return kind,settings[kind+'_model_rounds']


def execution_policy(settings, metadata, python_enabled):
    kind,rounds=budget_for(settings,metadata)
    return dict(kind=kind, model_rounds=rounds, node_dir=metadata.get('output_dir') or '',
                required_files=metadata.get('required_files') or [],
                requires_execution=bool(metadata.get('requires_execution')),
                python_enabled=bool(python_enabled), inspection_rounds=settings['inspection_rounds'],
                delivery_tool_rounds=settings['delivery_tool_rounds'])


def policy_brief(policy):
    return ('\n## Node execution budget (program settings)\n'
            f"This {policy['kind']} node has at most {policy['model_rounds']} model interaction rounds, "
            f"including one final response-only round. The initial inspection target is {policy['inspection_rounds']} rounds; "
            f"reserve the last {policy['delivery_tool_rounds']} tool-enabled rounds for corrections and verification. "
            'A round can contain multiple tool calls. This is not the overall research round budget. '
            'For a coding revision, perform one targeted inspection batch, copy the relevant baseline and dependencies '
            'to this node, persist a complete modified implementation early, then execute scoped checks. '
            'Do not exhaust the budget on repeated global hashing or read-only surveys. Required checks remain mandatory; '
            'these targets do not authorize fabricated results, partial placeholder code or weaker acceptance. '
            'Return actual node-relative files and parameter evidence; report unperformed work honestly.\n')


def checkpoint(policy, iteration, total):
    remaining=max(0,total-iteration-1)
    phase=('delivery' if remaining==0 else 'verification' if remaining<=policy['delivery_tool_rounds'] else
           'implementation' if iteration>=policy['inspection_rounds'] else 'targeted_inspection')
    inventory=(node_inventory(policy['node_dir'], policy['required_files'], max_files=24)
               if policy.get('node_dir') else dict(files=[],required_files=[],errors=['No node directory supplied']))
    progress=dict(schema_version=1, node_kind=policy['kind'], model_round=iteration+1,
                  model_round_budget=total, tool_rounds_remaining=remaining, phase=phase, inventory=inventory)
    if policy.get('node_dir') and Path(policy['node_dir']).is_dir():
        try:atomic_json(Path(policy['node_dir'])/'solver_progress.json',progress)
        except OSError as exc:progress['progress_write_warning']=type(exc).__name__
    return progress


def checkpoint_prompt(progress, policy):
    required=progress['inventory'].get('required_files',[])
    status='; '.join(row['name']+': '+row['status'] for row in required) or 'no named file requirement supplied'
    observed=', '.join(row['path'] for row in progress['inventory'].get('files',[])[:12]) or 'none observed'
    text=(f"## Execution checkpoint: round {progress['model_round']}/{progress['model_round_budget']}, "
          f"{progress['tool_rounds_remaining']} tool-enabled rounds remain (including this round), phase={progress['phase']}. "
          f"Current node disk observations: {observed}. Required file observations: {status}. "
          'Disk presence is not declaration, acceptance or proof that the model ran. ')
    if policy['required_files'] or policy['requires_execution']:
        if progress['phase'] in ('implementation','verification'):
            text+='Prioritize persisting the corrected complete implementation and scoped execution over more baseline inspection. '
        if progress['phase']=='verification':
            text+='Verify actual files, parameters and the specific revision; include real execution receipts and outstanding blockers. '
    if not policy['python_enabled']:
        text+='Python execution is disabled; do not invent execution and explicitly report that limit. '
    return text


def needs_delivery_resume(response, policy):
    """One opportunity to persist missing delivery; never manufactures a result."""
    if not policy.get('python_enabled') or not (policy.get('required_files') or policy.get('requires_execution')):
        return False
    from utils.research_integrity import parse_output
    from pathlib import PureWindowsPath
    data=parse_output(response)
    files=data.get('files') if isinstance(data.get('files'),list) else []
    declared=set()
    for item in files:
        raw=item.get('path') if isinstance(item,dict) else item
        if isinstance(raw,str):declared.add(PureWindowsPath(raw).name)
    return any(name not in declared for name in policy.get('required_files',[])) or not files
