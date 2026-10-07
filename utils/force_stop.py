"""Linux task-scoped emergency stop. Pin process identities with pidfds."""
import json
import os
import re
import signal
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from utils.live_updates import atomic_json
from utils.recovery import freeze_timing, run_locked

VERSION = 'v9.6.3-flash-force-stop-1'
TERMINAL = {'finished', 'partial', 'failed', 'interrupted'}


def supported():
    return os.name == 'posix' and Path('/proc/self/stat').exists() and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal')


def process_record(pid):
    try:
        path = Path('/proc') / str(int(pid))
        tail = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return dict(pid=int(pid), state=tail[0], ppid=int(tail[1]), pgrp=int(tail[2]),
                    session=int(tail[3]), start_ticks=int(tail[19]), uid=path.stat().st_uid)
    except (OSError, ValueError, IndexError):
        return None


def same_process(record, identity):
    return record and all(record.get(k) == identity.get(k) for k in ('pid', 'start_ticks', 'uid', 'session', 'pgrp'))


def capture_process(proc, task_dir, project):
    if not supported():
        return None
    row = process_record(proc.pid)
    if row is None or row['state']=='Z':
        return None  # A very short-lived worker already exited.
    try:args = (Path('/proc') / str(proc.pid) / 'cmdline').read_bytes().split(b'\0')
    except OSError:return None
    required = [os.fsencode(str(Path(project) / 'run.py')), b'--cfg_file',
                os.fsencode(str(Path(task_dir).parent / 'web_config.yaml'))]
    if not all(arg in args for arg in required) or row['session'] != proc.pid or row['pgrp'] != proc.pid:
        return None  # Refuse future signals, but still let the regular watcher run.
    row['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return row


def targets(identity, task_dir):
    """Session members plus descendants, including children with their own session."""
    if not isinstance(identity, dict):
        raise ValueError('缺少研究进程身份记录；不能安全地强制停止，请查看服务器日志')
    if identity.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        return []  # No process from the old boot survives.
    rows = {}
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            r = process_record(int(p.name))
            if r and r['state'] != 'Z' and r['uid'] == identity['uid'] and r['start_ticks'] >= identity['start_ticks']:
                rows[r['pid']] = r
    root = rows.get(identity['pid'])
    chosen = {identity['pid']} if same_process(root, identity) else set()
    marker = b'PHY_TIMING_ROOT=' + os.fsencode(str(Path(task_dir)))
    for pid, r in rows.items():
        if pid == os.getpid():
            continue
        # Environment corroborates reparented children after a leader exits.
        try:
            owned = marker in (Path('/proc') / str(pid) / 'environ').read_bytes().split(b'\0')
        except OSError:
            owned = False
        if owned:
            chosen.add(pid)
    while True:
        children = {pid for pid, r in rows.items() if r['ppid'] in chosen and pid != os.getpid()}
        if children <= chosen:
            break
        chosen |= children
    return [rows[pid] for pid in chosen if pid in rows and pid != os.getpid()]


def kill_task_tree(identity, task_dir):
    """Freeze verified targets before enumeration; SIGKILL through pinned handles."""
    if not supported():
        raise ValueError('强制停止需要支持 pidfd 的 Ubuntu/Linux；当前环境不支持')
    pinned = {}; failure = None; killed = 0
    try:
        deadline = time.monotonic() + 3
        stable = 0
        while stable < 2 and time.monotonic() < deadline:
            added = 0
            for row in targets(identity, task_dir):
                key = (row['pid'], row['start_ticks'])
                if key in pinned:
                    continue
                try:
                    fd = os.pidfd_open(row['pid'])
                except ProcessLookupError:
                    continue
                if not same_process(process_record(row['pid']), row):
                    os.close(fd); continue
                pinned[key] = (fd, row); added += 1
                try:
                    signal.pidfd_send_signal(fd, signal.SIGSTOP)
                except ProcessLookupError:
                    pass
            stable = 0 if added else stable + 1
            time.sleep(.04)
    except Exception as exc:
        failure = exc
    finally:
        # Never leave a process we froze suspended, even if enumeration fails.
        for fd, row in pinned.values():
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL); killed += 1
            except ProcessLookupError:
                pass
            except OSError as exc:
                failure = failure or exc
                try:signal.pidfd_send_signal(fd, signal.SIGCONT)
                except OSError:pass
        for fd, row in pinned.values():
            os.close(fd)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not targets(identity, task_dir) and not run_locked(task_dir):
            if failure:
                raise failure
            return killed
        time.sleep(.05)
    raise RuntimeError('仍有研究进程或工作锁未释放；没有宣称停止成功，请重试或检查服务器日志')


class TaskForceStopMixin:
    def _refresh(self, job):
        if job.get('force_stop', {}).get('state') == 'stopping':
            from utils.research_monitor import safe_read
            return safe_read(job['task_dir'] / 'live_updates/progress.json') or {}
        return super()._refresh(job)

    def force_stop_task(self, ident, actor):
        with self.lock:
            job = self._get(ident)
            if job['state'] == 'queued':
                return self.cancel_queued(ident)
            existing = job.get('force_stop') or {}
            if existing.get('state') == 'stopped':
                return dict(ok=True, state=job['state'], force_stop=existing)
            if existing.get('state') == 'stopping' and job.get('_force_stop_thread', None) and job['_force_stop_thread'].is_alive():
                return dict(ok=True, state='stopping', force_stop=existing)
            identity = job.get('process_identity')
            if not supported():
                raise ValueError('当前环境不支持任务进程树强制停止')
            if job['state'] in TERMINAL and not targets(identity, job['task_dir']):
                return dict(ok=True, state=job['state'], notice='任务已经结束，无需停止')
            if not isinstance(identity, dict):
                raise ValueError('此任务缺少安全进程身份记录，无法强制停止；请查看服务器日志')
            # Write the interruption request before sending any signals.
            atomic_json(job['task_dir'] / 'live_updates/control.json', dict(paused=False, stop_requested=True, force_stop_requested=True))
            job['force_stop'] = dict(state='stopping', requested_at=time.time(), requested_by=actor)
            job['state'] = 'stopping'
            job['logs'].append('[ForceStop] 用户请求强制停止研究及计算子进程；已落盘文件保留。')
            self._save_job(job)
            thread = threading.Thread(target=self._finish_force_stop, args=(job,), daemon=True)
            job['_force_stop_thread'] = thread
            thread.start()
            return dict(ok=True, state='stopping', force_stop=dict(job['force_stop']))

    def _finish_force_stop(self, job):
        try:
            count = kill_task_tree(job['process_identity'], job['task_dir'])
            proc = job.get('process')
            if proc is not None:
                proc.wait(timeout=2)
                done=job.get('_watch_done')
                if done is not None and not done.wait(timeout=3):
                    raise RuntimeError('进程已终止，但日志收尾尚未完成；请重试核对停止状态')
            freeze_timing(job['task_dir'])
            with self.lock:
                job['force_stop'].update(state='stopped', finished_at=time.time(), killed_processes=count)
                job.update(state='interrupted', paused_requested=False)
                if proc is not None:job['exit_code'] = proc.returncode
                self._finish_timing(job)
                job['logs'].append('[ForceStop] 已强制停止；可从已有安全断点恢复，未完成的调用或计算需要重做。')
                self._save_job(job)
                self._start_next()
        except Exception as exc:
            with self.lock:
                job['force_stop'].update(state='failed', error=str(exc), finished_at=time.time())
                job['state'] = 'reconnecting'  # Keep queue blocked until verified stopped.
                job['logs'].append('[ForceStop] 未确认停止成功：' + str(exc))
                self._save_job(job)


def make_force_stop_handler(Base, manager, store=None):
    class Handler(Base):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/v963/health':
                if self.client_address[0] not in ('127.0.0.1', '::1') or not self.trusted_host():
                    return self.reply({'error': 'Not found'}, 404)
                from utils.ui_translation import TRANSLATION_MODEL
                return self.reply(dict(ok=supported(), team=bool(store), version=VERSION, translation_model=TRANSLATION_MODEL, force_stop=supported()))
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/force-stop', path)
            if not match:
                return super().do_POST()
            user = self._run_user(path, write=True)
            if not user:
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:raise ValueError('停止请求格式无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or body.get('confirm') is not True:raise ValueError('请明确确认强制停止')
                result = manager.force_stop_task(match[1], user['username'])
                return self.reply(result, 202 if result.get('state') == 'stopping' else 200)
            except KeyError:
                return self.reply({'error': '任务不存在'}, 404)
            except (ValueError, TypeError) as exc:
                return self.reply({'error': str(exc)}, 400)
            except OSError:
                return self.reply({'error': '停止请求无法保存，未确认停止；请检查服务器日志'}, 503)
    return Handler
