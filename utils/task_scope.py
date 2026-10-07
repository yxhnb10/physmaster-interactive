"""Task-independent requirements; reference material never creates user locks."""

RULES = (
    'TASK SCOPE: derive requirements from this task\'s authoritative user inputs and '
    'explicit per-task parameters. Examples, workflow templates, retrieved Prior/Wisdom, '
    'old reviews and historical summaries are reference material, not new user requirements. '
    'Do not import their numeric values, filenames, tolerances or domain-specific checks '
    'into an unrelated task. Preserve applicable user constraints; explicit later user '
    'amendments supersede the changed conditions only. Explain unresolved conflicts. '
    'Use computation, code, data, literature or reports only as required by the task; '
    'a reasoning task need not invent Python, CSV, plots or PDF deliverables. '
    'Validate substantive requirements using relevant evidence. Never infer scientific '
    'validity or practical feasibility solely from successful code execution or file presence.'
)


def ensure_fresh_run(task_dir):
    """Prevent an independent CLI run from overwriting or reusing old run state."""
    from pathlib import Path
    root=Path(task_dir)
    if any((root/name).exists() for name in ('contract.json','completion.json','summary.md','final_manifest.json')) or any(root.glob('node_*')):
        raise ValueError('输出目录已有研究记录，不能作为独立新任务复用：'+str(root)+
            '。请在配置中设置新的 pipeline.output_path；继续旧问题请使用网页续研。')


def bind_scope(contract, task_dir):
    """Program-owned scope, independent of the model's inferred domain/continuation."""
    from pathlib import Path
    incremental = (Path(task_dir) / 'inheritance.json').is_file()
    contract['task_scope'] = dict(mode='incremental' if incremental else 'independent',
        policy=RULES, parameter_names=[p['name'] for p in contract.get('hard_parameters', [])])
    if not incremental:
        contract.pop('continuation_context', None)
        contract.pop('continuation_plan', None)
    return contract
