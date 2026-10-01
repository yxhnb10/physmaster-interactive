"""Patch only JSON publication and non-critical progress writes in live_updates.py."""
import argparse
import ast
import datetime
import os
from pathlib import Path

ATOMIC_JSON = '''def atomic_json(path, value):
    """Publish JSON atomically; tolerate short Windows reader/AV file locks."""
    import json
    import os
    import time
    import uuid
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # Keep the old valid document until replacement actually succeeds.
        # Do not unlink the target or rewrite it in place.
        for attempt in range(30):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as exc:
                if attempt == 29:
                    if hasattr(exc, 'add_note'):
                        exc.add_note(f'Cannot replace {path} after 30 attempts. '
                                     'Check persistent file locks/read-only permissions.')
                    raise
                time.sleep(min(0.01 * (2 ** min(attempt, 4)), 0.1))
    finally:
        # Cleanup must never hide the original publication error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
'''

PROGRESS = '''    def progress(self, state, round_index=0, revision=0):
        # UI progress is advisory; failure must not abort the research task.
        # Contract, update queue, acknowledgments and control remain strict.
        try:
            atomic_json(self.root / 'progress.json', {
                'state': state, 'round': round_index, 'revision': revision,
            })
        except PermissionError as exc:
            if not getattr(self, '_progress_write_warned', False):
                print(f'[LiveUpdate] Progress file is locked or not writable; '
                      f'research continues, UI progress may lag: {exc}', flush=True)
                self._progress_write_warned = True
        else:
            self._progress_write_warned = False
'''


def patched_source(source):
    tree = ast.parse(source)
    atomic = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == 'atomic_json'), None)
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == 'UpdateInbox'), None)
    progress = next((n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == 'progress'), None) if cls else None
    if atomic is None or progress is None:
        raise ValueError('Expected atomic_json and UpdateInbox.progress not found; no file changed')
    if atomic.decorator_list or progress.decorator_list:
        raise ValueError('Unexpected decorated functions; no file changed')
    lines = source.splitlines(keepends=True)
    replacements = [(atomic.lineno-1, atomic.end_lineno, ATOMIC_JSON),
                    (progress.lineno-1, progress.end_lineno, PROGRESS)]
    for start, end, text in sorted(replacements, reverse=True):
        lines[start:end] = [text]
    result = ''.join(lines)
    compile(result, 'live_updates.py', 'exec')
    return result


def patch_project(project):
    path = Path(project).resolve() / 'utils' / 'live_updates.py'
    source = path.read_text(encoding='utf-8-sig')
    result = patched_source(source)
    if source == result:
        print('Already patched:', path)
        return path
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = path.with_name('live_updates.py.backup_' + timestamp)
    backup.write_bytes(path.read_bytes())
    temporary = path.with_name('.live_updates.fix_' + timestamp + '.tmp')
    try:
        temporary.write_text(result, encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print('Fixed:', path)
    print('Backup:', backup)
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fix Windows progress.json publication locks')
    parser.add_argument('--project', default='.', help='Project root containing utils/live_updates.py')
    args = parser.parse_args()
    patch_project(args.project)
