"""Local PhysMaster UI. No extra server dependencies; uses existing PyYAML."""
import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from html import escape
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import yaml
from utils.live_updates import atomic_json, submit_update, set_control
from utils.research_monitor import list_rounds, safe_read
from utils.round_feed import RoundFeed
from utils.capabilities import capability_values, merge_capabilities, apply_capabilities, public_capabilities
from utils.critic_policy import critic_settings_from_config, merge_critic_settings, apply_critic_settings
from utils.runtime_timing import read_timing
from utils.hard_parameters import resolve_parameters, normalize_parameters, parameter_text
from utils.continuation import snapshot_research, inheritance_view, get_record
from utils.recovery import recovery_info, run_locked, process_alive, freeze_timing, read_checkpoint, validate_evidence
from utils.run_naming import TASK_ID, research_topic, valid_round, run_label, naming_fields

# Preserve the deployed site from the user-supplied web_server.py baseline.
DEFAULT_PUBLIC_URL = 'https://physmaster.tech'
TERMINAL = {'finished', 'partial', 'failed', 'interrupted'}


from utils.force_stop import TaskForceStopMixin, capture_process  # PHY_FORCE_STOP_V963 IMPORT

class TaskManager(TaskForceStopMixin):
    def __init__(self, project_root, config_path):
        self.root = Path(project_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.jobs = {}
        self.lock = threading.RLock()
        self.creation_lock = threading.RLock()
        self.round_feed = RoundFeed()
        self.token = secrets.token_urlsafe(32)
        self.closed = False
        self.queue_enabled = False
        self._restore_history()

    def _save_job(self, job):
        # Credentials remain in web_config.yaml, never in history metadata.
        data = {key: job[key] for key in ('id', 'state', 'query', 'original_query',
            'parent_id', 'messages', 'exit_code', 'created_at', 'capabilities', 'critic_settings')}
        data.update(started_at=job.get('started_at'), finished_at=job.get('finished_at'),
                    elapsed_seconds=job.get('elapsed_seconds'),hard_parameters=job.get('hard_parameters',[]))
        data.update(naming_fields(job))
        data.update({key:job.get(key) for key in ('owner_id','shared','archived','cancelled','worker_pid','recovery_attempts','process_identity','force_stop')})
        try:
            atomic_json(job['task_dir'].parent / 'task.json', data)
        except OSError as exc:
            if not job.get('_history_warning'):
                job['logs'].append('任务历史保存失败：' + str(exc))
                job['_history_warning'] = True

    def _restore_history(self):
        # Restore interrupted studies without automatically spending API credits.
        for run_root in sorted((self.root / 'outputs' / 'web').glob('*')):
            if not run_root.is_dir() or run_root.is_symlink():
                continue
            task_dir = run_root / 'query'
            meta = safe_read(run_root / 'task.json')
            if isinstance(meta, dict):
                if (meta.get('force_stop') or {}).get('state')=='stopping':
                    meta['state']='reconnecting'
                    meta['force_stop'].update(state='failed',error='服务器在强制停止期间重启；请再次点击强制停止以核对剩余进程')
                if meta.get('state') not in TERMINAL:
                    if meta.get('state') == 'queued':
                        meta.update(state='failed', cancelled=True)
                    elif run_locked(task_dir) or process_alive(meta.get('worker_pid')):
                        meta['state']='reconnecting'
                    else:
                        runtime=safe_read(task_dir/'runtime.json') or {}
                        meta.update(state='interrupted',finished_at=runtime.get('sampled_at',meta.get('started_at')))
                        freeze_timing(task_dir)
                ident = meta.get('id', run_root.name)
                if not isinstance(ident, str) or not TASK_ID.fullmatch(ident):
                    continue
            elif TASK_ID.fullmatch(run_root.name) and (task_dir / 'summary.md').is_file():
                meta = {'state': 'finished'}  # Completed v8 runs had no task.json.
                ident = run_root.name
            else:
                continue
            try:
                query = meta.get('query') or (run_root / 'query.txt').read_text(encoding='utf-8')
                if not isinstance(query, str) or not query.strip():
                    continue
                messages = meta.get('messages')
                if not isinstance(messages, list):
                    messages = []
                    # Recover v8 conditions from their existing inbox/receipts.
                    for path in sorted((task_dir / 'live_updates').glob('*/*.json'), key=lambda p: p.name):
                        item = safe_read(path)
                        if isinstance(item, dict) and isinstance(item.get('text'), str):
                            messages.append(dict(id=path.stem, text=item['text'],
                                status='not_applied', _path=str(path)))
                messages = [dict(m) for m in messages if isinstance(m, dict)
                    and isinstance(m.get('text'), str)]
                for message in messages:
                    receipt = self._receipt(None, message)
                    if isinstance(receipt, dict) and 'revision' in receipt:
                        message.update(status='applied', revision=receipt['revision'])
                    elif message.get('status') in ('waiting', 'queued') and meta['state'] not in ('interrupted','reconnecting'):
                        message['status'] = 'not_applied'
                log_path = run_root / 'console.log'
                logs = deque(maxlen=250)
                if log_path.is_file():
                    with log_path.open('rb') as stream:
                        stream.seek(max(0, log_path.stat().st_size - 65536))
                        logs.extend(stream.read().decode('utf-8', errors='replace').splitlines()[-250:])
                job = dict(id=ident, state=meta['state'], query=query,
                    original_query=meta.get('original_query', query), parent_id=meta.get('parent_id'),
                    task_dir=task_dir, process=None, logs=logs, messages=messages,
                    exit_code=meta.get('exit_code'), created_at=meta.get('created_at', run_root.stat().st_mtime),
                    log_path=log_path, paused_requested=False)
                job.update(worker_pid=meta.get('worker_pid'),recovery_attempts=meta.get('recovery_attempts') or 0)
                job.update(process_identity=meta.get('process_identity'),force_stop=meta.get('force_stop') or {})
                job.update(owner_id=meta.get('owner_id'),shared=meta.get('shared',False),archived=meta.get('archived',False),cancelled=meta.get('cancelled',False))
                if meta.get('cancelled'):job['logs'].append('排队任务未启动；服务器重启后请重新提交。')
                job.update(started_at=meta.get('started_at'), finished_at=meta.get('finished_at'),
                           elapsed_seconds=meta.get('elapsed_seconds'))
                if job['state']=='interrupted':
                    runtime=safe_read(task_dir/'runtime.json') or {}
                    job['elapsed_seconds']=max(job.get('elapsed_seconds') or 0,runtime.get('seconds') or 0)
                job.update(research_topic=research_topic(job['original_query'], meta.get('research_topic')),
                    research_round=valid_round(meta.get('research_round')),research_id=meta.get('research_id'))
                old_config = run_root / 'web_config.yaml'
                config = yaml.safe_load(old_config.read_text(encoding='utf-8')) if old_config.is_file() else {}
                job['capabilities'] = merge_capabilities(capability_values(config or {}), meta.get('capabilities'))
                job['critic_settings'] = merge_critic_settings(critic_settings_from_config(config or {}),
                                                               meta.get('critic_settings'))
                job['hard_parameters'] = normalize_parameters(meta['hard_parameters'] if 'hard_parameters' in meta
                    else (config or {}).get('integrity',{}).get('hard_parameters',[]))
                if job['id'] not in self.jobs:
                    self.jobs[job['id']] = job
                    if job['state']=='reconnecting':
                        threading.Thread(target=self._observe_detached,args=(job,),daemon=True).start()
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                continue
        self.jobs = dict(sorted(self.jobs.items(), key=lambda item: item[1]['created_at']))
        # Derive continuation numbering for old metadata without modifying or
        # moving historical files. A recursive lookup handles creation-order ties.
        complete = set()
        def resolve(job, visiting):
            if job['id'] in complete or job['id'] in visiting:
                return
            parent = self.jobs.get(job.get('parent_id'))
            if parent and parent['id'] not in visiting:
                resolve(parent, visiting | {job['id']})
            if job['research_round'] is None:
                job['research_round'] = (parent.get('research_round') or 1) + 1 if parent else 1
            if not isinstance(job.get('research_id'), str) or not TASK_ID.fullmatch(job['research_id']):
                job['research_id'] = parent.get('research_id') or parent['id'] if parent else job['id']
            job.update(naming_fields(job))
            complete.add(job['id'])
        for job in self.jobs.values():
            resolve(job, set())

    def capability_settings(self):
        config = yaml.safe_load(self.config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('配置文件格式错误')
        result = public_capabilities(config)
        result['critic_defaults'] = critic_settings_from_config(config)
        integrity=config.get('integrity',{}) or {}
        result['hard_parameter_default_text'] = integrity.get('hard_parameter_text') or parameter_text(
            normalize_parameters(integrity.get('hard_parameters',[])))
        return result

    def create(self, query, capabilities=None, critic_settings=None, hard_parameter_text=None, *, original_query=None, parent_id=None, initial_messages=None, inherited_parameters=None, inherited_task=None, research_title=None, owner_id=None, task_config=None, task_env=None, runtime_settings=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 100000:
            raise ValueError('请输入任务描述（最多100000字符）')
        topic = research_topic(original_query or query, research_title)
        with self.creation_lock:
            if self.closed:
                raise ValueError('服务正在关闭')
            # One active worker avoids contention in shared prior/wisdom indexes.
            busy = any(j['state'] not in TERMINAL and j['state'] != 'queued' for j in self.jobs.values())
            if busy and not self.queue_enabled:
                raise ValueError('当前已有任务运行，请完成后再创建新任务')
            config = copy.deepcopy(task_config if task_config is not None else yaml.safe_load(self.config_path.read_text(encoding='utf-8')))
            if not isinstance(config, dict):
                raise ValueError('配置文件格式错误')
            from utils.run_observer import configure_task
            controls_v96 = configure_task(config, runtime_settings)
            capabilities = merge_capabilities(capability_values(config), capabilities)
            apply_capabilities(config, capabilities)
            critic_settings = merge_critic_settings(critic_settings_from_config(config), critic_settings)
            apply_critic_settings(config, critic_settings)
            inputs=[(original_query or query).strip()]+[m['text'] for m in (initial_messages or [])]
            integrity=config.get('integrity',{}) or {}
            # Independent web tasks never implicitly inherit a viewed task or
            # configuration's task-specific locks. Config defaults require an
            # explicit copy into the per-task field; CLI configuration is unchanged.
            configured=inherited_parameters if inherited_parameters is not None else []
            manual=hard_parameter_text
            params=resolve_parameters(inputs,manual,configured if manual is None else [])
            config['integrity'] = dict(integrity,hard_parameters=params)
            config['integrity'].pop('hard_parameter_text',None)
            ident = uuid.uuid4().hex
            ancestor = inherited_task or self.jobs.get(parent_id)
            if ancestor:
                topic = ancestor['research_topic'] if not research_title or not research_title.strip() else topic
            research_round = ancestor['research_round'] + 1 if ancestor else 1
            research_id = ancestor['research_id'] if ancestor else ident
            created_at = time.time()
            label = run_label(created_at, topic, research_round)
            output_root = self.root / 'outputs' / 'web'
            output_root.mkdir(parents=True,exist_ok=True)
            # Atomic directory reservation prevents collisions with another
            # task of the same name/second, including other server processes.
            for attempt in range(100):
                suffix = '' if attempt == 0 else '-'+ident[:8]+('' if attempt == 1 else '-'+str(attempt))
                run_root = output_root / (label+suffix)
                try:
                    run_root.mkdir()
                    break
                except FileExistsError:
                    continue
            else:
                raise ValueError('无法创建唯一研究目录，请检查输出目录')
            query_path = run_root / 'query.txt'
            query_path.write_text(query.strip(), encoding='utf-8')
            task_dir = run_root / 'query'
            if inherited_task is not None:
                try:
                    lineage=[];cursor=inherited_task;seen=set()
                    while cursor:
                        if cursor['id'] in seen: raise ValueError('历史任务来源出现循环')
                        seen.add(cursor['id']);lineage.append(cursor)
                        if (cursor['task_dir']/'inheritance.json').is_file() or not cursor.get('parent_id'):
                            break
                        cursor=self.jobs.get(cursor['parent_id'])
                        if cursor is None:
                            raise ValueError('较早的续研来源记录已不存在，无法承诺完整继承；请恢复旧任务目录')
                    for ancestor in reversed(lineage):
                        snapshot_research(ancestor['task_dir'], task_dir, ancestor['id'],
                                          [m['text'] for m in (initial_messages or [])])
                except Exception:
                    import shutil
                    shutil.rmtree(run_root)
                    raise
            config.setdefault('pipeline', {}).update(query_file=str(query_path),
                output_path=str(run_root), live_updates_enabled=True,authoritative_user_inputs=inputs)
            # Use original project root as cwd so existing resource paths work.
            cfg_path = run_root / 'web_config.yaml'
            cfg_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            try:
                cfg_path.chmod(0o600)
            except OSError:
                pass
            (run_root/'研究说明.md').write_text(
                '# '+run_root.name+'\n\n研究名称：'+topic+'\n\n研究轮次：第'+str(research_round)+
                '轮（每次新建／续研任务计一次，区别于内部节点搜索轮次）。\n\n日期和时间：北京时间 UTC+08:00。\n\n'
                '任务编号：'+ident+'\n\n上一轮任务编号：'+(parent_id or '无，独立研究')+
                '\n\n最终成果：query/final/\n\n研究总结：query/summary.md\n\n'
                '节点记录：query/node_*/\n\n继承快照：query/inherited/\n',encoding='utf-8')
            from utils.run_observer import save_settings
            save_settings(task_dir, controls_v96)
            set_control(task_dir, False)
            env = dict(os.environ, PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
                       PHY_TIMING_ROOT=str(task_dir))
            env.update(task_env or {})
            job = dict(id=ident, state='queued', query=(original_query or query).strip(),
                original_query=(original_query or query).strip(), parent_id=parent_id, task_dir=task_dir,
                capabilities=capabilities, critic_settings=critic_settings, hard_parameters=params,
                process=None, logs=deque(maxlen=250), messages=initial_messages or [], exit_code=None,
                created_at=created_at, started_at=None, finished_at=None, elapsed_seconds=None,
                log_path=run_root/'console.log', paused_requested=False, owner_id=owner_id,
                shared=False, archived=False, _launch=(cfg_path,env))
            job.update(research_topic=topic,research_round=research_round,research_id=research_id)
            job.update(naming_fields(job))
            with self.lock:
                if self.closed:
                    import shutil
                    shutil.rmtree(run_root)
                    raise ValueError('服务正在关闭；新任务尚未启动')
                busy = any(j['state'] not in TERMINAL and j['state'] != 'queued' for j in self.jobs.values())
                self.jobs[ident] = job
                self._save_job(job)
                if not busy:self._start_job(job)
            return ident

    def _start_job(self, job):
        cfg_path, env = job.pop('_launch')
        try:
            command=[sys.executable, '-u', str(self.root/'run.py'),'--cfg_file', str(cfg_path)]
            if job.pop('_resume_launch',False): command.append('--resume')
            proc = subprocess.Popen(command, cwd=self.root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', start_new_session=(os.name=='posix'))
        except Exception:
            job.update(state='failed',finished_at=time.time(),elapsed_seconds=0)
            job['logs'].append('工作进程启动失败，请检查服务器依赖与配置。')
            self._save_job(job)
            self._start_next()
            raise
        job.update(process=proc,worker_pid=proc.pid,state='starting',
            started_at=job.get('started_at') or time.time(),finished_at=None,
            _elapsed_before=job.get('elapsed_seconds') or 0,_started_monotonic=time.monotonic())
        job.update(force_stop={}, process_identity=capture_process(proc,job['task_dir'],self.root))
        self._save_job(job)
        job['_watch_done']=threading.Event()
        threading.Thread(target=self._watch,args=(job,),daemon=False).start()
        threading.Thread(target=self._pump,args=(job,),daemon=True).start()

    def _start_next(self):
        if self.closed or any(j['state'] not in TERMINAL and j['state']!='queued' for j in self.jobs.values()):return
        for job in self.jobs.values():
            if job['state']=='queued' and '_launch' in job:
                try:self._start_job(job)
                except Exception:pass
                break

    def cancel_queued(self, ident):
        with self.lock:
            job=self._get(ident)
            if job['state']!='queued':raise ValueError('只能取消尚未启动的排队任务；运行任务请使用暂停')
            job.pop('_launch',None)
            job.update(state='failed',cancelled=True,finished_at=time.time(),elapsed_seconds=0)
            job['logs'].append('排队任务已由成员取消；没有调用模型 API。')
            self._save_job(job)
            self._start_next()
            return {'cancelled':True}

    def _observe_detached(self, job):
        # A server can die while its worker is still alive. Never start a duplicate.
        while run_locked(job['task_dir']) or process_alive(job.get('worker_pid')):
            if self.closed:return
            time.sleep(0.5)
        if self.closed:return
        with self.lock:
            if job.get('force_stop',{}).get('requested_at'):return
            info=recovery_info(job['task_dir'])
            report=safe_read(job['task_dir']/'completion.json') or {}
            if info.get('stage')=='done':
                job['state']='finished' if report.get('status')=='passed' else 'partial'
            else:job['state']='interrupted'
            runtime=safe_read(job['task_dir']/'runtime.json') or {}
            job['finished_at']=runtime.get('sampled_at',time.time())
            job['elapsed_seconds']=max(job.get('elapsed_seconds') or 0,runtime.get('seconds') or 0)
            freeze_timing(job['task_dir'])
            self._save_job(job);self._start_next()

    def _recovery_env(self,job,config):
        return dict(os.environ,PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8',
                    PHY_TIMING_ROOT=str(job['task_dir']))

    def recover_task(self,ident):
        with self.lock:
            if self.closed:raise ValueError('服务器正在关闭')
            job=self._get(ident)
            if job['state'] not in ('interrupted','failed'):raise ValueError('只能恢复已中断的任务；运行中的任务不能重复启动')
            if run_locked(job['task_dir']) or process_alive(job.get('worker_pid')):
                raise ValueError('原研究进程仍在运行，不能重复恢复')
            checkpoint=read_checkpoint(job['task_dir'])
            if checkpoint['stage']=='done':raise ValueError('任务已经结束，请使用追加条件续研')
            if checkpoint.get('supervisor'):validate_evidence(job['task_dir'],checkpoint['supervisor'])
            cfg_path=job['task_dir'].parent/'web_config.yaml'
            config=yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
            # The saved config must still target exactly the original task.
            pipeline=config.get('pipeline') or {}
            target=Path(pipeline.get('output_path','outputs'))/Path(pipeline.get('query_file','query.txt')).stem
            if not target.is_absolute():target=self.root/target
            if target.resolve()!=job['task_dir'].resolve():raise ValueError('恢复配置的输出路径与原任务不一致')
            env=self._recovery_env(job,config)
            busy=any(j['state'] not in TERMINAL and j['state']!='queued' for j in self.jobs.values())
            if busy and not self.queue_enabled:raise ValueError('已有研究正在运行，请等其结束后再恢复')
            if self.queue_enabled and (sum(j['state']=='queued' for j in self.jobs.values())>=6 or
                sum(j['state']=='queued' and j.get('owner_id')==job.get('owner_id') for j in self.jobs.values())>=2):
                raise ValueError('等待队列已满，请稍后恢复')
            freeze_timing(job['task_dir'])
            runtime=safe_read(job['task_dir']/'runtime.json') or {}
            job['elapsed_seconds']=max(job.get('elapsed_seconds') or 0,runtime.get('seconds') or 0)
            set_control(job['task_dir'],False)
            old_active=safe_read(job['task_dir']/'live_updates'/'active.json') or {}
            session=(checkpoint.get('inbox') or {}).get('session') or old_active.get('session','')
            atomic_json(job['task_dir']/'live_updates'/'active.json',{'session':session,'status':'restoring'})
            for m in job['messages']:
                if m.get('status')=='not_applied' and not self._receipt(job,m):
                    m['status']='queued' if m.get('_path') and Path(m['_path']).exists() else 'waiting'
            atomic_json(job['task_dir']/'live_updates'/'progress.json',{'state':'starting',
                'round':(checkpoint.get('supervisor') or {}).get('values',{}).get('round_counter',0),
                'revision':(checkpoint.get('supervisor') or {}).get('values',{}).get('live_revision',0)})
            job.update(state='queued',cancelled=False,archived=False,exit_code=None,
                recovery_attempts=(job.get('recovery_attempts') or 0)+1,_launch=(cfg_path,env),_resume_launch=True)
            job['logs'].append('[Recovery] 从原任务断点恢复；历史文件与评审保留。')
            self._save_job(job)
            if not busy:self._start_job(job)
            return {'id':ident,'recovered':True,'queued':busy}

    def request_checkpoint_stop(self):
        with self.lock:
            for job in self.jobs.values():
                if job['state'] not in TERMINAL and job['state']!='queued':
                    atomic_json(job['task_dir']/'live_updates'/'control.json',
                                {'paused':False,'stop_requested':True})

    def continue_task(self, ident, text, capabilities=None, critic_settings=None, hard_parameter_text=None, research_title=None, runtime_settings=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ValueError('请输入本轮补充条件（最多100000字符）')
        with self.creation_lock:
            with self.lock:
                parent = self._get(ident)
                self._refresh(parent)
            if parent['state'] not in TERMINAL or run_locked(parent['task_dir']) or process_alive(parent.get('worker_pid')):
                raise ValueError('请等任务完成；运行中的任务请使用“发送条件”')
            capabilities = merge_capabilities(parent['capabilities'], capabilities)
            critic_settings = merge_critic_settings(parent['critic_settings'], critic_settings)
            if hard_parameter_text == parameter_text(parent['hard_parameters']):
                hard_parameter_text=None  # An unchanged field inherits; explicit new conditions can update it.
            # Include all intended conditions, even those submitted too late to apply.
            conditions = [m['text'] for m in parent['messages']] + [text.strip()]
            summary_path = parent['task_dir'] / 'summary.md'
            summary = ''
            if summary_path.is_file():
                with summary_path.open(encoding='utf-8') as stream:
                    summary = stream.read(12001)
                if len(summary) > 12000:
                    summary = summary[:12000] + '\n[摘要已截断；完整内容见旧成果目录的 summary.md]'
            parts = ['# 原始任务', parent['original_query'], '# 累计补充条件（按时间顺序，后续条件优先）']
            parts.extend(f'条件 {index}:\n{condition}' for index, condition in enumerate(conditions, 1))
            parts.extend(['# 上一轮研究摘要（待核验资料，不是已验证的事实）',
                summary or '[上一轮没有最终摘要，请检查已有成果和失败日志。]',
                '# 上一轮成果目录', str(parent['task_dir']),
                '# 本轮要求',
                '按照原始任务和累计条件开展增量研究；用户明确修改的条件以最新修改为准，其余条件保留。'
                '仅规划新增工作与受影响的核对，不重复无关或已有充分证据且未受影响的研究。'
                '历史摘要、评审和模板是参考资料，不能新增用户未要求的参数、文件或验证阈值。'
                '针对当前目标读取相关旧成果；需要修改旧文件时先复制到本轮输出目录，'
                '不要修改或覆盖旧成果。本轮使用新的任务进程与输出目录，未恢复旧进程或旧 MCTS 树。'])
            manifest=safe_read(parent['task_dir']/'final_manifest.json')
            if manifest:
                parts.extend(['# 上轮成果清单（仅供复核，条件改变后不能直接继承验收）',
                    json.dumps(dict(status=manifest.get('status'),
                        artifacts=manifest.get('artifacts',[])[:24]),ensure_ascii=False,indent=2),
                    '完整清单及全部研究文件由程序保存在本轮继承快照中，不受上述预览长度限制。',
                    '优先复用清单中的模型与数据来开展有针对性的追加研究；读取前验证 SHA-256。'
                    '没有受新条件影响的内容可引用其来源，受影响的参数、结果和验证必须重新核对。'])
            from utils.run_observer import continuation_preview, settings_for
            changes_v96 = continuation_preview(parent, text, hard_parameter_text)
            parts.extend(['# 程序条件变更预览（自然语言影响仍需复核）', json.dumps({k:changes_v96[k] for k in ('changes','impact_status','impact_notice')}, ensure_ascii=False)])
            if runtime_settings is None:runtime_settings = settings_for(parent['task_dir'])
            prompt = '\n\n'.join(parts)
            if len(prompt) > 100000:
                raise ValueError('原任务、累计条件和摘要合计超过100000字符，请精简后创建新任务')
            messages = [dict(id=uuid.uuid4().hex, text=condition, status='included')
                for condition in conditions]
            child = self.create(prompt, capabilities, critic_settings, hard_parameter_text,
                original_query=parent['original_query'],parent_id=ident, initial_messages=messages,
                inherited_parameters=parent['hard_parameters'], inherited_task=parent,research_title=research_title,runtime_settings=runtime_settings)
            return dict(id=child, parent_id=ident)

    def summary_document(self, ident, format='md'):
        if format not in ('md', 'txt', 'html'):
            raise ValueError('总结格式仅支持 md、txt、html')
        with self.lock:
            job = self._get(ident)
            if job['state'] not in TERMINAL:
                raise RuntimeError('总结尚未完成，请等待任务结束')
            root = job['task_dir'].resolve()
            path = (root / 'summary.md').resolve()
            if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
                raise FileNotFoundError('本任务尚无可下载的总结')
            content = path.read_text(encoding='utf-8')
        filename = naming_fields(job)['summary_filename_base']+'_研究总结.'+format
        if format == 'html':
            content = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>PhysMaster 研究总结</title><style>'
                'body{max-width:960px;margin:40px auto;padding:0 24px;font-family:system-ui,sans-serif;'
                'color:#243544}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.75 system-ui,sans-serif}'
                '@media print{body{margin:0;max-width:none}pre{font-size:11pt}}'
                '</style><h1>PhysMaster 研究总结</h1><p>研究：'+escape(job['display_name'])+'</p><p>任务编号：' + ident +
                '</p><pre>' + escape(content) + '</pre></html>')
        content_type = {'md': 'text/markdown', 'txt': 'text/plain', 'html': 'text/html'}[format]
        return content.encode('utf-8'), content_type + '; charset=utf-8', filename

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
            with job['log_path'].open('a' if job.get('recovery_attempts') else 'w', encoding='utf-8') as log:
                for line in job['process'].stdout:
                    log.write(line)
                    log.flush()
                    with self.lock:
                        job['logs'].append(line.rstrip()[:4000])
            code = job['process'].wait()
            with self.lock:
                job['exit_code'] = code
                completion=safe_read(job['task_dir']/'completion.json') or {}
                if job.get('force_stop',{}).get('requested_at'):pass  # Force-stop worker confirms final state.
                elif code==0:job['state']='finished' if completion.get('status')=='passed' else 'partial'
                else:
                    info=recovery_info(job['task_dir'])
                    job['state']='reconnecting' if info['running'] else 'interrupted' if info['available'] else 'failed'
                    if info['running']:threading.Thread(target=self._observe_detached,args=(job,),daemon=True).start()
                if not job.get('force_stop',{}).get('requested_at'):self._finish_timing(job)
                self._save_job(job)
        except Exception as exc:
            with self.lock:
                if not job.get('force_stop',{}).get('requested_at'):job['state'] = 'failed'
                self._finish_timing(job)
                job['logs'].append(str(exc))
                self._save_job(job)
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
                            if job['state']!='interrupted':m['status'] = 'not_applied'
                self._save_job(job)
                self._start_next()
            job['_watch_done'].set()

    def _get(self, ident):
        if ident not in self.jobs:
            raise KeyError('任务不存在；只能访问 outputs/web 中的历史任务')
        return self.jobs[ident]

    def _finish_timing(self, job):
        if job.get('finished_at') is None:
            job['finished_at'] = time.time()
            job['elapsed_seconds'] = job.get('_elapsed_before',0)+max(0.0, time.monotonic() - job.get('_started_monotonic', time.monotonic()))

    def _timing_snapshot(self, job):
        terminal = job['state'] in TERMINAL
        result = read_timing(job['task_dir'], terminal=terminal, finished_at=job.get('finished_at'))
        elapsed = job.get('elapsed_seconds')
        if not terminal and '_started_monotonic' in job:
            elapsed = job.get('_elapsed_before',0)+max(0.0, time.monotonic() - job.get('_started_monotonic', time.monotonic()))
        paused = result['phases'].get('paused', 0.0)
        result.update(elapsed_seconds=elapsed, paused_seconds=paused if result['available'] else None,
                      active_seconds=max(0.0, elapsed-paused) if elapsed is not None and result['available'] else None,
                      started_at=job.get('started_at'), finished_at=job.get('finished_at'))
        if elapsed is not None:
            # Import/startup and process exit are outside the pipeline's timer.
            result['phases']['process_overhead'] = max(0.0, elapsed-result['pipeline_seconds'])
        return result

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
        messages_before = copy.deepcopy(job['messages'])
        if job['state'] not in TERMINAL and job['state'] not in ('queued','reconnecting'):
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
        old_parameters=job['hard_parameters']
        inputs=[job['original_query']]+[m['text'] for m in job['messages'] if m['status'] in ('included','applied')]
        try:job['hard_parameters']=resolve_parameters(inputs,configured=job['hard_parameters'])
        except ValueError:pass  # The worker checks invalid amendments before acknowledging.
        if job['messages'] != messages_before or job['hard_parameters'] != old_parameters:
            self._save_job(job)
        return data

    def history_snapshot(self, ident):
        with self.lock:
            job=self._get(ident);self._refresh(job)
            completion=safe_read(job['task_dir']/'completion.json')
            return dict(id=ident,state=job['state'],query=job['query'][:300],parent_id=job['parent_id'],
                created_at=job['created_at'],**naming_fields(job),owner_id=job.get('owner_id'),
                shared=job.get('shared',False),archived=job.get('archived',False),
                completion_report={'status':completion.get('status')} if completion else None)

    def snapshot(self, ident, lightweight=False):
        # Capture mutable metadata briefly; document reads and timing aggregation
        # never hold the lock needed by unrelated task and round requests.
        with self.lock:
            original = self._get(ident)
            progress = self._refresh(original)
            job = dict(original, messages=copy.deepcopy(original['messages']), logs=list(original['logs']))
            queue_position = next((i+1 for i,j in enumerate(j for j in self.jobs.values() if j['state']=='queued') if j['id']==ident),None)
        folder=job['task_dir'];summary=folder/'summary.md'
        data=dict(id=ident,state=job['state'],query=job['query'],parent_id=job['parent_id'],
            owner_id=job.get('owner_id'),shared=job.get('shared',False),archived=job.get('archived',False),cancelled=job.get('cancelled',False),
            force_stop=job.get('force_stop') or {},
            queue_position=queue_position,**naming_fields(job),
            capabilities=job['capabilities'],critic_settings=job['critic_settings'],
            hard_parameters=job['hard_parameters'],hard_parameter_text=parameter_text(job['hard_parameters']),
            summary_available=job['state'] in TERMINAL and summary.is_file() and summary.stat().st_size>0,
            round=progress.get('round',0),revision=progress.get('revision',0),
            paused_requested=job['paused_requested'],exit_code=job['exit_code'],
            v96_control=(safe_read(folder/'live_updates/control.json') or {}),
            messages=[{k:v for k,v in m.items() if not k.startswith('_')} for m in job['messages']],task_dir=str(folder))
        stamps=[]
        for name in ('completion.json','final_manifest.json','finalization.json','summary.md','summary_generation.json'):
            try:
                stat=(folder/name).stat();stamps.append((name,stat.st_mtime_ns,stat.st_size))
            except OSError:stamps.append((name,None,None))
        data['reports_version']=hashlib.sha256(repr(stamps).encode()).hexdigest()[:20]
        if not lightweight:
            data.update(recovery=recovery_info(folder),recovery_attempts=job.get('recovery_attempts',0),
                completion_report=safe_read(folder/'completion.json'),final_manifest=safe_read(folder/'final_manifest.json'),
                finalization=safe_read(folder/'finalization.json'),summary_generation=safe_read(folder/'summary_generation.json'),
                timing=self._timing_snapshot(job),logs=job['logs'],summary=summary.read_text(encoding='utf-8') if summary.exists() else '')
        return data

    def update(self, ident, text):
        if not isinstance(text, str) or not text.strip() or len(text)>100000:
            raise ValueError('请输入补充条件（最多100000字符）')
        with self.lock:
            job = self._get(ident)
            self._refresh(job)
            if job['state'] in TERMINAL or job['state'] in ('summarizing','queued','reconnecting'):
                raise ValueError('任务已结束研究阶段；完成后可选择“补充条件并重新研究”')
            m = dict(id=uuid.uuid4().hex, text=text.strip(), status='waiting')
            job['messages'].append(m)
            self._refresh(job)
            self._save_job(job)
            return {k:v for k,v in m.items() if not k.startswith('_')}

    def control(self, ident, action):
        if action not in ('pause', 'resume'):
            raise ValueError('无效操作')
        with self.lock:
            job = self._get(ident)
            self._refresh(job)
            if job['state'] in TERMINAL or job['state'] in ('summarizing','queued','reconnecting'):
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
                if job['state']=='queued':self.cancel_queued(job['id'])
                elif job['state'] not in TERMINAL:
                    self._refresh(job)
                    control=safe_read(job['task_dir']/'live_updates'/'control.json') or {}
                    if not control.get('stop_requested'):set_control(job['task_dir'], False)


def make_handler(manager):
    feed = getattr(manager, "round_feed", None) or RoundFeed()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, content_type='application/json; charset=utf-8', headers=None):
            data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith('application/json') else value
            compressed=False
            accepted=getattr(self,'headers',{}).get('Accept-Encoding','')
            if content_type.startswith('application/json') and len(data)>1024 and any(
                    part.strip().split(';')[0]=='gzip' and not re.search(r'q\s*=\s*0(?:\.0*)?(?:;|$)',part)
                    for part in accepted.split(',')):
                data=gzip.compress(data,compresslevel=1);compressed=True
            try:
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Vary','Accept-Encoding')
                if compressed:self.send_header('Content-Encoding','gzip')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                self.close_connection=True  # A closed browser poll does not fail the research.

        def trusted_host(self):
            # Local mode must not become an anonymous public workspace through a
            # loopback tunnel. Team mode supplies its own authenticated host rule.
            forwarded=('CF-Ray','CF-Connecting-IP','Forwarded','X-Forwarded-For','X-Forwarded-Host','X-Forwarded-Proto')
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}') and not any(self.headers.get(h) for h in forwarded)

        def request_token(self):
            return manager.token

        def trusted_origin(self):
            origin=self.headers.get('Origin')
            return not origin or origin in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.trusted_host():
                return self.reply({'error':'访问地址未获允许；公开域名访问须以 --team 启动并登录'}, 403)
            path = urlparse(self.path).path
            if path == '/':
                html = (Path(__file__).parent/'web'/'index.html').read_text(encoding='utf-8')
                return self.reply(html.replace('__TOKEN__', self.request_token()).replace('/*__TEAM_BOOTSTRAP__*/', '').replace('/*__TEAM_IDENTITY__*/', '').encode(), content_type='text/html; charset=utf-8')
            if path == '/api/capabilities':
                try:
                    return self.reply(manager.capability_settings())
                except (OSError, ValueError, yaml.YAMLError):
                    return self.reply({'error':'无法读取能力配置，请检查本地 config.yaml'}, 400)
            if path == '/api/tasks':
                with manager.lock:
                    ids = list(manager.jobs)
                return self.reply({'tasks':[manager.history_snapshot(i) for i in ids]})
            summary_match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/summary', path)
            if summary_match:
                try:
                    format = parse_qs(urlparse(self.path).query).get('format', ['md'])[0]
                    content, content_type, filename = manager.summary_document(summary_match[1], format)
                    return self.reply(content, content_type=content_type,
                        headers={'Content-Disposition':f'attachment; filename="PhysMaster-summary-{summary_match[1][:8]}.{format}"; filename*=UTF-8\'\''+quote(filename)})
                except (KeyError, FileNotFoundError):
                    return self.reply({'error':'任务不存在或尚无可下载的总结'}, 404)
                except RuntimeError as exc:
                    return self.reply({'error':str(exc)}, 409)
                except ValueError as exc:
                    return self.reply({'error':str(exc)}, 400)
                except OSError:
                    return self.reply({'error':'读取总结失败，请检查文件权限'}, 500)
            monitor_match=re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(rounds|artifact|manifest|timeline|delivery-index|inheritance|inherited-file|consistency-review)',path)
            if monitor_match:
                try:
                    with manager.lock:
                        job=manager._get(monitor_match[1])
                        task_dir=job['task_dir'].resolve()
                    if monitor_match[2]=='inheritance':
                        args=parse_qs(urlparse(self.path).query)
                        return self.reply(inheritance_view(task_dir,
                            offset=int(args.get('offset',['0'])[0]),limit=int(args.get('limit',['50'])[0]),
                            query=args.get('query',[''])[0]))
                    if monitor_match[2]=='inherited-file':
                        file_id=parse_qs(urlparse(self.path).query).get('file_id',[''])[0]
                        record, source=get_record(task_dir,file_id)
                        return self.reply(source.read_bytes(),content_type='application/octet-stream',
                            headers={'Content-Disposition':"attachment; filename*=UTF-8''"+
                                __import__('urllib.parse',fromlist=['quote']).quote(Path(record['source_path']).name)})
                    if monitor_match[2]=='rounds':
                        args=parse_qs(urlparse(self.path).query)
                        view=args.get('view',[self.headers.get('X-PhysMaster-View','full')])[0]
                        return self.reply(feed.read(task_dir,view=view,since=args.get('since',[self.headers.get('X-PhysMaster-Since','')])[0],
                            round_index=int(args['round'][0]) if 'round' in args else None,
                            node_id=int(args['node'][0]) if 'node' in args else None))
                    if monitor_match[2]=='consistency-review':
                        review_path=task_dir/'final_consistency.json'
                        if review_path.is_symlink():return self.reply({'error':'核验记录路径无效'},403)
                        review=safe_read(review_path)
                        if not review:return self.reply({'error':'本任务尚无 v9.5 一致性核验记录'},404)
                        return self.reply(review,headers={'Content-Disposition':'attachment; filename="PhysMaster-consistency-review.json"'})
                    if monitor_match[2]=='manifest':
                        manifest=safe_read(task_dir/'final_manifest.json')
                        if not manifest:return self.reply({'error':'本任务尚无最终成果清单'},404)
                        return self.reply(manifest,headers={'Content-Disposition':'attachment; filename="PhysMaster-final-manifest.json"'})
                    if monitor_match[2]=='delivery-index':
                        index=task_dir/'delivery_index.md'
                        if not index.is_file():return self.reply({'error':'成果索引尚未生成'},404)
                        return self.reply(index.read_bytes(),content_type='text/markdown; charset=utf-8',
                            headers={'Content-Disposition':'attachment; filename="PhysMaster-delivery-index.md"'})
                    if monitor_match[2]=='timeline':
                        events=[]
                        for round_record in list_rounds(task_dir):
                            for node in round_record.get('nodes',[]):
                                events.extend(dict(event,round=round_record['round'],revision=round_record.get('revision',0),node_id=node['node_id']) for event in node.get('timeline',[]))
                        return self.reply({'events':sorted(events,key=lambda e:e['at'])})
                    relative=parse_qs(urlparse(self.path).query).get('path',[''])[0]
                    artifact=(task_dir/relative).resolve()
                    # Node artifacts plus explicitly audited final deliverables.
                    parts=Path(relative).parts
                    report=safe_read(task_dir/'completion.json') or {}
                    rows=report.get('artifacts',[])+report.get('additional_artifacts',[])
                    final_names={a.get('path') for a in rows if a.get('status')=='available'}
                    published={a.get('download_path'):a for a in rows if a.get('status')=='available' and a.get('download_path')}
                    allowed=bool(parts) and (bool(re.fullmatch(r'node_\d+',parts[0])) or relative in published or
                        (len(parts)==1 and relative in final_names and relative not in ('contract.json','runtime.json','completion.json')))
                    raw_path=task_dir/relative
                    if (not allowed or Path(relative).is_absolute() or '..' in parts or '\\' in relative
                            or any(p.is_symlink() for p in [raw_path,*raw_path.parents] if p.is_relative_to(task_dir))
                            or not artifact.is_relative_to(task_dir) or not artifact.is_file()):
                        return self.reply({'error':'文件不存在或不允许访问'},404)
                    content=artifact.read_bytes()
                    if len(parts)==1 or relative in published:
                        record=published[relative] if relative in published else next(a for a in rows if a.get('path')==relative and a.get('status')=='available')
                        if not record.get('sha256') or hashlib.sha256(content).hexdigest()!=record['sha256']:
                            return self.reply({'error':'文件已在核对后发生变化，请重新研究并核对交付'},409)
                    elif re.fullmatch(r'node_\d+',parts[0]):
                        manifest=safe_read(task_dir/parts[0]/'artifact_manifest.json') or {}
                        node_record=next((a for a in manifest.get('artifacts',[]) if a.get('path')==relative),None)
                        if node_record and node_record.get('status') in ('verified','accepted','published') and hashlib.sha256(content).hexdigest()!=node_record.get('sha256'):
                            return self.reply({'error':'节点文件在核对后发生变化；不能下载为已核对的版本'},409)
                    return self.reply(content,content_type='application/octet-stream')
                except ValueError as exc:
                    return self.reply({'error':str(exc)},409)
                except (KeyError,OSError) as exc:
                    return self.reply({'error':'任务或文件不存在'},404)
            match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})', path)
            if match:
                try:
                    return self.reply(manager.snapshot(match[1],lightweight=True) if self.headers.get('X-PhysMaster-View')=='progress' or parse_qs(urlparse(self.path).query).get('view')==['progress'] else manager.snapshot(match[1]))
                except KeyError as e:
                    return self.reply({'error':str(e)},404)
            return self.reply({'error':'Not found'},404)

        def do_POST(self):
            if not self.trusted_host() or self.headers.get('X-PhysMaster-Token') != self.request_token():
                return self.reply({'error':'请求校验失败，请刷新本地页面'},403)
            origin = self.headers.get('Origin')
            if not self.trusted_origin():
                return self.reply({'error':'Invalid origin'},403)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length <= 0 or length > 500000:
                    raise ValueError('请求过大或为空')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict): raise ValueError('请求格式错误')
                path = urlparse(self.path).path
                if path == '/api/tasks':
                    return self.reply({'id':manager.create(body.get('query'), body.get('capabilities'), body.get('critic_settings'),body.get('hard_parameter_text'),research_title=body.get('research_title'),runtime_settings=body.get('runtime_settings'))},201)
                match = re.fullmatch(r'/api/tasks/([a-f0-9]{32})/(updates|control|continue|recover)',path)
                if not match: return self.reply({'error':'Not found'},404)
                if match[2]=='recover':return self.reply(manager.recover_task(match[1]),202)
                if match[2] == 'continue':
                    return self.reply(manager.continue_task(match[1], body.get('text'), body.get('capabilities'), body.get('critic_settings'),body.get('hard_parameter_text'),research_title=body.get('research_title'),runtime_settings=body.get('runtime_settings')), 201)
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
    parser.add_argument('--team',action='store_true',help='启用小组账号与个人 API 配置')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--public-url',help='浏览器访问地址；小组模式默认 https://physmaster.tech')
    parser.add_argument('--api-origin',action='append',default=[],help='管理员允许的额外 HTTPS 模型 API 根地址')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    cfg=Path(args.cfg_file)
    if not cfg.is_absolute(): cfg=root/cfg
    if args.team:
        from utils.team_workspace import TeamStore, TeamTaskManager, make_team_handler
        store=TeamStore(root/'team_data',allowed_origins=args.api_origin)
        if not store.users:parser.error('请先运行 python team_admin.py add-user 用户名 --admin 创建管理员')
        public_url=args.public_url or DEFAULT_PUBLIC_URL
        manager=TeamTaskManager(root,cfg,store)
        handler=make_team_handler(manager,store,public_url)
    else:
        if args.public_url:parser.error('--public-url 必须同时启用 --team；本地匿名模式不支持公开域名')
        if args.host!='127.0.0.1':parser.error('开放网络访问必须启用 --team；本地模式只监听 127.0.0.1')
        manager=TaskManager(root,cfg);handler=make_handler(manager)
    # PHY_CLOUD_TRANSLATION_1 BEGIN
    from utils.ui_translation import make_translation_handler
    handler = make_translation_handler(handler, manager, store if args.team else None)
    # PHY_CLOUD_TRANSLATION_1 END
    # PHY_CLOUD_V96_1 BEGIN
    from utils.research_web import make_evidence_handler
    handler = make_evidence_handler(handler, manager, store if args.team else None)
    from utils.run_observer import make_run_handler
    handler = make_run_handler(handler, manager, store if args.team else None)
    # PHY_CLOUD_V96_1 END
    # PHY_SOURCE_POLICY_V962 BEGIN
    from utils.web_source_policy import make_source_policy_handler
    handler = make_source_policy_handler(handler, manager, store if args.team else None)
    # PHY_SOURCE_POLICY_V962 END
    # PHY_FORCE_STOP_V963 HANDLER BEGIN
    from utils.force_stop import make_force_stop_handler
    handler = make_force_stop_handler(handler, manager, store if args.team else None)
    # PHY_FORCE_STOP_V963 HANDLER END
    server=ThreadingHTTPServer((args.host,args.port),handler)
    print(f'PhysMaster Chat: {public_url if args.team else f'http://127.0.0.1:{server.server_port}'}',flush=True)
    print(f'后端监听：{args.host}:{server.server_port}',flush=True)
    print('访问模式：小组登录保护' if args.team else '访问模式：仅本机；公开域名与代理转发已阻止。网站部署请加 --team。',flush=True)
    print('关闭网页不会停止研究。Ctrl+C 请求在安全边界保存并停止，重启后可恢复。',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        manager.request_checkpoint_stop()
        print('\n服务器停止接收请求；正在等待当前轮到达安全断点。重启后可恢复中断任务。',flush=True)
    finally:
        manager.close()
        server.server_close()

if __name__=='__main__':
    main()
