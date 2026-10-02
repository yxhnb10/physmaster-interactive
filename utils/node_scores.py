"""Separate scientific review from observable delivery and integrity checks.

The composite reward ranks MCTS attempts; it never grants acceptance. Critic
thresholds continue to apply to science_reward, on the original 0..1 scale.
"""
from utils.critic_policy import normalized_score
from utils.research_integrity import parse_output

WEIGHTS = {'science': .50, 'artifact': .20, 'execution': .10, 'compliance': .15, 'communication': .05}


def dimension(score, source, basis, applicable=True):
    value = normalized_score(score)
    return dict(score=value, valid=value is not None, applicable=applicable,
                source=source, basis=basis)


def score_node(result, evaluation, subtask=None, tools=None):
    ev = dict(evaluation)
    data = parse_output(result)
    audit = ev.get('integrity_audit') or {}
    science = normalized_score(ev.get('reward')) if ev.get('score_valid') is True else None
    checks = audit.get('artifact_checks') or []
    declared = data.get('files') or []
    if not isinstance(declared, list):
        declared = []
    # A reasoning task without files is not penalized for an irrelevant dimension.
    artifact_required = bool(declared) or str((subtask or {}).get('subtask_type', '')).lower() == 'coding'
    verified = sum(bool(r.get('sha256')) for r in checks if isinstance(r, dict))
    artifacts = min(1.0, verified / len(declared)) if declared else (0.0 if artifact_required else None)
    compliance = 1.0 if audit.get('status') == 'pass' else 0.0 if audit.get('status') == 'violation' else None
    executions = [t.get('execution') for t in tools or [] if t.get('tool') == 'Python_code_interpreter']
    execution_required = str((subtask or {}).get('subtask_type', '')).lower() == 'coding' or bool(executions)
    last = executions[-1] if executions and isinstance(executions[-1], dict) else {}
    execution = (1.0 if last.get('exit_code') == 0 else 0.0) if last.get('exit_code') is not None else None
    if data.get('execution_error'): execution_required, execution = True, 0.0
    structure_checks = [bool(data), bool(data.get('analysis') or data.get('core_results') or data.get('numerical_results')),
                        data.get('delivery_status') != 'failed' and not data.get('delivery_error') and bool(data)]
    dimensions = dict(
        science=dimension(science, 'critic', '当前子任务的科学／方法评审；原 Critic 阈值适用于此分数'),
        artifact=dimension(artifacts, 'program', f'声明文件 {len(declared)} 个，文件核对记录 {verified} 个；基础格式另受验收门禁约束', artifact_required),
        execution=dimension(execution, 'program', '最后一次 Python 执行退出码：' + str(last.get('exit_code', '无回执')) + '；成功退出不证明物理正确', execution_required),
        compliance=dimension(compliance, 'program', '参数、代码绑定、执行回执核对：' + audit.get('status', '尚未核对')),
        communication=dimension(sum(structure_checks)/3, 'program', '可解析 JSON、实质结果字段、非失败交付；不评判文字是否正确'))
    applicable = {k: d for k, d in dimensions.items() if d['applicable']}
    total = (sum(WEIGHTS[k]*d['score'] for k,d in applicable.items()) / sum(WEIGHTS[k] for k in applicable)
             if all(d['valid'] for d in applicable.values()) else None)
    ev.update(science_reward=science, score_dimensions=dimensions, reward_weights=WEIGHTS,
              composite_reward=round(total, 6) if total is not None else None,
              composite_valid=total is not None, reward=round(total, 6) if total is not None else 0.0,
              reward_scope='用于搜索排序；验收独立检查科学阈值、关键问题和程序证据')
    return ev


def failure_categories(result, evaluation, tools=None, stage=None):
    """Evidence-based categories, not an inferred diagnosis from prose keywords."""
    data = parse_output(result)
    ev = evaluation or {}
    categories = []
    def add(kind, reason):
        if reason: categories.append(dict(category=kind, reason=str(reason)))
    if stage == 'failed': add('Execution', data.get('error') or '工作进程未完成')
    if data.get('delivery_status') == 'failed' or data.get('delivery_error'):
        add('Delivery', data.get('delivery_error') or '求解器未交付可核对的结果')
    if ev.get('critic_error'): add('API', ev['critic_error'])
    if ev.get('score_valid') is False: add('Critic', 'Critic 没有返回有效科学评分；不是科学质量 0 分')
    executions = [t.get('execution') or {} for t in tools or [] if t.get('execution')]
    if executions and executions[-1].get('exit_code') not in (None, 0):
        add('Code', '最后一次 Python 执行失败，退出码 ' + str(executions[-1]['exit_code']))
    audit = ev.get('integrity_audit') or {}
    for reason in audit.get('issues') or []: add('Integrity', reason)
    for reason in audit.get('unchecked') or []: add('Evidence', reason)
    for reason in ev.get('blocking_issues') or []: add('Review', reason)
    return categories
