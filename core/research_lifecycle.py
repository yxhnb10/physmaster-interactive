"""Research scope is independent of draft/revise and execution progress."""
import json
import re

STAGES = ('EXPLORATION', 'MODEL_BUILDING', 'SIMULATION', 'VALIDATION', 'FINALIZATION')
GUIDANCE = {
    'EXPLORATION': 'Identify assumptions, sources and unresolved questions. Do not claim a numerical solution without evidence.',
    'MODEL_BUILDING': 'Specify equations, units, parameters and model assumptions. Do not claim unperformed simulations.',
    'SIMULATION': 'Implement and execute the current model; deliver the code and numerical outputs required by THIS subtask.',
    'VALIDATION': 'Execute the validation/sensitivity/convergence checks required by THIS subtask and report failures honestly.',
    'FINALIZATION': 'Deliver the requested final report, citing current-run accepted sources. Do not invent missing numerical evidence.',
}


def stage_for(subtask):
    subtask = subtask or {}
    explicit = str(subtask.get('research_stage', '')).upper()
    if explicit in STAGES:
        return explicit
    description = str(subtask.get('description', ''))
    text = description + ' ' + json.dumps(subtask.get('expected_output', ''), ensure_ascii=False)
    if 'REPORT FINALIZATION ONLY' in description or re.search(r'报告|report|\.pdf\b', text, re.I):
        # A simulation which also creates a report must still execute its code.
        if subtask.get('subtask_type') != 'coding':
            return 'FINALIZATION'
    if re.search(r'收敛|敏感性|验证|validation|validate|convergence|sensitivity', description, re.I):
        return 'VALIDATION'
    if subtask.get('subtask_type') == 'coding':
        return 'SIMULATION'
    if re.search(r'方程|建模|model|equation', description, re.I):
        return 'MODEL_BUILDING'
    return 'EXPLORATION'


def stage_contract(subtask):
    stage = stage_for(subtask)
    return dict(research_stage=stage, current_expected_output=(subtask or {}).get('expected_output'),
                guidance=GUIDANCE[stage], mandatory_rules=[
                    'All locked parameters apply in EVERY stage, including repairs.',
                    'Verify all files claimed by this node, regardless of stage.',
                    'Do not require a later subtask report/PDF before its stage. Never ignore an output explicitly required by the current subtask.',
                    'Final task acceptance still requires ALL contract deliverables and all subtask gates.'])


def audit_stage(subtask, audit, tools):
    """Require only current-scope files now; global deliverables gate completion."""
    from pathlib import PureWindowsPath
    audit = dict(audit, issues=list(audit.get('issues', [])), unchecked=list(audit.get('unchecked', [])))
    expected = (subtask or {}).get('expected_output') or []
    names = []
    if isinstance(expected, list):
        for item in expected:
            raw = item.get('path') if isinstance(item, dict) else item
            if isinstance(raw, str):
                names.extend(re.findall(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:pdf|csv|py|md|txt|html|json|png|svg|xlsx)\b', raw, re.I))
    elif isinstance(expected, str):
        names = re.findall(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:pdf|csv|py|md|txt|html|json|png|svg|xlsx)\b', expected, re.I)
    verified = {PureWindowsPath(x['path']).name for x in audit.get('artifact_checks', []) if x.get('sha256')}
    for name in sorted(set(names) - verified):
        audit['issues'].append('当前子任务要求的文件未核对：' + name)
    if (subtask or {}).get('subtask_type') == 'coding':
        receipts = [t.get('execution') or {} for t in tools or [] if t.get('tool') == 'Python_code_interpreter']
        if not receipts or receipts[-1].get('exit_code') != 0:
            audit['unchecked'].append('当前代码任务缺少最后一次成功执行回执；关闭 Python 工具不能视为验证通过')
    audit['issues'] = list(dict.fromkeys(audit['issues']))
    audit['unchecked'] = list(dict.fromkeys(audit['unchecked']))
    audit['status'] = 'violation' if audit['issues'] else 'unchecked' if audit['unchecked'] else 'pass'
    audit['research_stage'] = stage_for(subtask)
    audit['required_current_files'] = sorted(set(names))
    return audit
