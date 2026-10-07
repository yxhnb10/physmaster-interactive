"""Rebuild a summary from saved evidence; never run or accept research nodes."""
import argparse
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from utils.live_updates import atomic_json, atomic_text
from utils.recovery import RunLock, run_locked, new_execution_generation

ROOT = Path(__file__).resolve().parent


def rebuild(task_dir, config_path='config.yaml', member=None):
    root = Path(task_dir).resolve()
    if run_locked(root):raise ValueError('研究进程仍在运行，请等待结束或安全中断后再重建总结')
    for name in ('contract.json','completion.json','trajectory.json'):
        path=root/name
        if not path.is_file() or path.is_symlink():raise ValueError('缺少普通研究记录文件：'+name)
    contract=json.loads((root/'contract.json').read_text(encoding='utf-8'))
    report=json.loads((root/'completion.json').read_text(encoding='utf-8'))
    trajectory=json.loads((root/'trajectory.json').read_text(encoding='utf-8'))
    if not isinstance(contract,dict) or not isinstance(report,dict) or not isinstance(trajectory,list):
        raise ValueError('研究记录格式无效')
    if member:
        from utils.team_workspace import TeamStore
        store=TeamStore(ROOT/'team_data')
        profile=store.profile(member)
        if not profile or not profile.get('api_key'):raise ValueError('该成员尚未保存个人 API 设置')
        llm=__import__('yaml').safe_load(Path(config_path).read_text(encoding='utf-8')).get('llm',{})
        if llm.get('api_key_env')!='PHY_TEAM_LLM_API_KEY':raise ValueError('--member 应配合该任务的 web_config.yaml 使用')
        if profile['base_url'].rstrip('/')!=llm.get('base_url','').rstrip('/'):
            raise ValueError('成员 API 地址与任务原配置不一致')
        os.environ['PHY_TEAM_LLM_API_KEY']=profile['api_key']
    from core.summarizer import TrajectorySummarizer
    from utils.final_consistency import adopted_evidence, FinalConsistencyReviewer
    from utils.research_integrity import finalize_summary
    from utils.finalizer import write_delivery_index
    with RunLock(root):
        # Cancel queued stale starts and reject still-running worker leases.
        new_execution_generation(root)
        packet=dict(contract,completion_report=report,adopted_artifact_evidence=adopted_evidence(root,report))
        summary=TrajectorySummarizer(prompts_path=ROOT/'prompts',config_path=str(config_path))
        text=summary.build_summary_markdown(task_description=json.dumps(packet,ensure_ascii=False),trajectory=trajectory)
        if summary.generation.get('status')!='generated':
            atomic_json(root/'summary_rebuild_failure.json',summary.generation)
            raise ValueError('总结重建未成功；原总结和研究文件保留，原因见 summary_rebuild_failure.json')
        backup=root/'summary_backups'/ (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        if (root/'summary_backups').is_symlink():raise ValueError('总结备份目录不能为符号链接')
        backup.mkdir(parents=True)
        for name in ('summary.md','summary_generation.json','completion.json','final_consistency.json','final_manifest.json','final/manifest.json','delivery_index.md'):
            source=root/name
            if source.is_symlink():raise ValueError('研究文档不能为符号链接：'+name)
            if source.is_file():
                dest=backup/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        old_status=report.get('status')
        atomic_text(root/'summary.md',text+'\n')
        atomic_json(root/'summary_generation.json',dict(summary.generation,source='manual_summary_rebuild'))
        report=FinalConsistencyReviewer(config_path=str(config_path)).run(root,contract,report,trajectory)
        if old_status!='passed':
            report['status']='partial'
            note='仅重建总结，未重新执行或验收研究节点；原部分完成状态保留'
            if note not in report.setdefault('reasons',[]):report['reasons'].append(note)
        report=finalize_summary(root/'summary.md',report)
        write_delivery_index(root,report)
        atomic_json(root/'summary_rebuild.json',dict(version='v9.5.2',backup=str(backup),status=report['status'],nodes_reexecuted=0))
        return dict(summary=str(root/'summary.md'),backup=str(backup),status=report['status'])


def main():
    p=argparse.ArgumentParser(description='使用已有成果重建总结与一致性评审，会调用 API，不重跑研究节点')
    p.add_argument('--task-dir',required=True,help='含 contract.json、completion.json、trajectory.json 的 query 目录')
    p.add_argument('--cfg_file',default='config.yaml')
    p.add_argument('--member',help='小组任务所有者的账号；配合该任务 web_config.yaml 使用')
    a=p.parse_args()
    # Prompts in the consistency reviewer resolve against the project root.
    task=Path(a.task_dir).resolve();config=Path(a.cfg_file).resolve();os.chdir(ROOT)
    try:result=rebuild(task,config,a.member)
    except Exception as exc:
        # Do not echo arbitrary API bodies or secrets in the CLI.
        from core.summarizer import safe_diagnostic
        print('重建未完成。',json.dumps(safe_diagnostic(exc),ensure_ascii=False))
        if isinstance(exc,ValueError):print('请检查任务记录、运行状态、账号与配置；失败记录保存在任务目录。')
        raise SystemExit(1)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
