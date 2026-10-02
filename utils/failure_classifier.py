"""Route failures from observable receipts; never treat API failure as physics failure."""
from utils.research_integrity import parse_output

REPAIRABLE = {'EXECUTION_FAILURE', 'CODE_FAILURE', 'DELIVERY_FAILURE', 'ARTIFACT_FAILURE', 'INTEGRITY_FAILURE'}


def classify_failure(result, evaluation, tools=None):
    data = parse_output(result)
    ev = evaluation or {}
    audit = ev.get('integrity_audit') or {}
    def outcome(kind, reasons):
        return dict(kind=kind, reasons=[str(x) for x in reasons if x],
                    action='reevaluate' if kind == 'CRITIC_FAILURE' else 'repair_then_retry' if kind in REPAIRABLE else
                    'accept' if kind == 'NONE' else 'research_revision')
    if data.get('execution_error'):
        return outcome('EXECUTION_FAILURE', [data['execution_error']])
    executions = [t.get('execution') for t in tools or [] if t.get('tool') == 'Python_code_interpreter']
    if executions and isinstance(executions[-1], dict) and executions[-1].get('exit_code') not in (None, 0):
        return outcome('CODE_FAILURE', ['最后一次 Python 执行退出码：' + str(executions[-1]['exit_code'])])
    if not data or data.get('delivery_status') == 'failed' or data.get('delivery_error'):
        return outcome('DELIVERY_FAILURE', [data.get('delivery_error') or '缺少可核对的结构化交付'])
    if audit.get('status') != 'pass':
        reasons = audit.get('issues', []) + audit.get('unchecked', [])
        artifact_failure = any('文件' in str(x) or '交付' in str(x) for x in reasons)
        return outcome('ARTIFACT_FAILURE' if artifact_failure else 'INTEGRITY_FAILURE', reasons)
    if ev.get('critic_error') or ev.get('score_valid') is not True or ev.get('review_valid') is False:
        return outcome('CRITIC_FAILURE', [ev.get('critic_error') or '评审缺少有效评分或明确的关键问题字段'])
    if ev.get('decision') != 'complete' or ev.get('blocking_issues'):
        return outcome('REVIEW_FAILURE', ev.get('blocking_issues') or [ev.get('policy_reason') or ev.get('opinion') or '科学评审要求修订'])
    return outcome('NONE', [])
