"""Observable research artifacts, not hidden reasoning or inferred confidence."""
import json
import time
from pathlib import Path
from utils.live_updates import atomic_json
from utils.artifact_manager import ArtifactManager
from utils.node_scores import failure_categories


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
        self.artifact_manager=ArtifactManager(self.task_dir)

    def _save(self):
        if self.current is not None:
            try:
                atomic_json(self.root/f"round_{self.current['round_index']:06d}.json",self.current)
            except OSError as exc:
                print(f'[Monitor] Could not publish round report: {exc}',flush=True)

    def start(self, round_index, revision, dispatch, parent_id):
        self._round_started = time.monotonic()
        self.current=dict(round_index=round_index, round=round_index+1,
            revision=revision,state='running',started_at=time.time(),
            parent_id=parent_id,goal=dispatch.get('description',''),
            dispatch_adjusted=(dispatch.get('supervisor_dispatch') or {}).get('dispatch_adjusted',False),
            dispatch_reason=(dispatch.get('supervisor_dispatch') or {}).get('dispatch_reason',''),
            requested_subtask_id=(dispatch.get('supervisor_dispatch') or {}).get('requested_subtask_id'),
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
            artifacts=files,updated_at=time.time(),
            research_stage=getattr(node, 'research_stage', None),
            pipeline_version=getattr(node, 'pipeline_version', None),
            repair_attempt=getattr(node, 'repair_attempt', 0),
            repair_parent_id=getattr(node, 'repair_parent_id', None),
            repair_child_id=getattr(node, 'repair_child_id', None),
            repair_status=getattr(node, 'repair_status', None),
            repair_outcome=getattr(node, 'repair_outcome', None),
            review_history=getattr(node, 'review_history', []))
        if tool_calls is not None:
            record['tools']=[dict(tool=t.get('tool',''),arguments=t.get('arguments',{}),execution=t.get('execution')) for t in tool_calls]
        existing=next((r for r in self.current['nodes'] if r['node_id']==node.node_id),None)
        history=list((existing or {}).get('timeline') or [])
        if not history or history[-1]['stage']!=stage:
            history.append(dict(stage=stage,at=time.time()))
        record['timeline']=history
        try:
            if getattr(node, 'pipeline_version', None):
                manifest = safe_read(node_dir/'artifact_manifest.json') or {'artifacts': [], 'invalid_declarations': []}
            else:
                manifest=self.artifact_manager.record_node(node.node_id,node.result,node.evaluation)
            record['artifacts']=manifest['artifacts']
            record['invalid_declarations']=manifest['invalid_declarations']
        except (OSError,ValueError) as exc:
            record['manifest_error']=str(exc)
        record['failure_categories']=failure_categories(node.result,node.evaluation,
            tool_calls if tool_calls is not None else (existing or {}).get('tools'), 'failed' if node.status=='failed' else stage)
        if existing:
            if 'tools' in existing and 'tools' not in record:record['tools']=existing['tools']
            existing.clear();existing.update(record)
        else:self.current['nodes'].append(record)
        self._save()

    def finish(self, nodes):
        for node in nodes:self.node(node,'finished')
        self.current['state']='finished'
        self.current['finished_at']=time.time()
        self.current['duration_seconds']=max(0.0,time.monotonic()-self._round_started)
        self._save()


def list_rounds(task_dir):
    # Incomplete or inaccessible report never breaks the dashboard.
    root=Path(task_dir)/'research_monitor'
    rounds=[r for path in sorted(root.glob('round_*.json')) if isinstance((r:=safe_read(path)),dict)]
    for record in rounds:
        for node in record.get('nodes',[]):
            manifest=safe_read(Path(task_dir)/f"node_{node['node_id']}"/'artifact_manifest.json')
            if isinstance(manifest,dict):
                node['artifacts']=manifest.get('artifacts',node.get('artifacts',[]))
                published=[a.get('published_at') for a in node['artifacts'] if a.get('status')=='published' and a.get('published_at')]
                if published:node['timeline']=(node.get('timeline') or [])+[dict(stage='published',at=max(published))]
    return rounds
