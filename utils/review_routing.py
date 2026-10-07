"""Explain blockers and route repairs; never turn a missing review into acceptance."""
import hashlib
import re

TARGETS = {'report', 'summary', 'model', 'validation'}


def issue_key(text):
    text = re.sub(r'^(assumptions|definitions|limiting_cases|quantitative_consistency|applicability|validation_scope|artifact_consistency|summary_consistency)\s*[：:]\s*', '', str(text))
    return re.sub(r'\s+', ' ', text).strip().casefold()


def route_review(evaluation):
    """Typed records augment the original decision and score, without overriding either."""
    review = evaluation.get('scientific_review') or {}
    protocol = list(review.get('errors') or []) + list(evaluation.get('review_pending_reasons') or [])
    if evaluation.get('critic_error'):
        protocol.append(evaluation['critic_error'])
    protocol_keys = {issue_key(x) for x in protocol}
    checks = review.get('checks') or []
    details = review.get('issue_details') or []
    audit = evaluation.get('integrity_audit') or {}
    integrity = list(audit.get('issues') or []) + list(audit.get('unchecked') or []) if audit.get('status') in ('violation','unchecked') else []
    integrity_keys = {issue_key(x) for x in integrity}
    records = {}
    for text in list(evaluation.get('blocking_issues') or []) + protocol + integrity:
        key = issue_key(text)
        if not key:
            continue
        matched = [c for c in checks if c.get('status') in ('fail', 'unchecked') and issue_key(c.get('basis')) == key]
        supplied = [d for d in details if issue_key(d.get('detail')) == key]
        # An observed substantive check wins over an identical protocol message.
        category = 'review_protocol' if key in protocol_keys and not matched else 'science'
        if key in integrity_keys:
            category = 'integrity'
        targets = [c.get('repair_target', 'validation' if c.get('status') == 'unchecked' else 'model') for c in matched]
        targets += [d.get('target') for d in supplied]
        target = targets[0] if targets and len(set(targets)) == 1 else 'model'
        if target not in TARGETS:
            target = 'model'
        record = records.setdefault(key, dict(id='issue_' + hashlib.sha256(key.encode()).hexdigest()[:12],
            detail=str(text), category=category, severity='blocking', target='review' if category == 'review_protocol' else target,
            checks=[], evidence=[], aliases=[]))
        if str(text) != record['detail'] and str(text) not in record['aliases']:
            record['aliases'].append(str(text))
        for c in matched:
            if c['id'] not in record['checks']:
                record['checks'].append(c['id'])
            record['evidence'] = list(dict.fromkeys(record['evidence'] + c.get('evidence', [])))
        for d in supplied:
            record['evidence'] = list(dict.fromkeys(record['evidence'] + d.get('evidence', [])))
    issues = list(records.values())
    substantive = [x for x in issues if x['category'] != 'review_protocol']
    protocol_failed = (bool(protocol) or evaluation.get('review_valid') is False or
        bool(evaluation.get('critic_error')) or
        evaluation.get('score_valid') is not True and evaluation.get('science_assessable') is not False)
    if audit.get('status') in ('violation','unchecked'):
        route, state = 'repair_then_retry', 'blocked'
    elif not substantive and protocol_failed:
        route, state = 'reevaluate', 'review_pending'
    elif substantive and all(x['target'] in ('report', 'summary') for x in substantive):
        route, state = 'report_repair', 'blocked'
    elif substantive or evaluation.get('decision') != 'complete':
        route, state = 'research_revision', 'blocked'
    else:
        route, state = 'accept', 'accepted'
    evaluation.update(blocking_issue_details=issues, blocker_count=len(issues),
        repair_route=route, acceptance_state=state,
        advisory_issues=[dict(detail=x, severity='advisory', category='limitation') for x in review.get('limitations', [])])
    return evaluation


def pending_review(node):
    return (getattr(node, 'evaluation', None) or {}).get('acceptance_state') == 'review_pending'


def repair_scope_instruction(evaluation):
    route = evaluation.get('repair_route')
    issues = evaluation.get('blocking_issue_details') or []
    details = '\n'.join('- ' + x['detail'] for x in issues if x.get('category') != 'review_protocol')
    if route == 'report_repair':
        return ('\n## Program-owned targeted report repair\n'
            'Repair only the report/summary discrepancies listed below. Reuse receipt-matching accepted numerical '
            'sources. Copy required unchanged inputs into the new node, preserving their provenance and hashes. '
            'Update the adopted definitions, numbers, citations and limitations together; verify the revised report '
            'against those sources. Do not rebuild the physical model or rerun a global sweep just to change prose. '
            'If adopted numerical sources are themselves inconsistent, identify the affected calculation and validate '
            'that change instead of hiding it in wording. Never claim a copied file was newly executed.\n' + details)
    return ('\n## Program-owned repair scope\nFix the specific substantive defects below and validate affected results. '
        'Keep accepted unaffected prerequisites, user constraints and parameter values. '
        'Reviewer format/read errors belong to reevaluation, not scientific reconstruction.\n' + details) if details else ''
