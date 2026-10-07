"""Cached round cards and explicitly requested full nodes. Never changes evidence."""
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from utils.research_monitor import safe_read


def stamp(path):
    try:
        if path.is_symlink(): return None
        s = path.stat()
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


def confined_read(root, path):
    root = Path(root).resolve()
    path = Path(path).absolute()
    try:
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():return None
        path.resolve().relative_to(root)
    except (ValueError, OSError):return None
    return safe_read(path)


def apply_manifest(node, manifest):
    if not isinstance(manifest, dict):return
    node['artifacts'] = manifest.get('artifacts', node.get('artifacts', []))
    dates = [a.get('published_at') for a in node['artifacts']
             if a.get('status') == 'published' and isinstance(a.get('published_at'), (int, float))]
    if dates:
        event = dict(stage='published', at=max(dates))
        history = node.get('timeline') or []
        if event not in history:node['timeline'] = history + [event]


def preview(value, limit=1000, depth=0):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + '\n[卡片摘录；展开加载完整记录]'
    if isinstance(value, list):
        return [preview(v, limit, depth+1) for v in value[:30]]
    if isinstance(value, dict):
        if depth > 5: return '[嵌套证据请加载完整记录]'
        omitted = {'review_attempts','review_history','observed_file_evidence',
                   'accepted_prerequisites','parameter_evidence','code','arguments',
                   'evidence_coverage','retrieval_evidence','extra_reads'}
        return {k: preview(v, limit, depth+1) for k,v in value.items()
                if k not in omitted and not k.startswith('_')}
    return value


def node_card(node, version):
    row = preview(node, 900)
    result = node.get('result')
    if isinstance(result, str):
        try: result = json.loads(result)
        except ValueError: pass
    row['result'] = preview(result, 1600)
    row['tools'] = [dict(tool=t.get('tool',''), execution=preview(t.get('execution'), 500))
                    for t in (node.get('tools') or [])]
    # Counts and acceptance fields keep their original semantics. Full artifacts
    # remain available in the detail route; the cards show all file links.
    row['artifacts'] = node.get('artifacts', [])
    row['timeline'] = node.get('timeline', [])
    row['_detail_available'] = True
    row['_version'] = version
    return row


class RoundFeed:
    def __init__(self):
        self.lock = threading.RLock()
        self.tasks = OrderedDict()

    def _scan(self, task_dir, fresh=False):
        root = Path(task_dir).resolve(); key = str(root)
        state = self.tasks.setdefault(key, dict(scanned=0, rounds={}, signatures={}, cursors=OrderedDict(), errors=[]))
        self.tasks.move_to_end(key)
        while len(self.tasks)>8: self.tasks.popitem(last=False)
        if not fresh and time.monotonic()-state['scanned'] < 1:
            return state
        manifest_stamps = {p.parent.name: stamp(p) for p in root.glob('node_*/artifact_manifest.json')}
        seen = set(); errors=[]
        for path in sorted((root/'research_monitor').glob('round_*.json')):
            if not path.stem[6:].isdigit(): continue
            index=int(path.stem[6:]); seen.add(index)
            old=state['rounds'].get(index)
            signature=(stamp(path), tuple((n.get('node_id'), manifest_stamps.get('node_'+str(n.get('node_id'))))
                for n in (old or {}).get('nodes', [])))
            if signature==state['signatures'].get(index): continue
            data=confined_read(root,path)
            if not isinstance(data,dict) or not isinstance(data.get('nodes',[]),list):
                errors.append('第 '+str(index+1)+' 轮文件暂时无法读取；已有内容保留');continue
            data=copy.deepcopy(data)
            for n in data.get('nodes',[]):
                if not isinstance(n,dict): continue
                # Do not follow a model-supplied arbitrary node path.
                nid=n.get('node_id')
                if not isinstance(nid,int) or isinstance(nid,bool) or nid<0: continue
                apply_manifest(n,confined_read(root,root/f'node_{nid}'/'artifact_manifest.json'))
            data.setdefault('round_index',index);data.setdefault('round',index+1)
            signature=(stamp(path),tuple((n.get('node_id'),manifest_stamps.get('node_'+str(n.get('node_id')))) for n in data.get('nodes',[])))
            version=hashlib.sha256(repr(signature).encode()).hexdigest()[:20]
            state['rounds'][index]=dict({k:preview(v,2500) for k,v in data.items() if k not in ('nodes','dispatch')},
                                       nodes=[node_card(n,version) for n in data.get('nodes',[])])
            state['signatures'][index]=signature
        # Missing/transient files do not erase previously visible history.
        if set(state['rounds'])-seen:errors.append('部分历史轮次文件暂不可见；保留已缓存记录')
        state['errors']=errors;state['scanned']=time.monotonic()
        return state

    def read(self, task_dir, view='cards', since='', round_index=None, node_id=None):
        if view not in ('cards','full','node'):raise ValueError('不支持的轮次读取方式')
        if view=='full':  # Legacy clients retain full records, with the same path rules.
            root=Path(task_dir).resolve();rounds=[]
            for path in sorted((root/'research_monitor').glob('round_*.json')):
                if not path.stem[6:].isdigit():continue
                record=confined_read(root,path)
                if not isinstance(record,dict):continue
                for node in record.get('nodes',[]):
                    if not isinstance(node,dict):continue
                    nid=node.get('node_id')
                    if isinstance(nid,int) and not isinstance(nid,bool) and nid>=0:
                        apply_manifest(node,confined_read(root,root/f'node_{nid}'/'artifact_manifest.json'))
                rounds.append(record)
            return dict(rounds=rounds)
        with self.lock:
            state=self._scan(task_dir, fresh=view=='node')
            versions={i:hashlib.sha256(repr(s).encode()).hexdigest()[:20] for i,s in state['signatures'].items()}
            cursor=hashlib.sha256(json.dumps(versions,sort_keys=True).encode()).hexdigest()[:24]
            if view=='node':
                if not isinstance(round_index,int) or round_index<0:raise ValueError('轮次编号无效')
                root=Path(task_dir).resolve()
                path=root/'research_monitor'/f'round_{round_index:06d}.json'
                before=stamp(path)
                record=confined_read(root,path)
                node=next((n for n in (record or {}).get('nodes',[]) if n.get('node_id')==node_id),None)
                if node is None:raise KeyError('节点记录不存在')
                node=copy.deepcopy(node)
                if not isinstance(node_id,int) or isinstance(node_id,bool) or node_id<0:raise ValueError('节点编号无效')
                apply_manifest(node,confined_read(root,root/f'node_{node_id}'/'artifact_manifest.json'))
                if stamp(path)!=before:raise ValueError('节点正在更新，请稍后重新加载')
                return dict(node=dict(copy.deepcopy(node),_version=versions.get(round_index),_detail_loaded=True),cursor=cursor)
            previous=state['cursors'].get(since) if since else None
            changed=[i for i in sorted(state['rounds']) if previous is None or previous.get(i)!=versions.get(i)]
            rows=[]
            for i in changed:
                record=state['rounds'][i]
                row={k:preview(v,2500) for k,v in record.items() if k not in ('nodes','dispatch')}
                row['nodes']=record.get('nodes',[])
                row['_version']=versions.get(i);rows.append(row)
            state['cursors'][cursor]=versions
            while len(state['cursors'])>24:state['cursors'].popitem(last=False)
            return dict(rounds=rows,cursor=cursor,incremental=previous is not None,
                        round_indices=sorted(state['rounds']),warnings=state['errors'],protocol=2)
