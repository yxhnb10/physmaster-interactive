"""Elapsed wall time and process-local operation counters; no prompt data."""
import copy
import json
import os
import time
import uuid
from contextlib import contextmanager, nullcontext
from functools import wraps
from pathlib import Path
from utils.live_updates import atomic_json


def _publish(path, data):
    # Telemetry must never abort a model call or the research pipeline.
    try:
        atomic_json(path, data)
    except OSError as exc:
        print(f'[Timing] Could not publish timing: {exc}', flush=True)


class RunTimer:
    """One writer, mutually exclusive phases measured with monotonic time."""
    def __init__(self, task_dir, clock=None, wall_clock=None):
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.path = Path(task_dir) / 'runtime.json'
        self.started = self.clock()
        self.phase_started = self.started
        self.current = 'initializing'
        self.phases = {}
        self.state = 'running'
        self._save()

    def change(self, phase):
        if self.state != 'running' or phase == self.current:
            return
        now = self.clock()
        self.phases[self.current] = self.phases.get(self.current, 0.0) + max(0.0, now - self.phase_started)
        self.current, self.phase_started = phase, now
        self._save()

    @contextmanager
    def stage(self, phase):
        previous = self.current
        self.change(phase)
        try:
            yield
        finally:
            self.change(previous)

    def snapshot(self):
        now = self.clock()
        phases = dict(self.phases)
        if self.state == 'running':
            phases[self.current] = phases.get(self.current, 0.0) + max(0.0, now - self.phase_started)
        return dict(state=self.state, current=self.current if self.state == 'running' else None,
                    seconds=sum(phases.values()), phases=phases, sampled_at=self.wall_clock())

    def _save(self):
        _publish(self.path, self.snapshot())

    def finish(self, success):
        if self.state != 'running':
            return
        now = self.clock()
        self.phases[self.current] = self.phases.get(self.current, 0.0) + max(0.0, now - self.phase_started)
        self.state = 'finished' if success else 'failed'
        self._save()


def timed_stage(phase, enabled=None):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            timer = getattr(self, 'timing', None)
            if enabled and not getattr(self, enabled, False):
                timer = None
            with timer.stage(phase) if timer else nullcontext():
                return fn(self, *args, **kwargs)
        return wrapped
    return decorate


class OperationRecorder:
    """Each process writes its own file, avoiding concurrent read/modify/write."""
    def __init__(self, root):
        self.path = Path(root) / 'timing_operations' / f'{os.getpid()}_{uuid.uuid4().hex}.json'
        self.stats = {}
        self.active = []

    def _save(self):
        now = time.monotonic()
        stats = copy.deepcopy(self.stats)
        for name, start in self.active:
            stats[name]['seconds'] += max(0.0, now - start)
        _publish(self.path, dict(stats=stats, active=[name for name, _ in self.active],
                                 sampled_at=time.time()))

    @contextmanager
    def measure(self, name):
        record = self.stats.setdefault(name, dict(seconds=0.0, calls=0, failures=0))
        record['calls'] += 1
        entry = (name, time.monotonic())
        self.active.append(entry)
        self._save()
        try:
            yield
        except BaseException:
            record['failures'] += 1
            raise
        finally:
            record['seconds'] += max(0.0, time.monotonic() - entry[1])
            self.active.remove(entry)
            self._save()


_recorders = {}


@contextmanager
def measure_operation(name):
    root = os.environ.get('PHY_TIMING_ROOT')
    if not root:
        yield
        return
    key = (root, os.getpid())
    if key not in _recorders:
        _recorders[key] = OperationRecorder(root)
    recorder = _recorders[key]
    with recorder.measure(name):
        yield


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_timing(task_dir, *, terminal=False, finished_at=None):
    """Extend active samples for display. Finished records never keep ticking."""
    now = finished_at if terminal else time.time()
    runtime = _read(Path(task_dir) / 'runtime.json')
    phases = dict(runtime.get('phases') or {})
    current = runtime.get('current')
    if current and runtime.get('state') == 'running':
        phases[current] = phases.get(current, 0.0) + (max(0.0, now - runtime.get('sampled_at', now)) if now is not None else 0.0)
    operations = {}
    for path in (Path(task_dir) / 'timing_operations').glob('*.json'):
        data = _read(path)
        for name, item in (data.get('stats') or {}).items():
            dest = operations.setdefault(name, dict(seconds=0.0, calls=0, failures=0, running=0))
            for field in ('seconds', 'calls', 'failures'):
                dest[field] += item.get(field, 0)
        for name in data.get('active') or []:
            if name in operations:
                operations[name]['seconds'] += max(0.0, now - data.get('sampled_at', now)) if now is not None else 0.0
                if not terminal:
                    operations[name]['running'] += 1
    return dict(available=bool(runtime), current=None if terminal else current,
                phases=phases, pipeline_seconds=sum(phases.values()), operations=operations)
