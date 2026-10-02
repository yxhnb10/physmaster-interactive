"""Bounded report-only finalization, reviewed through the ordinary node gates."""
import json
from pathlib import Path
from utils.live_updates import atomic_json
from utils.research_integrity import complete_run


class FinalizationAgent:
    def __init__(self, task_dir, config=None):
        self.root = Path(task_dir)
        values = config or {}
        self.enabled = values.get('enabled', True)
        attempts = values.get('max_attempts', 1)
        if not isinstance(self.enabled, bool) or isinstance(attempts, bool) or not isinstance(attempts, int) or not 0 <= attempts <= 3:
            raise ValueError('finalization.enabled 必须为布尔值，max_attempts 必须为 0～3 的整数')
        self.max_attempts = attempts

    def run(self, contract, result, report, supervisor):
        record = dict(schema_version=1, state='skipped', attempts=[], reason='',
                      scope='只补最终报告交付；不会替代科学评审或重新运行整项研究')
        def save(): atomic_json(self.root/'finalization.json', record)
        if not self.enabled or self.max_attempts == 0:
            record['reason']='配置关闭了额外的报告整理节点'; save(); return result, report
        states = report.get('subtasks') or []
        subtasks = getattr(supervisor, 'subtasks', [])
        if not isinstance(subtasks, list): subtasks=[]
        missing = [a for a in report.get('artifacts',[]) if a['status'] != 'available']
        eligible = (len(states)>1 and len(subtasks)==len(states)
                    and all(s['status']=='passed' for s in states[:-1])
                    and states[-1]['status']!='passed'
                    and subtasks[-1].get('subtask_type')=='analysis'
                    and bool(missing) and all(a['path'].lower().endswith('.pdf') for a in missing))
        if not eligible:
            record['reason']='已齐备，或仍有科学／代码／数据子任务未验收；不进行报告专用补交'; save(); return result, report
        record['state']='running'; save()
        base_id=states[-2]['node_id']
        parent=supervisor.tree.get_node(base_id)
        if parent is None:
            record.update(state='skipped',reason='无法定位本次已验收的前置分支'); save(); return result,report
        for attempt in range(self.max_attempts):
            if parent.status=='completed_closed' and parent.is_subtask_complete():
                parent.status='completed'  # Reopen only the selected accepted prerequisite.
            task=dict(subtasks[-1], research_stage='FINALIZATION')
            missing_names=[a['path'] for a in report['artifacts'] if a['status']!='available']
            evidence=json.dumps(dict(artifacts=report['artifacts'],hard_parameters=report['hard_parameters'],
                                     prior_subtasks=report['subtasks'][:-1]),ensure_ascii=False,indent=2)
            task['description']=('REPORT FINALIZATION ONLY. '+task['description']+
                '\nUse the current-run accepted source files below; verify their hashes before citing. '
                'Produce ONLY the missing final report(s): '+', '.join(missing_names)+'. '
                'Do not rerun or replace the numerical model/CSV, invent results, repair science by prose, '
                'or treat unaccepted attempts as verified. Carry unresolved limitations into the report; '
                'if the existing evidence cannot satisfy the report contract, explicitly return needs revision. '
                'Declare the user-locked primary parameters and relative report paths in non-empty JSON. '
                'Validate PDF page count/readability by parsing/rendering when dependencies are available. '
                'The original report/table/content requirements still apply.\n## Accepted evidence\n'+evidence)
            round_index=supervisor.round_counter
            dispatch=dict(description=task['description'],subtask=task,phase='finalization')
            supervisor.monitor.start(round_index,supervisor.live_revision,dispatch,parent.node_id)
            nodes=supervisor._expand_and_simulate_nodes(parent,'revise',1,task,task['description'],dispatch,round_index)
            supervisor.monitor.finish(nodes)
            supervisor.round_counter+=1
            if not nodes:
                record['reason']='整理节点未能派发';break
            record['attempts'].extend(dict(node_id=n.node_id,round_index=round_index,
                                            decision=(n.evaluation or {}).get('decision'), repair_attempt=getattr(n, 'repair_attempt', 0)) for n in nodes)
            result=dict(result,trajectory=supervisor._find_best_trajectory(),
                        total_rounds=supervisor.round_counter,total_nodes=len(supervisor.tree.get_all_nodes()),
                        completed_subtasks=supervisor._collect_completed_subtasks(),tree_stats=supervisor.tree.get_tree_stats())
            report=complete_run(contract,result['trajectory'],self.root,result.get('stop_reason','unknown'))
            save()
            if report['status']=='passed': break
            parent=nodes[-1]
        record.update(state='passed' if report['status']=='passed' else 'partial',
                      reason='整理后的文件已通过普通 Critic 与程序核对' if report['status']=='passed' else '补交未通过，保留原始核对问题')
        result['finalization']=record
        save(); return result,report


def write_delivery_index(task_dir, report):
    """Deterministic evidence index; never manufactures missing scientific files."""
    root=Path(task_dir)
    lines=['# PhysMaster v9.4 Incremental 成果索引','',
           '任务状态：'+report['status']+'。文件验收不等同于独立科学验证。','',
           '| 文件 | 状态 | 本次来源 | SHA-256 |','| --- | --- | --- | --- |']
    for row in report.get('artifacts',[])+report.get('additional_artifacts',[]):
        lines.append('| '+' | '.join(str(row.get(k) or '—').replace('|','\\|') for k in ('path','status','source','sha256'))+' |')
    lines.extend(['','## 尚未完成的内容','']+([ '- '+r for r in report.get('reasons',[])] or ['无']))
    lines.extend(['','详细核对见 `completion.json`；发布清单见 `final/manifest.json`。',
                  '最终报告若缺失，不能用此成果索引代替合同要求的科学报告。'])
    if (root/'inheritance.json').is_file():
        from utils.continuation import inheritance_view
        inherited=inheritance_view(root)
        lines.extend(['','## 完整继承与增量续研','',
            f"完整继承 {inherited['file_count']} 个历史文件，来自 {len(inherited['sources'])} 代任务。",
            f"上下文提供 {inherited['context_count']}，工具读取 {inherited['read_count']}，复制复用 {inherited['copied_count']}，声明不受影响 {inherited['unchanged_count']}。",
            '完整目录和哈希见 `inherited_index.md`、`inheritance.json`；网页可查看来源、旧评审及本轮使用记录。',
            '原文件评分不转为本轮评分。任意 Python 文件读取不自动推断为工具读取。'])
    (root/'delivery_index.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
