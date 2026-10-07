"""Bounded final-file/summary audit. Never rewrites a scientific model by prose."""
import json
from pathlib import Path
from utils.live_updates import atomic_json, atomic_text
from utils.research_integrity import _node_file, _file_hash, parse_output
from utils.review_evidence import text_content, excerpt_text, make_reader, READ_REVIEW_TOOL, uncovered_reports, prioritize_excerpts
from utils.scientific_review import CHECKS, REVIEW_RULES, validate_review, reference_errors

FINAL_CHECKS = CHECKS + ('summary_consistency',)


def adopted_evidence(task_dir, report, include_summary=False):
    """Only published receipt-matching bytes; rejected nodes never become baseline truth."""
    root = Path(task_dir)
    rows, errors = [], []
    specs = report.get('artifacts', []) + report.get('additional_artifacts', [])
    if include_summary:
        specs = specs + [dict(path='summary.md', download_path='summary.md', status='available', required=True)]
    for spec in specs:
        if spec.get('status') != 'available':
            continue
        relative = spec.get('download_path') or spec.get('path')
        try:
            path = _node_file(root, relative)
            digest = _file_hash(path)
            if relative != 'summary.md' and (not spec.get('sha256') or digest != spec['sha256']):
                raise ValueError('已发布文件与验收哈希不一致')
            if relative != 'summary.md' and spec.get('source'):
                source = _node_file(root, spec['source'])
                if _file_hash(source) != digest:
                    raise ValueError('当前源文件与已发布文件不一致')
            row = dict(path=str(relative), sha256=digest, bytes=path.stat().st_size,
                       source=spec.get('source'), required=spec.get('required', True), node_id=None)
            try:
                row.update(excerpt_text(text_content(path), 20000 if path.suffix.lower() in ('.md', '.txt', '.pdf', '.docx') else 6000))
            except (ValueError, OSError, ImportError, KeyError) as exc:
                row.update(coverage='unavailable', truncated=True, extraction_error=str(exc))
            rows.append(row)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(relative)+'：'+str(exc))
    # Prefer the actual final report and summary, without silently excluding other sources.
    rows.sort(key=lambda r: (not r['required'], Path(r['path']).suffix.lower() not in ('.md', '.pdf', '.docx'), r['path']))
    priority = [r for r in rows if (r['required'] or r['path']=='summary.md')
        and Path(r['path']).suffix.lower() in ('.md','.txt','.pdf','.docx')]
    prioritize_excerpts(rows, priority, loader=lambda r: text_content(_node_file(root,r['path'])))
    return dict(files=rows, errors=errors,
                scope='本次已发布文件及其验收哈希；历史轨迹不是最终公式的替代来源')


class FinalConsistencyReviewer:
    def __init__(self, config_path='config.yaml', model_call=None, rewrite_call=None):
        self.config_path = config_path
        self.model_call = model_call
        self.rewrite_call = rewrite_call

    def _audit(self, root, contract, report, trajectory):
        evidence = adopted_evidence(root, report, include_summary=True)
        reads = []
        catalog = {r['path']: r for r in evidence['files']}
        reader = make_reader(root, catalog, reads, max_reads=12, max_chars=80000)
        if evidence['errors']:
            return dict(status='needs_revision', issues=[dict(target='validation', detail=x,
                        affected_subtask_ids=[]) for x in evidence['errors']], checks=[],
                        errors=evidence['errors'], claim_scope='not_assessed', assumptions=[],
                        limitations=['文件内容发生变化或读取失败，未完成一致性审查'],
                        evidence_coverage=[{k:r.get(k) for k in ('path','sha256','coverage','truncated','sections')} for r in evidence['files']],
                        extra_reads=[])
        system = (REVIEW_RULES + '\nYou are the FINAL consistency reviewer, independent of previous acceptance.\n'
            'Review the actual published report, adopted source files, and generated summary.\n'
            'Final claims take precedence over historical paraphrases. Do not blend different revisions.\n'
            'Do not equate unavailable/truncated evidence with a completed review. Read relevant missing\n'
            'sections with read_review_file. Keep unverified reachability, objective and time-horizon\n'
            'assumptions explicit. A conditional internally consistent model may be accepted.\n'
            'Return JSON only: scientific_checks (the seven protocol checks PLUS summary_consistency),\n'
            'claim_scope, assumptions, limitations, issues. issues is an array of objects with target\n'
            '(summary, report, model, validation), detail (specific discrepancy), affected_subtask_ids\n'
            '(array of actual contract IDs), and evidence (array of file/line references).\n'
            'For summary_consistency compare summary formulas, meanings, numbers, validation claims\n'
            'and limitations with final adopted artifacts. Explain non-applicable checks.\n'
            'Never prescribe additional deliverables/tests merely because a historical critic wanted them.\n'
            'A scientific-model contradiction cannot be repaired by rewording the summary.\n')
        # Prior results are diagnostic context, not a source overriding adopted files.
        prior = [dict(node_id=n.get('node_id'), subtask_id=n.get('subtask_id'),
            core_results=str(parse_output(n.get('result')).get('core_results') or '')[:1500],
            review_summary={k:((n.get('critic_feedback') or {}).get('scientific_review') or {}).get(k)
                for k in ('claim_scope','assumptions','limitations')}) for n in trajectory[-16:]]
        prompt = json.dumps(dict(contract=contract, completion_status=report.get('status'),
            adopted_artifact_evidence=evidence, selected_trajectory_context=prior), ensure_ascii=False)
        try:
            if self.model_call is None:
                from utils.llm_client import call_model
                model_call = call_model
            else:
                model_call = self.model_call
            raw = model_call(system_prompt=system, user_prompt=prompt, role='critic_strong',
                tools=[READ_REVIEW_TOOL], tool_functions={'read_review_file': reader},
                max_tool_calls=6, config_path=self.config_path)
            parsed = parse_output(raw)
            review = validate_review(parsed, FINAL_CHECKS)
            review['errors'].extend(reference_errors(review['checks'], catalog, ('contract:',), evidence['files']))
            issues = parsed.get('issues')
            if not isinstance(issues, list):
                review['errors'].append('最终审查缺少 issues 数组'); issues = []
            allowed_ids = {s.get('id', i) for i, s in enumerate(contract.get('subtasks', []), 1) if isinstance(s, dict)}
            clean = []
            for issue in issues:
                if (not isinstance(issue, dict) or issue.get('target') not in ('summary','report','model','validation')
                        or not isinstance(issue.get('detail'), str) or not issue['detail'].strip()
                        or not isinstance(issue.get('affected_subtask_ids'), list)
                        or any(isinstance(i, bool) or i not in allowed_ids for i in issue['affected_subtask_ids'])
                        or not isinstance(issue.get('evidence'), list)
                        or not issue['evidence'] or any(not isinstance(x, str) or not x.strip() for x in issue['evidence'])):
                    review['errors'].append('最终审查的问题记录格式或来源无效'); continue
                clean.append(issue)
                review['errors'].extend(reference_errors([dict(id='问题记录', evidence=issue['evidence'])], catalog, ('contract:',), evidence['files']))
            for key in ('assumptions', 'definitions', 'validation_scope', 'artifact_consistency', 'summary_consistency'):
                if any(c['id']==key and c['status']=='not_applicable' for c in review['checks']):
                    review['errors'].append('最终成果审查不能省略：'+key)
            # Detect mutations after prompt construction / tool reads, including published source changes.
            for row in evidence['files']:
                if _file_hash(_node_file(root, row['path'])) != row['sha256']:
                    review['errors'].append('审查期间文件发生变化：'+row['path'])
                if row.get('source') and _file_hash(_node_file(root, row['source'])) != row['sha256']:
                    review['errors'].append('审查期间源文件发生变化：'+row['source'])
            required_reports = [r['path'] for r in evidence['files'] if r['required']
                and Path(r['path']).suffix.lower() in ('.md', '.txt', '.pdf', '.docx')]
            review['errors'].extend(uncovered_reports(catalog, reads, required_reports))
            status = 'unchecked' if review['errors'] else 'needs_revision' if clean or review['blockers'] else 'passed'
            # A failed check without an issue target cannot safely trigger summary-only rewriting.
            for blocker in review['blockers']:
                if not clean:
                    clean.append(dict(target='validation', detail=blocker, affected_subtask_ids=[], evidence=[]))
            # Same discrepancy may appear in several checks; retain all affected IDs and evidence.
            from utils.review_routing import issue_key
            grouped = {}
            for issue in clean:
                key = (issue['target'], issue_key(issue['detail']))
                item = grouped.setdefault(key, dict(issue, severity='blocking', affected_subtask_ids=[], evidence=[]))
                for field in ('affected_subtask_ids','evidence'):
                    item[field] = list(dict.fromkeys(item[field] + issue.get(field, [])))
            clean = list(grouped.values())
            return dict(review, status=status, issues=clean,
                        evidence_coverage=[{k:r.get(k) for k in ('path','sha256','coverage','truncated','sections')} for r in evidence['files']],
                        extra_reads=reads)
        except Exception as exc:
            return dict(status='unchecked', issues=[], checks=[], errors=[type(exc).__name__+': '+str(exc)],
                        claim_scope='not_assessed', assumptions=[], limitations=['最终一致性评审未成功，不能视为已核验'],
                        evidence_coverage=[{k:r.get(k) for k in ('path','sha256','coverage','truncated','sections')} for r in evidence['files']],
                        extra_reads=reads)

    def _review_with_retries(self, root, contract, report, trajectory):
        """Protocol retries never rewrite source files or trigger a solver."""
        if report.get('stop_reason') == 'review_pending':
            return dict(status='unchecked', issues=[], checks=[], errors=['原节点仍待评审，暂不对未完成交付做最终一致性签收'],
                claim_scope='not_assessed', assumptions=[], limitations=[], evidence_coverage=[], extra_reads=[],
                review_attempts=[], deferred=True)
        import yaml
        from core.repair_agent import repair_settings
        config = yaml.safe_load(Path(self.config_path).read_text(encoding='utf-8')) if Path(self.config_path).is_file() else {}
        settings = repair_settings(config or {})
        limit = settings['max_critic_retries'] if settings['enabled'] else 0
        history = []
        for attempt in range(limit+1):
            audit = self._audit(root, contract, report, trajectory)
            history.append(dict(attempt=attempt+1, status=audit['status'], errors=audit.get('errors', [])))
            if audit['status'] != 'unchecked':
                break
        audit['review_attempts'] = history
        return audit

    def run(self, task_dir, contract, report, trajectory):
        root = Path(task_dir)
        history = []
        rewrites = 0
        audit = self._review_with_retries(root, contract, report, trajectory)
        history.append(audit)
        # Exactly one summary-only correction; model/report/validation issues never get a prose repair.
        if (audit['status']=='needs_revision' and audit.get('issues')
                and all(x['target']=='summary' for x in audit['issues'])
                and all(x['status'] not in ('fail','unchecked') or x['id']=='summary_consistency' for x in audit['checks'])):
            try:
                evidence = adopted_evidence(root, report)
                contract_text = json.dumps(dict(contract, completion_report=report,
                    adopted_artifact_evidence=evidence, summary_corrections=audit['issues']), ensure_ascii=False)
                if self.rewrite_call is not None:
                    text = self.rewrite_call(contract_text, trajectory)
                else:
                    from core.summarizer import TrajectorySummarizer
                    summary = TrajectorySummarizer(config_path=self.config_path)
                    text = summary.build_summary_markdown(task_description=contract_text, trajectory=trajectory)
                    if summary.generation.get('status')!='generated':raise ValueError('总结重写失败；保留原总结与研究文件')
                if not isinstance(text, str) or not text.strip().startswith('# Summary'):
                    raise ValueError('重写总结未返回有效 Markdown')
                original = root/'summary.md'
                if original.is_symlink():
                    raise ValueError('总结文件不能为符号链接')
                (root/'summary_before_v95_review.md').write_bytes(original.read_bytes())
                atomic_text(original,text)
                atomic_json(root/'summary_generation.json',dict(version='v9.5.2',status='generated',source='consistency_rewrite'))
                rewrites += 1
                audit = self._review_with_retries(root, contract, report, trajectory)
                history.append(audit)
            except Exception as exc:
                audit = dict(audit, status='unchecked', errors=audit.get('errors', [])+[type(exc).__name__+': '+str(exc)])
                history.append(audit)
        targets = {x['target'] for x in audit.get('issues', [])}
        route = ('reevaluate' if audit['status']=='unchecked' and not targets else 'report_repair'
            if targets and targets <= {'report','summary'} else 'research_revision' if targets else
            'accept' if audit['status']=='passed' else 'reevaluate')
        record = dict(audit, version='v9.5.3', attempts=history, summary_rewrites=rewrites,
                      repair_route=route, acceptance_state='review_pending' if route=='reevaluate' else
                      'accepted' if route=='accept' else 'blocked',
                      scope='最终文件与总结的模型一致性评审；不等同于独立实验或物理正确性证明')
        record['next_actions'] = [dict(target=x['target'], detail=x['detail'],
            affected_subtask_ids=x.get('affected_subtask_ids', [])) for x in audit.get('issues', [])]
        report['final_consistency'] = record
        report['delivery_status'] = 'complete' if all(x.get('status')=='available' for x in report.get('artifacts', [])) else 'incomplete'
        report['node_review_status'] = 'review_pending' if report.get('stop_reason')=='review_pending' else 'accepted' if all(x.get('status')=='passed' for x in report.get('subtasks', [])) else 'needs_revision'
        if audit['status'] != 'passed':
            report['status'] = 'partial'
            reasons = ['最终一致性审查：'+x['detail'] for x in audit.get('issues', [])]
            reasons += ['最终一致性尚未核验：'+x for x in audit.get('errors', [])]
            report.setdefault('reasons', []).extend(x for x in reasons if x not in report.get('reasons', []))
        atomic_json(root/'final_consistency.json', record)
        atomic_json(root/'completion.json', report)
        for path in (root/'final_manifest.json', root/'final'/'manifest.json'):
            if path.is_file() and not path.is_symlink():
                manifest = json.loads(path.read_text(encoding='utf-8'))
                manifest.update(status=report['status'], final_consistency_status=audit['status'], claim_scope=audit['claim_scope'])
                atomic_json(path, manifest)
        return report
