"""Re-review a pending immutable node, without starting a solver or publishing it."""
import argparse
import json
from pathlib import Path


def retry(task_dir, node_id, cfg_file=None):
    from utils.recovery import RunLock, read_checkpoint, restore_supervisor, run_locked
    from utils.research_monitor import safe_read
    from utils.research_integrity import _node_file, _file_hash
    from utils.live_updates import atomic_json
    from utils.review_routing import route_review
    from utils.failure_classifier import classify_failure
    from utils.node_scores import score_node
    from core.v93_pipeline import NodePipeline
    from core.supervisor import SupervisorOrchestrator
    root=Path(task_dir).resolve()
    if run_locked(root):
        raise ValueError('研究或旧工作进程仍在运行；请先停止它们再重新评审')
    cfg=Path(cfg_file).resolve() if cfg_file else root.parent/'web_config.yaml'
    if not cfg.is_file():
        raise ValueError('找不到此任务的配置；用 --cfg-file 指定原运行配置')
    with RunLock(root):
        checkpoint=read_checkpoint(root)
        records=(checkpoint.get('supervisor') or {}).get('nodes', [])
        stored=next((r['fields'] for r in records if r['fields'].get('node_id')==node_id), None)
        if not stored or (stored.get('evaluation') or {}).get('acceptance_state')!='review_pending':
            raise ValueError('只允许重新评审已记录为 review_pending 的节点；不修改已验收节点')
        manifest=safe_read(root/f'node_{node_id}'/'artifact_manifest.json') or {}
        for row in manifest.get('artifacts', []):
            if row.get('sha256') and _file_hash(_node_file(root,row['path'])) != row['sha256']:
                raise ValueError('原成果已修改，不能按同一节点重新评审：'+row['path'])
        owner=SupervisorOrchestrator(checkpoint['contract'],str(root),processes=1,
            landau_prior_enabled=False,landau_library_enabled=False,config_path=str(cfg))
        restore_supervisor(owner,checkpoint['supervisor'])
        node=owner.tree.get_node(node_id)
        pipeline=NodePipeline(owner)
        previous=dict(node.evaluation or {})
        ev=pipeline._review(node)
        ev=owner._audit_node_result(node,node.subtask_payload or {},getattr(node,'current_tool_calls',[]),ev)
        for row in manifest.get('artifacts', []):
            if row.get('sha256') and _file_hash(_node_file(root,row['path'])) != row['sha256']:
                raise ValueError('评审期间原成果发生变化；未更新节点验收')
        ev=route_review(ev);failure=classify_failure(node.result,ev,getattr(node,'current_tool_calls',[]))
        ev.update(failure_class=failure['kind'],failure_reasons=failure['reasons'],pipeline_action=failure['action'],repair_route=failure['action'])
        if failure['action']=='reevaluate':
            ev.update(acceptance_state='review_pending',decision='to_revise',verdict='refine',review_exhausted=True)
        elif failure['kind']!='NONE':
            ev['acceptance_state']='blocked'
        ev=score_node(node.result,ev,node.subtask_payload,getattr(node,'current_tool_calls',[]))
        ev.update(final_decision=ev['decision'],critic_decision=ev.get('model_decision'),
            decision_mismatch=ev.get('model_decision')!=ev['decision'])
        node.evaluation=ev
        delta=owner._extract_reward(ev)-float(node.reward or 0)
        node.reward=owner._extract_reward(ev)
        current=node
        while current:
            current.total_reward+=delta
            current.average_reward=current.total_reward/current.visits if current.visits else 0
            current=current.parent
        node.repair_status='review_pending' if failure['action']=='reevaluate' else 'report_repair' if failure['action']=='report_repair' else 'not_needed'
        node.knowledge='[REVIEW RETRY] 原成果已重新评审；当前验收状态：'+ev['acceptance_state']+'。参阅节点结果和原始评审，不沿用上次评审失败归因。'
        pipeline.artifacts.record_node(node.node_id,node.result,ev)
        histories=root/f'node_{node_id}'/'review_retries'
        histories.mkdir(exist_ok=True)
        import time
        atomic_json(histories/f'{time.time_ns()}.json',dict(previous_evaluation=previous,evaluation=ev,
            scope='仅重新评审原成果；没有启动 Theoretician、重算模型或发布文件'))
        for path in (root/'research_monitor').glob('round_*.json'):
            data=safe_read(path)
            if isinstance(data,dict) and any(n.get('node_id')==node_id for n in data.get('nodes', [])):
                owner.monitor.current=data;owner.monitor.node(node,'finished',getattr(node,'current_tool_calls',[]));break
        # Existing recovery resumes tree progress and final publication under normal gates.
        # A review retry itself never makes an old completion report current.
        owner.recovery_stage='search';owner.recovery_result=None
        owner.save_recovery()
        return dict(node_id=node_id,acceptance_state=ev['acceptance_state'],repair_route=ev['repair_route'],
            decision=ev['decision'],next_step='在网页恢复研究，或用 run.py --cfg_file 原运行配置 --resume 恢复同一任务，完成后续签收与总结')


def main():
    parser=argparse.ArgumentParser(description='只重新评审待评审节点，不重算模型')
    parser.add_argument('--task-dir',required=True,help='含 research_checkpoint.json 的 query 目录')
    parser.add_argument('--node',type=int,required=True)
    parser.add_argument('--cfg-file',help='默认读取 query 上一级 web_config.yaml')
    args=parser.parse_args()
    try:
        print(json.dumps(retry(args.task_dir,args.node,args.cfg_file),ensure_ascii=False,indent=2))
    except (OSError,ValueError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
