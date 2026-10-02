"""Complete research snapshots and explicit, hash-checked incremental inputs.

Historical acceptance describes the old conditions. It never supplies a new
node's score or bypasses its normal integrity/review/publication gates.
"""
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath
from utils.live_updates import atomic_json

POLICY = ('INCREMENTAL CONTINUATION: preserve all original requirements and amendments. '
    'Use inherited code/data/validation as the baseline; plan only new work and affected '
    'checks. Do not repeat unaffected exploration/model construction. First inspect the '
    'inherited inventory and relevant original contracts/reviews. Explain reuse vs changes. '
    'Historical acceptance applies only to historical conditions; failures stay failures. '
    'Use read_inherited_file to inspect inputs and reuse_inherited_file to copy unchanged '
    'code/data plus dependencies into this node. Declare reused_files with file_id, '
    'unchanged_reason and impact_checked=true when claiming unchanged results. '
    'Generate outputs only in current_node_dir. Never edit inherited snapshots or originals. '
    'New/affected results must pass normal execution, parameter, artifact and Critic checks. '
    'A snapshot is not experimental validation or restoration of process memory.')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    root = Path(root).resolve()
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or '\\' in relative or ':' in relative:
        raise ValueError('Invalid inherited file path')
    candidate = root.joinpath(*p.parts)
    if any(x.is_symlink() for x in [candidate, *candidate.parents] if x.is_relative_to(root)):
        raise ValueError('Inherited files must not be symbolic links')
    if not candidate.resolve().is_relative_to(root):
        raise ValueError('Inherited path escapes task directory')
    return candidate


def snapshot_research(parent_dir, child_dir, source_id, conditions):
    """Copy every regular research file, flattening earlier generations once.

    Credentials/configuration outside query/ are intentionally not research
    outputs. Broken/changed historical files remain visible as unverified.
    """
    parent, child = Path(parent_dir).resolve(), Path(child_dir).resolve()
    if parent == child or child.is_relative_to(parent):
        raise ValueError('Snapshot destination must be a separate task')
    child.mkdir(parents=True, exist_ok=True)
    staging = child / ('.inheritance-' + uuid.uuid4().hex)
    staging.mkdir()
    prior = read_json(parent / 'inheritance.json', {}) or {}
    existing = read_json(child / 'inheritance.json', {}) or {}
    records, sources = [], []
    try:
        # Earlier sources remain accessible after original tasks are moved/deleted.
        known = set()
        for context_root, context in ((child, existing), (parent, prior)):
            for source in context.get('sources', []):
                if source['task_id'] not in {s['task_id'] for s in sources}: sources.append(source)
            for old in context.get('files', []):
                if old['file_id'] in known: continue
                src = safe_path(context_root, old['snapshot_path'])
                dst = safe_path(staging, old['snapshot_path'].removeprefix('inherited/'))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                if file_hash(dst) != old['sha256']:
                    raise ValueError('Earlier inherited snapshot changed: ' + old['source_path'])
                records.append(dict(old));known.add(old['file_id'])
        receipts = {}
        for p in parent.glob('node_*/artifact_manifest.json'):
            manifest = read_json(p, {}) or {}
            for row in manifest.get('artifacts', []):
                receipts[row.get('path')] = dict(row, node_accepted=manifest.get('node_accepted') is True)
        final = read_json(parent / 'final_manifest.json', {}) or {}
        for row in final.get('artifacts', []):
            for key in ('path', 'download_path'):
                if row.get(key):
                    receipts[row[key]] = dict(row, node_accepted=row.get('status') == 'available')
        # A full snapshot includes failed nodes, logs, empty files and receipts.
        for directory, dirs, files in os.walk(parent, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(directory) == parent and d == 'inherited'))
            for name in dirs + sorted(files):
                if (Path(directory) / name).is_symlink():
                    raise ValueError('Cannot snapshot linked research output: ' + name)
            for name in sorted(files):
                src = Path(directory) / name
                relative = src.relative_to(parent).as_posix()
                dst = staging / source_id / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                digest = file_hash(dst)
                if digest != file_hash(src):
                    raise ValueError('Research file changed during snapshot: ' + relative)
                receipt = receipts.get(relative, {})
                historical = ('accepted' if receipt.get('node_accepted') and receipt.get('sha256') == digest
                    else 'verified' if receipt.get('sha256') == digest else 'unverified')
                identity = hashlib.sha256((source_id + '/' + relative).encode()).hexdigest()[:24]
                records.append(dict(file_id=identity, source_task_id=source_id, source_path=relative,
                    snapshot_path='inherited/' + source_id + '/' + relative,
                    bytes=dst.stat().st_size, sha256=digest, historical_status=historical,
                    kind=src.suffix.lower().lstrip('.') or 'file'))
        # Server-side run records live one level above query/. Never copy the
        # credential-bearing web_config.yaml; retain logs and user/task metadata.
        for name in ('console.log', 'task.json', 'query.txt'):
            src = parent.parent / name
            if not src.exists(): continue
            if src.is_symlink() or not src.is_file():
                raise ValueError('Invalid historical run record: ' + name)
            relative = 'run_records/' + name
            dst = staging / source_id / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            digest = file_hash(dst)
            if digest != file_hash(src):
                raise ValueError('Historical run record changed during snapshot')
            records.append(dict(file_id=hashlib.sha256((source_id+'/'+relative).encode()).hexdigest()[:24],
                source_task_id=source_id, source_path=relative,
                snapshot_path='inherited/'+source_id+'/'+relative, bytes=dst.stat().st_size,
                sha256=digest, historical_status='unverified', kind=src.suffix.lstrip('.')))
        sources.append(dict(task_id=source_id, original_dir=str(parent),
                            contract=read_json(parent / 'contract.json', {}),
                            outcome=(read_json(parent / 'completion.json', {}) or {}).get('status', 'unknown')))
        destination = child / 'inherited'
        if destination.exists():
            # Before launch only: combine legacy v9.3 ancestors into one flat snapshot.
            retired = child / ('.old-inherited-' + uuid.uuid4().hex)
            os.replace(destination, retired)
            try: os.replace(staging, destination)
            except Exception:
                os.replace(retired, destination)
                raise
            shutil.rmtree(retired)
        else:
            os.replace(staging, destination)
        value = dict(schema_version=1, mode='incremental', created_at=time.time(),
            parent_task_id=source_id, conditions=list(conditions), sources=sources, files=records,
            note='Full historical snapshots; acceptance must be checked against current conditions.')
        atomic_json(child / 'inheritance.json', value)
        (child / 'inherited_index.md').write_text(inventory_text(child, value), encoding='utf-8')
        return value
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def inventory_text(task_dir, manifest=None):
    value = manifest or read_json(Path(task_dir) / 'inheritance.json', {}) or {}
    lines = ['# Inherited research inventory', '', POLICY, '',
        '| file_id | source task | original path | historical status | SHA-256 |',
        '| --- | --- | --- | --- | --- |']
    for row in value.get('files', []):
        lines.append('| ' + ' | '.join(str(row[k]).replace('|', '\\|')
            for k in ('file_id', 'source_task_id', 'source_path', 'historical_status', 'sha256')) + ' |')
    return '\n'.join(lines) + '\n'


def attach_context(contract, task_dir):
    """Program-owned context survives a Clarifier that omits every old path."""
    root = Path(task_dir).resolve()
    value = read_json(root / 'inheritance.json', {}) or {}
    if not value:
        return contract
    rows = value.get('files', [])
    # Entire inventory is on disk/tools. A small priority list avoids context bloat.
    priority = sorted(rows, key=lambda r: (r['historical_status'] != 'accepted',
        r['kind'] not in ('py', 'csv', 'json'), r['source_task_id'] != value['parent_task_id'], r['source_path']))
    contract['continuation_context'] = dict(mode='incremental', parent_task_id=value['parent_task_id'],
        source_task_ids=[s['task_id'] for s in value['sources']], file_count=len(rows),
        manifest_path=str(root / 'inheritance.json'), inventory_path=str(root / 'inherited_index.md'),
        inherited_root=str(root / 'inherited'), policy=POLICY, priority_files=priority[:24],
        tools=['list_inherited_files', 'read_inherited_file', 'reuse_inherited_file'])
    if not isinstance(contract.get('continuation_plan'), dict):
        contract['continuation_plan'] = dict(mode='incremental', status='scope_to_be_checked',
            reuse_basis='Complete inherited files remain available; unchanged claims require impact evidence.',
            new_work=[s.get('description', '') for s in contract.get('subtasks', []) if isinstance(s, dict)])
    contract['constraints'] = list(contract.get('constraints') or [])
    if POLICY not in contract['constraints']:
        contract['constraints'].append(POLICY)
    for subtask in contract.get('subtasks', []):
        if isinstance(subtask, dict) and 'INCREMENTAL CONTINUATION' not in subtask.get('description', ''):
            subtask['description'] = POLICY + '\n' + subtask.get('description', '')
    return contract


def planning_brief(task_dir):
    value = read_json(Path(task_dir) / 'inheritance.json', {}) or {}
    if not value:
        return ''
    accepted = [r for r in value['files'] if r['historical_status'] == 'accepted']
    brief = dict(parent_task_id=value['parent_task_id'], file_count=len(value['files']),
        accepted_baselines=[{k: r[k] for k in ('file_id', 'source_path', 'kind')} for r in accepted[:40]],
        historical_tasks=[dict(task_id=s['task_id'], outcome=s['outcome'],
            subtasks=(s.get('contract') or {}).get('subtasks', [])) for s in value['sources'][-1:]])
    return '\n\n## Program-preserved incremental baseline\n' + POLICY + '\n' + json.dumps(brief, ensure_ascii=False)


def get_record(task_dir, file_id):
    root = Path(task_dir).resolve()
    value = read_json(root / 'inheritance.json', {}) or {}
    row = next((r for r in value.get('files', []) if r['file_id'] == file_id), None)
    if row is None:
        raise ValueError('Unknown inherited file_id')
    path = safe_path(root, row['snapshot_path'])
    if not path.is_file() or file_hash(path) != row['sha256']:
        raise ValueError('Inherited file missing or changed: ' + row['source_path'])
    return row, path


def record_usage(node_dir, events):
    path = Path(node_dir) / 'inheritance_usage.json'
    value = read_json(path, {'events': []})
    value['events'].extend(dict(e, timestamp=time.time()) for e in events)
    atomic_json(path, value)


def list_files(task_dir, query='', offset=0, limit=50):
    value = read_json(Path(task_dir) / 'inheritance.json', {}) or {}
    rows = [r for r in value.get('files', []) if str(query).lower() in r['source_path'].lower()]
    offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
    return dict(total=len(rows), offset=offset, files=rows[offset:offset + limit],
                next_offset=offset + limit if offset + limit < len(rows) else None)


def read_file(task_dir, node_dir, file_id, offset=0, limit=12000):
    row, path = get_record(task_dir, file_id)
    start, size = max(0, int(offset)), min(24000, max(1, int(limit)))
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
    elif path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.npy', '.npz', '.zip'):
        text = '[Binary file: use verified absolute_path with Python or copy it as an input.]'
    else:
        # Avoid loading large datasets entirely; offsets refer to UTF-8 bytes.
        with path.open('rb') as stream:
            stream.seek(start); data = stream.read(size + 1)
        text = data[:size].decode('utf-8', errors='replace')
        record_usage(node_dir, [dict(file_id=file_id, action='read', sha256=row['sha256'])])
        return dict(row, absolute_path=str(path), offset=start, content=text,
                    next_offset=start + size if len(data) > size else None)
    record_usage(node_dir, [dict(file_id=file_id, action='read', sha256=row['sha256'])])
    return dict(row, absolute_path=str(path), content=text[start:start + size],
                next_offset=start + size if start + size < len(text) else None)


def reuse_file(task_dir, node_dir, file_id, with_dependencies=True):
    row, path = get_record(task_dir, file_id)
    value = read_json(Path(task_dir) / 'inheritance.json', {})
    prefix = PurePosixPath(row['source_path']).parent
    bundle = ([r for r in value['files'] if r['source_task_id'] == row['source_task_id']
        and (PurePosixPath(r['source_path']).is_relative_to(prefix) if prefix != PurePosixPath('.')
             else PurePosixPath(r['source_path']).parent == prefix)] if with_dependencies else [row])
    # Copy sibling inputs together; do not copy receipts over current-node logs.
    excluded = {'artifact_manifest.json', 'inheritance_usage.json', 'node_log.json', 'node.json'}
    pending = []
    for r in bundle:
        if Path(r['source_path']).name in excluded and r['file_id'] != file_id:
            continue
        _, source = get_record(task_dir, r['file_id'])
        relative = str(PurePosixPath(r['source_path']).relative_to(prefix)) if with_dependencies else Path(r['source_path']).name
        destination = safe_path(node_dir, relative)
        if destination.exists() and file_hash(destination) != r['sha256']:
            raise ValueError('Refusing to overwrite current node file: ' + relative)
        pending.append((r, source, destination))
    for r, source, destination in pending:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
        if file_hash(destination) != r['sha256']:
            raise ValueError('Copied inherited input failed hash check')
    record_usage(node_dir, [dict(file_id=r['file_id'], action='copied', sha256=r['sha256'],
                                destination=d.relative_to(Path(node_dir).resolve()).as_posix()) for r, _, d in pending])
    return dict(copied=[dict(file_id=r['file_id'], path=d.relative_to(Path(node_dir).resolve()).as_posix(), absolute_path=str(d),
                            sha256=r['sha256'], historical_status=r['historical_status']) for r, _, d in pending],
                note='Inputs copied; no new acceptance or score has been granted.')


def baseline_previews(task_dir, node_dir):
    """Deliver a bounded source preview even if the solver never searches files."""
    value = read_json(Path(task_dir) / 'inheritance.json', {}) or {}
    rows = [r for r in value.get('files', []) if r['historical_status'] == 'accepted' and r['kind'] == 'py']
    rows.sort(key=lambda r: (r['source_task_id'] != value.get('parent_task_id'),
                             not r['source_path'].startswith('node_'), r['source_path']))
    seen, blocks, events = set(), [], []
    for row in rows:
        if row['sha256'] in seen: continue
        _, path = get_record(task_dir, row['file_id'])
        with path.open('rb') as stream: content = stream.read(6001)
        seen.add(row['sha256'])
        blocks.append(dict(file_id=row['file_id'], source_path=row['source_path'],
            absolute_path=str(path), sha256=row['sha256'], content=content[:6000].decode('utf-8', errors='replace'),
            truncated=len(content) > 6000))
        events.append(dict(file_id=row['file_id'], action='context_attached', sha256=row['sha256']))
        if len(blocks) >= 3: break
    if events: record_usage(node_dir, events)
    return blocks


def finish_reuse(task_dir, node_dir, result):
    """Record explicit unchanged declarations; actual final acceptance stays Critic-owned."""
    if not isinstance(result, dict):
        return
    events = []
    for row in result.get('reused_files', []) if isinstance(result.get('reused_files'), list) else []:
        if not isinstance(row, dict): continue
        try:
            old, path = get_record(task_dir, row.get('file_id'))
            current = Path(node_dir) / Path(old['source_path']).name
            if (row.get('impact_checked') is True and str(row.get('unchanged_reason', '')).strip()
                    and current.is_file() and file_hash(current) == old['sha256']):
                events.append(dict(file_id=old['file_id'], action='unchanged_declared',
                    reason=row['unchanged_reason'], sha256=old['sha256']))
        except (OSError, ValueError): pass
    if events: record_usage(node_dir, events)


def inheritance_view(task_dir, offset=0, limit=50, query=''):
    root = Path(task_dir)
    value = read_json(root / 'inheritance.json', {}) or {}
    usage = []
    for path in sorted(root.glob('node_*/inheritance_usage.json')):
        for event in (read_json(path, {}) or {}).get('events', []):
            usage.append(dict(event, node_id=path.parent.name))
    inventory = list_files(root, query, offset, limit)
    by_id = {}
    node_manifests = {}
    for event in usage:
        event = dict(event)
        node_name = event['node_id']
        if event.get('action') == 'copied':
            if node_name not in node_manifests:
                node_manifests[node_name] = read_json(root/node_name/'artifact_manifest.json', {}) or {}
            manifest = node_manifests[node_name]
            receipt = next((r for r in manifest.get('artifacts', [])
                            if r.get('node_path') == event.get('destination')), {})
            if manifest.get('node_accepted') and receipt.get('status') in ('accepted','published'):
                event['current_status'] = ('accepted_unchanged' if receipt.get('sha256') == event.get('sha256')
                                           else 'accepted_modified')
            elif receipt.get('sha256') and receipt['sha256'] != event.get('sha256'):
                event['current_status'] = 'modified_unaccepted'
        by_id.setdefault(event.get('file_id'), []).append(event)
    for row in inventory['files']:
        row['usage'] = by_id.get(row['file_id'], [])
    return dict(inventory, inherited=bool(value), mode=value.get('mode'),
        parent_task_id=value.get('parent_task_id'), sources=[dict(task_id=s['task_id'], outcome=s['outcome'])
        for s in value.get('sources', [])], conditions=value.get('conditions', []),
        file_count=len(value.get('files', [])), read_count=len({e['file_id'] for e in usage if e['action'] == 'read'}),
        copied_count=len({e['file_id'] for e in usage if e['action'] == 'copied'}),
        context_count=len({e['file_id'] for e in usage if e['action'] == 'context_attached'}),
        unchanged_count=len({e['file_id'] for e in usage if e['action'] == 'unchanged_declared'}),
        plan=(read_json(root / 'contract.json', {}) or {}).get('continuation_plan'),
        note='Tool reads and copy receipts are recorded; arbitrary Python filesystem reads are not inferred.')


def tool_schemas():
    specs = [
        ('list_inherited_files', 'Search/page the complete inherited inventory, including old contracts, code, data, reviews and failed nodes.',
         dict(query={'type': 'string'}, offset={'type': 'integer'}, limit={'type': 'integer'}), []),
        ('read_inherited_file', 'Hash-check and read an inherited file. PDF yields text; binary yields a verified path. Text offset/limit are UTF-8 bytes.',
         dict(file_id={'type': 'string'}, offset={'type': 'integer'}, limit={'type': 'integer'}), ['file_id']),
        ('reuse_inherited_file', 'Hash-check and copy inherited inputs to this node; include sibling dependencies by default. No acceptance is inherited.',
         dict(file_id={'type': 'string'}, with_dependencies={'type': 'boolean'}), ['file_id'])]
    return [dict(type='function', function=dict(name=n, description=d,
        parameters=dict(type='object', properties=p, required=r))) for n, d, p, r in specs]
