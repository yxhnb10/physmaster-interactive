"""Bounded node-local file observations; never acceptance or execution proof."""
import os
import re
from pathlib import Path
from utils.research_integrity import EXTENSIONS, RESERVED, FILE_NAME_PATTERN, _node_file

INTERNAL = RESERVED | {'solver_progress.json', 'inheritance_usage.json', 'node_log.json'}


def required_file_names(expected):
    values = expected if isinstance(expected, list) else [expected]
    names = []
    for item in values:
        raw = item.get('path') if isinstance(item, dict) else item
        if isinstance(raw, str):
            names.extend(re.findall(FILE_NAME_PATTERN, raw, re.I))
    return sorted(set(names))


def node_inventory(node_dir, required=None, max_files=80, scan_limit=1000):
    """Observe even undeclared files, without following symlinks or executing code.

    A truncated/erroring scan never proves a required basename absent. Formats,
    hashes, declarations and actual execution still require the usual audits.
    """
    folder = Path(node_dir)
    result = dict(scope='node-local disk inventory; not accepted output or execution evidence',
                  files=[], required_files=[], truncated=False, errors=[])
    if folder.is_symlink():
        result['errors'].append('节点目录是符号链接，未扫描')
        return result
    if not folder.is_dir():
        result['errors'].append('节点目录不存在，未扫描')
        return result
    root = folder.resolve(); rows=[]; visited=0; directories=0
    def onerror(exc):result['errors'].append(type(exc).__name__ + ': ' + str(exc))
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        directories += 1
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not (Path(parent)/d).is_symlink())
        if directories > 200:
            result['truncated'] = True
            break
        for name in sorted(files):
            if name in INTERNAL or Path(name).suffix.lower() not in EXTENSIONS:continue
            visited += 1
            if visited > scan_limit:
                result['truncated'] = True
                break
            path = Path(parent)/name
            relative = path.relative_to(root).as_posix()
            try:
                verified = _node_file(root, relative)
                rows.append(dict(path=relative, bytes=verified.stat().st_size, status='present_unverified'))
            except (OSError, ValueError) as exc:
                rows.append(dict(path=relative, status='invalid', error=str(exc)))
        if visited > scan_limit:break
    for name in required or []:
        matches = [row for row in rows if Path(row['path']).name == name]
        present = [row['path'] for row in matches if row['status'] == 'present_unverified']
        status = ('present_unverified' if present else 'invalid' if matches else
                  'unknown' if result['truncated'] or result['errors'] else 'not_found')
        result['required_files'].append(dict(name=name, status=status, paths=present))
    required_names=set(required or [])
    rows.sort(key=lambda row:(Path(row['path']).name not in required_names,row['path']))
    result.update(files=rows[:max_files], observed_count=len(rows), inventory_rows_truncated=len(rows)>max_files)
    return result
