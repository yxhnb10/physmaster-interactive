"""Versioned safe-boundary checkpoints and OS-owned run locks (no pickle)."""
import copy
import hashlib
import json
import math
import os
import time
from pathlib import Path
from utils.live_updates import atomic_json

CHECKPOINT = 'research_checkpoint.json'
SCHEMA = 1

class ResearchInterrupted(RuntimeError):
    pass

class RunLock:
    """The kernel releases this lock after Ctrl+C, SIGKILL, or power loss."""
    def __init__(self, task_dir, wait_timeout=0, busy_message=None):
        self.path = Path(task_dir) / 'research.lock'
        self.stream = None
        self.wait_timeout = max(0, float(wait_timeout))
        self.busy_message = busy_message or '该研究仍由另一个进程运行，不能重复恢复'
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.path.stat().st_size == 0:
            self.stream.write(b'0'); self.stream.flush()
        self.stream.seek(0)
        deadline = time.monotonic() + self.wait_timeout
        while True:
            try:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close(); self.stream = None
                    raise ValueError(self.busy_message) from None
                time.sleep(.01)
        return self
    def __exit__(self, *args):
        if self.stream:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close(); self.stream = None

def _workers_locked(task_dir):
    for path in (Path(task_dir)/'worker_leases').glob('*/research.lock'):
        try:
            with RunLock(path.parent): pass
        except ValueError: return True
    return False

def run_locked(task_dir):
    # A live owner already proves activity. Do not momentarily acquire a worker
    # lease during dashboard polls: that can collide with a starting worker.
    if (Path(task_dir)/'research.lock').exists():
        try:
            with RunLock(task_dir): pass
        except ValueError:
            return True
    return _workers_locked(task_dir)


def new_execution_generation(task_dir):
    import uuid
    with RunLock(Path(task_dir)/'start_gate', wait_timeout=30,
                 busy_message='启动协调锁等待超时；请检查旧工作进程'):
        if _workers_locked(task_dir):raise ValueError('旧计算进程仍在运行，请等待其退出后恢复')
        generation=uuid.uuid4().hex
        atomic_json(Path(task_dir)/'worker_generation.json',{'generation':generation})
        return generation

def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def _json_value(value):
    if isinstance(value, Path): return str(value)
    if isinstance(value, set): return sorted(value)
    if isinstance(value, (list, tuple)): return [_json_value(v) for v in value]
    if isinstance(value, dict): return {str(k): _json_value(v) for k,v in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Checkpoint contains a nonfinite statistic')
    return value

def read_checkpoint(task_dir):
    path = Path(task_dir)/CHECKPOINT
    try:
        envelope = json.loads(path.read_text(encoding='utf-8'))
        data = envelope['checkpoint']
        if data.get('schema') != SCHEMA or _digest(data) != envelope['sha256']:
            raise ValueError('版本或校验和不匹配')
        if data.get('stage') not in ('clarifying','search','search_finished','finalization','summary','consistency','done'):
            raise ValueError('阶段无效')
        if not isinstance(data.get('contract'), (dict,type(None))): raise ValueError('合同无效')
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('断点文件缺失或损坏，不能自动恢复：'+str(exc)) from None

def save_checkpoint(task_dir, data):
    data = _json_value(dict(data, schema=SCHEMA, saved_at=time.time()))
    atomic_json(Path(task_dir)/CHECKPOINT, {'checkpoint':data, 'sha256':_digest(data)})

def recovery_info(task_dir):
    if run_locked(task_dir):
        return dict(available=False, reason='原研究进程仍在运行，禁止重复启动', running=True)
    if not (Path(task_dir)/CHECKPOINT).is_file():
        return dict(available=False,reason='该任务未保存断点；可基于已有成果追加条件，开启新一轮续研',running=False)
    try:
        data=read_checkpoint(task_dir)
        if data['stage']=='done': return dict(available=False,stage='done',reason='任务已正常结束',running=False)
        return dict(available=True,stage=data['stage'],saved_at=data['saved_at'],
                    round=(data.get('supervisor') or {}).get('values',{}).get('round_counter',0),
                    reason='将恢复已保存节点；尚未完成的调用需要重做',running=False)
    except ValueError as exc:
        return dict(available=False,reason=str(exc),running=False)

FIELDS = ('node_id_counter','round_counter','max_rounds','revision_round_budget',
          'revision_start_round','live_revision','live_update_history','prior_knowledge',
          '_seen_queries','_seen_urls','_tool_call_counts')

def supervisor_state(s):
    nodes=[]; evidence=[]
    for n in s.tree.get_all_nodes():
        fields={k:v for k,v in vars(n).items() if k not in ('parent','children')}
        nodes.append(dict(fields=fields,parent=n.parent.node_id if n.parent else None))
        if n.node_type!='virtual' and n.is_subtask_complete():
            manifest=Path(s.task_dir)/f'node_{n.node_id}'/'artifact_manifest.json'
            if manifest.is_file():
                for a in json.loads(manifest.read_text(encoding='utf-8')).get('artifacts',[]):
                    if a.get('sha256') and a.get('path'):
                        evidence.append(dict(path=a['path'],sha256=a['sha256']))
    return _json_value(dict(values={k:getattr(s,k) for k in FIELDS},nodes=nodes,
        root=s.tree.root.node_id, subtask_roots={k:v.node_id for k,v in s.tree.subtask_roots.items()},
        prior=s.tree.prior_knowlege,evidence=evidence))

def validate_evidence(task_dir, state):
    root=Path(task_dir).resolve()
    for item in state.get('evidence',[]):
        raw=root/item['path'];path=raw.resolve()
        linked=any(p.is_symlink() for p in [raw,*raw.parents] if p.is_relative_to(root))
        if not path.is_relative_to(root) or linked or not path.is_file():
            raise ValueError('已验收文件缺失或位置无效：'+item['path'])
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=item['sha256']: raise ValueError('已验收文件发生修改：'+item['path'])

def restore_supervisor(s, state):
    from core.mcts import MCTSNode, MCTSTree
    validate_evidence(s.task_dir,state)
    tree=MCTSTree(0,'Virtual Root',state.get('prior'))
    tree.nodes={};tree.subtask_roots={}
    for record in state['nodes']:
        fields=copy.deepcopy(record['fields'])
        ident=fields['node_id']
        if not isinstance(ident,int) or ident<0 or ident in tree.nodes: raise ValueError('断点节点编号无效')
        n=MCTSNode(fields['subtask_id'],ident,fields['node_type'],fields['subtask_description'])
        n.__dict__.update(fields)
        n.parent=None; n.children=[]
        # Never promote an interrupted attempt to an accepted scientific result.
        if n.node_type!='virtual' and n.status=='open':
            n.recovery_interrupted=True
            n.evaluation=dict(decision='to_revise',score_valid=False,reward=0,
                blocking_issues=['上次运行中断，本节点未完成评审'],policy_reason='恢复未完成的尝试')
            n.knowledge='[UNACCEPTED ATTEMPT] 上次运行中断；保留文件供检查，不作为已验证结论。'
            n.status='completed'
        tree.nodes[ident]=n
    tree.root=tree.nodes[state['root']]
    for record in state['nodes']:
        node=tree.nodes[record['fields']['node_id']]
        parent_id=record['parent']
        if parent_id is not None:
            if parent_id not in tree.nodes or parent_id==node.node_id: raise ValueError('断点树链接无效')
            tree.nodes[parent_id].add_child(node)
    for node in tree.nodes.values():
        seen=set(); parent=node
        while parent is not None:
            if parent.node_id in seen: raise ValueError('断点树包含环')
            seen.add(parent.node_id);parent=parent.parent
        if tree.root.node_id not in seen: raise ValueError('断点树包含孤立节点')
    tree.subtask_roots={int(k):tree.nodes[v] for k,v in state['subtask_roots'].items()}
    s.tree=tree
    for k in FIELDS:
        value=state['values'][k]
        setattr(s,k,set(value) if k in ('_seen_queries','_seen_urls') else value)
    s.subtasks=s._build_subtasks()
    # Reserved/partial node directories must never be overwritten.
    disk_ids=[int(p.name[5:]) for p in Path(s.task_dir).glob('node_*') if p.name[5:].isdigit()]
    s.node_id_counter=max([s.node_id_counter,max(tree.nodes)+1]+[i+1 for i in disk_ids])
    from utils.research_monitor import safe_read
    rounds=[]
    for p in (Path(s.task_dir)/'research_monitor').glob('round_*.json'):
        r=safe_read(p)
        if isinstance(r,dict) and isinstance(r.get('round_index'),int):
            rounds.append(r['round_index'])
            if r.get('state')=='running':
                r.update(state='interrupted',interrupted_at=time.time())
                atomic_json(p,r)
    # A partially recorded round keeps its own history index. Retry consumes
    # exactly one replacement round; completed rounds never reset their budget.
    next_round=max(rounds,default=-1)+1
    if next_round>s.round_counter:
        s.max_rounds+=next_round-s.round_counter
        s.round_counter=next_round

def freeze_timing(task_dir):
    """Keep the last observed sample; exclude offline time after an abrupt kill."""
    from utils.research_monitor import safe_read
    root=Path(task_dir)
    runtime=safe_read(root/'runtime.json') or {}
    if runtime.get('state')=='running':
        runtime.update(state='interrupted',current=None)
        atomic_json(root/'runtime.json',runtime)
    for p in (root/'timing_operations').glob('*.json'):
        data=safe_read(p)
        if isinstance(data,dict) and data.get('active'):
            data.update(active=[],interrupted=True)
            atomic_json(p,data)


def process_alive(pid):
    if not isinstance(pid,int) or pid<=0: return False
    try:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return False
            try:
                code=wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
            finally:kernel.CloseHandle(handle)
        os.kill(pid,0)
        stat=Path(f'/proc/{pid}/stat')
        if stat.exists() and stat.read_text().rsplit(')',1)[1].split()[0]=='Z': return False
        return True
    except (OSError,ValueError):return False
