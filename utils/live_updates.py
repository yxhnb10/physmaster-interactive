"""Atomic, per-run update inbox. Standard library only."""
import json
import os
import time
import uuid
from pathlib import Path


def atomic_json(path, value):
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
        replace_with_retry(tmp, path)
    finally:
        # Cleanup must never hide the original publication error.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def replace_with_retry(tmp, path):
    """Use bounded retries for transient Windows reader locks, without deleting the old file."""
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


class UpdateInbox:
    def __init__(self, task_dir, timing=None):
        self.timing = timing
        self.root = Path(task_dir) / 'live_updates'
        self.session = uuid.uuid4().hex
        self.directory = self.root / self.session
        self.directory.mkdir(parents=True)
        self.seen = set()
        atomic_json(self.root / 'active.json', {'session': self.session, 'status': 'running'})

    def pending(self):
        items = []
        for path in sorted(self.directory.glob('*.json')):
            if path.name in self.seen:
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data.get('text'), str) or not data['text'].strip():
                raise ValueError(f'Invalid update: {path.name}')
            items.append((path.name, data['text']))
        return items

    def acknowledge(self, items, revision):
        for name, _ in items:
            atomic_json(self.directory / 'acks' / name, {'revision': revision, 'status': 'applied'})
            self.seen.add(name)

    def progress(self, state, round_index=0, revision=0):
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

    def checkpoint(self, round_index, revision):
        announced = False
        previous = self.timing.current if self.timing else None
        while True:
            control = self.root / 'control.json'
            paused = json.loads(control.read_text(encoding='utf-8')).get('paused', False) if control.exists() else False
            if not paused:
                if announced and self.timing:
                    self.timing.change(previous)
                self.progress('running', round_index, revision)
                return
            if not announced:
                if self.timing:
                    self.timing.change('paused')
                print('[LiveUpdate] Paused at round boundary', flush=True)
                self.progress('paused', round_index, revision)
                announced = True
            time.sleep(0.2)

    def close(self):
        atomic_json(self.root / 'active.json', {'session': self.session, 'status': 'closed'})


def submit_update(task_dir, text):
    if not text.strip():
        raise ValueError('Update cannot be empty')
    root = Path(task_dir) / 'live_updates'
    active = json.loads((root / 'active.json').read_text(encoding='utf-8'))
    if active['status'] != 'running':
        raise RuntimeError('This run has finished; start a new run first')
    name = f'{time.time_ns():020d}_{uuid.uuid4().hex}.json'
    atomic_json(root / active['session'] / name, {'text': text.strip()})
    return root / active['session'] / name


def set_control(task_dir, paused):
    """Pause is cooperative: observed at the next round boundary."""
    atomic_json(Path(task_dir) / 'live_updates' / 'control.json', {'paused': bool(paused)})
