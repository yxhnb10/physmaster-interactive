"""Observable research artifacts, not hidden reasoning or inferred confidence."""
import json
import time
from pathlib import Path
from utils.live_updates import atomic_json


def safe_read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


class ResearchMonitor:
    def __init__(self, task_dir):
        self.task_dir=Path(task_dir)
        self.root=self.task_dir/'research_monitor'
        self.root.mkdir(parents=True,exist_ok=True)
        self.current=None

    def _save(self):
        if self.current is not None:
            try:
                atomic_json(self.root/f"round_{self.current['round_index']:06d}.json",self.current)
            except OSError as exc:
                print(f'[Monitor] Could not publish round report: {exc}',flush=True)

    def start(self, round_index, revision, dispatch, parent_id):
        self.current=dict(round_index=round_index, round=round_index+1,
            revision=revision,state='running',started_at=time.time(),
            parent_id=parent_id,goal=dispatch.get('description',''),
            subtask=dispatch.get('subtask',{}),nodes=[])
        self._save()

    def node(self,node,stage,tool_calls=None):
        if self.current is None:return
        node_dir=self.task_dir/f'node_{node.node_id}'
        files=[]
        if node_dir.exists():
            for path in sorted(node_dir.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    files.append(dict(path=path.relative_to(self.task_dir).as_posix(),
                        bytes=path.stat().st_size))
        record=dict(node_id=node.node_id,parent_id=node.parent.node_id if node.parent else None,
            subtask_id=node.subtask_id,node_type=node.node_type,stage=stage,
            status=node.status,goal=node.subtask_description,
            result=node.result,evaluation=node.evaluation,knowledge=node.knowledge,
            artifacts=files,updated_at=time.time())
        if tool_calls is not None:
            record['tools']=[dict(tool=t.get('tool',''),arguments=t.get('arguments',{})) for t in tool_calls]
        existing=next((r for r in self.current['nodes'] if r['node_id']==node.node_id),None)
        if existing:
            if 'tools' in existing and 'tools' not in record:record['tools']=existing['tools']
            existing.clear();existing.update(record)
        else:self.current['nodes'].append(record)
        self._save()

    def finish(self, nodes):
        for node in nodes:self.node(node,'finished')
        self.current['state']='finished'
        self.current['finished_at']=time.time()
        self._save()


def list_rounds(task_dir):
    # Incomplete or inaccessible report never breaks the dashboard.
    root=Path(task_dir)/'research_monitor'
    return [r for path in sorted(root.glob('round_*.json')) if (r:=safe_read(path)) is not None]
