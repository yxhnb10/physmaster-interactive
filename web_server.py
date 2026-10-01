"""Local PhysMaster UI. No extra server dependencies; uses existing PyYAML."""
import argparse
import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import yaml
from utils.live_updates import atomic_json, submit_update, set_control
from utils.research_monitor import list_rounds, safe_read

TERMINAL = {'finished', 'failed'}


class TaskManager:
    def __init__(self, project_root, config_path):
        self.root = Path(project_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.jobs = {}
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.closed = False

    def create(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 100000:
            raise ValueError('请输入任务描述（最多100000字符）')
        with self.lock:
            if self.closed:
                raise ValueError('服务正在关闭')
            # One active worker avoids contention in shared prior/wisdom indexes.
            if any(j['state'] not in TERMINAL for j in self.jobs.values()):
                raise ValueError('当前已有任务运行，请完成后再创建新任务')
            config = copy.deepcopy(yaml.safe_load(self.config_path.read_text(encoding='utf-8')))
            if not isinstance(config, dict):
                raise ValueError('配置文件格式错误')
            ident = uuid.uuid4().hex
            run_root = self.root / 'outputs' / 'web' / ident
            run_root.mkdir(parents=True)
            query_path = run_root / 'query.txt'
            query_path.write_text(query.strip(), encoding='utf-8')
            task_dir = run_root / 'query'
            config.setdefault('pipeline', {}).update(query_file=str(query_path),
                output_path=str(run_root), live_updates_enabled=True)
            # Use original project root as cwd so existing resource paths work.
            cfg_path = run_root / 'web_config.yaml'
            cfg_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            try:
                cfg_path.chmod(0o600)
            except OSError:
                pass
            set_control(task_dir, False)
            env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
            proc = subprocess.Popen([sys.executable, '-u', str(self.root/'run.py'),
                '--cfg_file', str(cfg_path)], cwd=self.root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace')
            job = dict(id=ident, state='starting', query=query.strip(), task_dir=task_dir,
                process=proc, logs=deque(maxlen=250), messages=[], exit_code=None,
                created_at=time.time(), log_path=run_root/'console.log', paused_requested=False)
            self.jobs[ident] = job
            threading.Thread(target=self._watch, args=(job,), daemon=False).start()
            threading.Thread(target=self._pump, args=(job,), daemon=True).start()
            return ident

    def _pump(self, job):
        # Deliver early queued messages even if the browser is closed.
        while True:
            with self.lock:
                if job['state'] in TERMINAL:
                    return
                try:
                    self._refresh(job)
                except (OSError, ValueError) as exc:
                    job['logs'].append('Progress read error: ' + str(exc))
            time.sleep(0.2)

    def _watch(self, job):
        try:
            with job['log_path'].open('w', encoding='utf-8') as log:
                for line in job['process'].stdout:
                    log.write(line)
                    log.flush()
                    with self.lock:
                        job['logs'].append(line.rstrip()[:4000])
            code = job['process'].wait()
            with self.lock:
                job['exit_code'] = code
                job['state'] = 'finished' if code == 0 else 'failed'
        except Exception as exc:
            with self.lock:
                job['state'] = 'failed'
                job['logs'].append(str(exc))
        finally:
            job['process'].stdout.close()
            # Retain pending user messages, but never label them as applied.
            with self.lock:
                for m in job['messages']:
                    if m['status'] in ('waiting', 'queued'):
                        receipt = self._receipt(job, m)
                        if receipt:
                            m.update(status='applied', revision=receipt['revision'])
                        else:
                            m['status'] = 'not_applied'

    def _get(self, ident):
        if ident not in self.jobs:
            raise KeyError('任务不存在；服务器重启后需要重新创建任务')
        return self.jobs[ident]

    def _receipt(self, job, message):
        path = message.get('_path')
        if path:
            ack = Path(path).parent / 'acks' / Path(path).name
            if ack.exists():
                return safe_read(ack)
        return None

    def _refresh(self, job):
        root = job['task_dir'] / 'live_updates'
        active_path = root / 'active.json'
        active = safe_read(active_path)
        progress = root / 'progress.json'
        data = safe_read(progress) or {}
        if job['state'] not in TERMINAL:
            if active and active['status'] == 'closed':
                job['state'] = 'summarizing'
            else:
                job['state'] = data.get('state', job['state'])
            for m in job['messages']:
                if m['status'] == 'waiting' and active and active['status'] == 'running':
                    try:
                        m['_path'] = str(submit_update(job['task_dir'], m['text']))
                        m['status'] = 'queued'
                    except RuntimeError:
                        m['status'] = 'not_applied'
                if m['status'] == 'waiting' and active and active['status'] == 'closed':
                    m['status'] = 'not_applied'
        for m in job['messages']:
            receipt = self._receipt(job, m)
            if receipt:
                m.update(status='applied', revision=receipt['revision'])
        return data

    def snapshot(self, ident):
        with self.lock:
            job = self._get(ident)
            progress = self._refresh(job)
            summary = job['task_dir']/'summary.md'
            return dict(id=ident, state=job['state'], query=job['query'],
                round=progress.get('round', 0), revision=progress.get('revision', 0),
                paused_requested=job['paused_requested'], exit_code=job['exit_code'],
                messages=[{k:v for k,v in m.items() if not k.startswith('_')} for m in job['messages']],
                logs=list(job['logs']), summary=summary.read_text(encoding='utf-8') if summary.exists() else '',
                task_dir=str(job['task_dir']))

    def update(self, ident, text):
        if not isinstance(text, str) or not text.strip() or len(text)>100000:
            raise ValueError('请输入补充条件（最多100000字符）')
        with self.lock:
            job = self._get(ident)
            self._refresh(job)
            if job['state'] in TERMINAL or job['state'] == 'summarizing':
                raise ValueError('任务已结束研究阶段，请创建新任务')
            m = dict(id=uuid.uuid4().hex, text=text.strip(), status='waiting')
            job['messages'].append(m)
            self._refresh(job)
            return {k:v for k,v in m.items() if not k.startswith('_')}

    def control(self, ident, action):
        if action not in ('pause', 'resume'):
            raise ValueError('无效操作')
        with self.lock:
            job = self._get(ident)
            self._refresh(job)
            if job['state'] in TERMINAL or job['state'] == 'summarizing':
                raise ValueError('当前阶段不能暂停或继续')
            # Flush all pending messages before releasing a paused worker.
            self._refresh(job)
            paused = action == 'pause'
            set_control(job['task_dir'], paused)
            job['paused_requested'] = paused
            return dict(requested=action)

    def close(self):
        # Stop accepting tasks. Allow jobs to finish; never silently kill workers.
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                if job['state'] not in TERMINAL:
                    self._refresh(job)
                    set_control(job['task_dir'], False)


def make_handler(manager):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, content_type='application/json; charset=utf-8'):
            data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def trusted_host(self):
            # Reject alternate Host headers (including DNS rebinding attempts).
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',
                                               f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.trusted_host():
                return self.reply({'error':'Invalid host'}, 403)
            path = urlparse(self.path).path
            if path == '/':
                html = (Path(__file__).parent/'web'/'index.html').read_text(encoding='utf-8')
                return self.reply(html.replace('__TOKEN__', manager.token).encode(), content_type='text/html; charset=utf-8')
            if path == '/api/tasks':
                with manager.lock:
                    ids = list(manager.jobs)
                return self.reply({'tasks':[manager.snapshot(i) for i in ids]})
            monitor_match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(rounds|artifact)',path)
            if monitor_match:
                try:
                    with manager.lock:
                        job=manager._get(monitor_match[1])
                        task_dir=job['task_dir'].resolve()
                    if monitor_match[2]=='rounds':
                        return self.reply({'rounds':list_rounds(task_dir)})
                    relative=parse_qs(urlparse(self.path).query).get('path',[''])[0]
                    artifact=(task_dir/relative).resolve()
                    # Serve only node output files, never runtime configs or secrets.
                    parts=Path(relative).parts
                    if (not parts or not re.fullmatch(r'node_\d+',parts[0])
                            or not artifact.is_relative_to(task_dir) or not artifact.is_file()):
                        return self.reply({'error':'文件不存在或不允许访问'},404)
                    return self.reply(artifact.read_bytes(),content_type='application/octet-stream')
                except (KeyError,OSError) as exc:
                    return self.reply({'error':'任务或文件不存在'},404)
            match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})', path)
            if match:
                try:
                    return self.reply(manager.snapshot(match[1]))
                except KeyError as e:
                    return self.reply({'error':str(e)},404)
            return self.reply({'error':'Not found'},404)

        def do_POST(self):
            if not self.trusted_host() or self.headers.get('X-PhysMaster-Token') != manager.token:
                return self.reply({'error':'请求校验失败，请刷新本地页面'},403)
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return self.reply({'error':'Invalid origin'},403)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 500000:
                    raise ValueError('请求过大或为空')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict): raise ValueError('请求格式错误')
                path = urlparse(self.path).path
                if path == '/api/tasks':
                    return self.reply({'id':manager.create(body.get('query'))},201)
                match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(updates|control)',path)
                if not match: return self.reply({'error':'Not found'},404)
                result = manager.update(match[1],body.get('text')) if match[2]=='updates' else manager.control(match[1],body.get('action'))
                return self.reply(result,202)
            except KeyError as e:
                self.reply({'error':str(e)},404)
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self.reply({'error':str(e)},400)
            except Exception:
                self.reply({'error':'操作失败，请检查配置文件、依赖及服务器终端'},500)
    return Handler


def main():
    parser=argparse.ArgumentParser(description='PhysMaster local chat UI')
    parser.add_argument('--cfg_file','-c',default='config.yaml')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    cfg=Path(args.cfg_file)
    if not cfg.is_absolute(): cfg=root/cfg
    manager=TaskManager(root,cfg)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(manager))
    print(f'PhysMaster Chat: http://127.0.0.1:{server.server_port}',flush=True)
    print('关闭网页不会停止研究。请等任务完成后再关闭服务器。',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务器停止接收请求，正在等待已运行任务完成。',flush=True)
    finally:
        manager.close()
        server.server_close()

if __name__=='__main__':
    main()
