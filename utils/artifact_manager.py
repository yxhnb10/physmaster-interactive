"""Auditable node inventory and immutable-by-hash final publication."""
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from utils.live_updates import atomic_json
from utils.research_integrity import _node_file, _file_hash, _valid_artifact, parse_output

ARTIFACT_STATES = ["CREATED", "REGISTERED", "VERIFIED", "PROMOTED", "PUBLISHED"]


def accepted(evaluation):
    ev = evaluation or {}
    return (ev.get('decision') == 'complete' and ev.get('verdict') == 'accept'
            and ev.get('score_valid') is True and ev.get('integrity_audit', {}).get('status') == 'pass'
            and not ev.get('blocking_issues'))


class ArtifactManager:
    def __init__(self, task_dir):
        self.root = Path(task_dir).resolve()

    def record_node(self, node_id, result, evaluation):
        folder = self.root / f'node_{int(node_id)}'
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink():
            raise ValueError('节点成果目录不能为符号链接')
        try:
            previous = json.loads((folder/'artifact_manifest.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        old_records = {x.get('path'): x for x in previous.get('artifacts', [])}
        data = parse_output(result)
        audit = (evaluation or {}).get('integrity_audit') or {}
        checked = {r['path']: r for r in audit.get('artifact_checks', []) if isinstance(r,dict) and r.get('path')}
        declarations = data.get('files') if isinstance(data.get('files'), list) else []
        claimed, invalid = set(), []
        for raw in declarations:
            value = raw.get('path') if isinstance(raw, dict) else raw
            try:
                path = _node_file(folder, value)
                claimed.add(path.relative_to(folder).as_posix())
            except (OSError, TypeError, ValueError) as exc:
                invalid.append(dict(declared_path=value, status='invalid', reason=str(exc)))
        records = []
        for path in sorted(folder.rglob('*')):
            if path.is_symlink() or not path.is_file() or '__pycache__' in path.parts or path.name == 'artifact_manifest.json':
                continue
            relative = path.relative_to(folder).as_posix()
            # All path components must remain within this node without links.
            if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(folder)):
                continue
            digest = _file_hash(path)
            error = _valid_artifact(path) if relative in claimed else None
            verified = bool(path.stat().st_size and relative in claimed and relative in checked
                            and checked[relative].get('sha256') == digest and not error)
            state = 'accepted' if verified and accepted(evaluation) else 'verified' if verified else 'generated'
            if error: state = 'invalid'
            record = dict(path=f'node_{int(node_id)}/{relative}', node_path=relative,
                          bytes=path.stat().st_size, sha256=digest, declared=relative in claimed,
                          status=state, lifecycle=['generated'] + (['verified'] if verified else [])
                          + (['accepted'] if state == 'accepted' else []))
            record['state'] = 'VERIFIED' if verified else 'REGISTERED'
            record['lifecycle'] = ['CREATED', 'REGISTERED'] + (['VERIFIED'] if verified else [])
            record['verification_error'] = error
            prior = old_records.get(record['path'], {})
            now = time.time()
            record['state_times'] = {stage: (prior.get('state_times', {}).get(stage, now) if prior.get('sha256') == digest else now) for stage in record['lifecycle']}
            if (state == 'accepted' and prior.get('sha256') == digest and prior.get('state') == 'PUBLISHED'
                    and prior.get('download_path')):
                published = self.root/prior['download_path']
                if published.is_file() and not published.is_symlink() and _file_hash(published) == digest:
                    for key in ('status', 'state', 'lifecycle', 'state_times', 'download_path', 'published_at'):
                        if key in prior: record[key] = prior[key]
            if error: record['reason'] = error
            records.append(record)
        manifest = dict(schema_version=2, node_id=int(node_id), updated_at=time.time(),
                        node_accepted=accepted(evaluation), artifacts=records, invalid_declarations=invalid)
        atomic_json(folder/'artifact_manifest.json', manifest)
        return manifest

    def publish(self, report):
        """Verify selected-node receipts, promote to the task root, then publish matching bytes to final/."""
        destination = self.root/'final'
        if destination.is_symlink(): raise ValueError('最终成果目录不能为符号链接')
        destination.mkdir(parents=True, exist_ok=True)
        manifest_records = []
        accepted_ids = {int(x['node_id']) for x in report.get('subtasks', []) if x.get('status') == 'passed'}
        for row in report.get('artifacts', []) + report.get('additional_artifacts', []):
            record = dict(row)
            if record.get('status') != 'available':
                manifest_records.append(record)
                continue
            name = record['path']
            try:
                if Path(name).name != name or name in ('manifest.json', 'final_manifest.json'):
                    raise ValueError('不允许的归档名称')
                source = self.root/record['source']
                if not re.fullmatch(r'node_\d+', Path(record['source']).parts[0]):
                    raise ValueError('最终文件必须来自本次选中节点')
                node_id = int(Path(record['source']).parts[0][5:])
                if node_id not in accepted_ids: raise ValueError('源节点不在选中且已验收的分支内')
                node_manifest = json.loads((self.root/f'node_{node_id}'/'artifact_manifest.json').read_text(encoding='utf-8'))
                receipt = next((x for x in node_manifest.get('artifacts', []) if x.get('path') == record['source']), {})
                if (node_manifest.get('node_accepted') is not True or receipt.get('sha256') != record.get('sha256')
                        or receipt.get('state') not in ('VERIFIED', 'PROMOTED', 'PUBLISHED')):
                    raise ValueError('源文件缺少已验收节点的核对回执')
                source = _node_file(self.root/Path(record['source']).parts[0], source)
                if _file_hash(source) != record.get('sha256'): raise ValueError('核对后源文件发生变化')
                from utils.research_integrity import _copy_verified
                promoted = self.root/name
                if promoted.is_symlink(): raise ValueError('归档目标不能为符号链接')
                _copy_verified(source, promoted, record['sha256'])
                row.update(state='PROMOTED', lifecycle=ARTIFACT_STATES[:4])
                for item in node_manifest['artifacts']:
                    if item.get('path') == record['source'] and item.get('sha256') == record['sha256']:
                        item.update(state='PROMOTED', lifecycle=list(ARTIFACT_STATES[:4]))
                        item.setdefault('state_times', {})['PROMOTED'] = time.time()
                atomic_json(self.root/f'node_{node_id}'/'artifact_manifest.json', node_manifest)
                target = destination/name
                if target.is_symlink(): raise ValueError('最终文件目标不能为符号链接')
                temporary = destination/f'.{name}.{uuid.uuid4().hex}.tmp'
                try:
                    shutil.copyfile(source, temporary)
                    if _file_hash(temporary) != record['sha256']: raise ValueError('复制过程中文件发生变化')
                    from utils.live_updates import replace_with_retry
                    replace_with_retry(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                row.update(download_path='final/'+name, state='PUBLISHED', lifecycle=list(ARTIFACT_STATES))
                record.update(row, source_node_id=int(Path(record['source']).parts[0][5:]))
                try:self._mark_published(record)
                except OSError as exc:record['node_manifest_warning']=str(exc)
            except (OSError, ValueError, KeyError) as exc:
                row.update(status='invalid', reason='最终发布失败：'+str(exc))
                record.update(row)
                report['status'] = 'partial'
                reason = name+' 最终发布失败：'+str(exc)
                if reason not in report['reasons']: report['reasons'].append(reason)
            manifest_records.append(record)
        manifest = dict(schema_version=2, status=report['status'], updated_at=time.time(),
                        contract_revision=report.get('contract_revision',0), artifacts=manifest_records,
                        acceptance_scope='文件来源于选中分支的已验收节点；不等同于独立物理验证',
                        accepted_nodes=[s['node_id'] for s in report.get('subtasks',[]) if s['status']=='passed'])
        atomic_json(destination/'manifest.json', manifest)
        atomic_json(self.root/'final_manifest.json', manifest)
        report['final_manifest_path']='final/manifest.json'
        return report

    def _mark_published(self, row):
        path = self.root/f"node_{row['source_node_id']}"/'artifact_manifest.json'
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError): return
        for entry in manifest.get('artifacts', []):
            if entry['path'] == row['source'] and entry['sha256'] == row['sha256']:
                entry.update(status='published', state='PUBLISHED', lifecycle=row['lifecycle'], download_path=row['download_path'],published_at=time.time())
                entry.setdefault('state_times', {}).setdefault('PROMOTED', time.time())
                entry['state_times']['PUBLISHED'] = time.time()
        atomic_json(path, manifest)
