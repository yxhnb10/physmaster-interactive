import json
import yaml
import os
import argparse

from pathlib import Path
from typing import Any, Dict, Tuple

from core.supervisor import SupervisorOrchestrator
from core.summarizer import TrajectorySummarizer
from core.clarifier import Clarifier
from core.visualization import generate_vis
from utils.skill_loader import resolve_skill_roots
from utils.live_updates import UpdateInbox
from utils.runtime_timing import RunTimer
from utils.hard_parameters import resolve_parameters, bind_contract, parameter_text
from utils.research_integrity import complete_run, finalize_summary
from utils.finalizer import FinalizationAgent, write_delivery_index
from utils.continuation import attach_context, planning_brief


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    """Read and parse the YAML config file. All pipeline behavior is
    controlled by sections in this file: pipeline, clarifier, landau,
    mcts, skills, visualization, llm."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def get_task_name(structured_problem) -> str:
    """Derive a filesystem-safe task name from the contract. Uses
    instruction_filename first, falls back to topic."""
    raw_name = str(
        structured_problem.get("instruction_filename")
        or structured_problem.get("topic")
    )
    # Sanitize: keep only alphanumeric, dot, underscore, dash
    task_name = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(raw_name))
    return task_name



def clarify_query(query_path: str, clr_cfg, workflow_enabled: bool = True, config_path:str = "config.yaml", hard_parameters=None, user_inputs=None) -> Dict[str, Any]:
    """Read the query file, run it through the Clarifier LLM to produce
    a structured contract, then save contract.json to the task directory.
    Returns (structured_problem, task_dir, task_name)."""
    path = query_path
    with open(path, 'r', encoding='utf-8') as file:
        content = file.read()
    inherited_dir = Path(clr_cfg.get('output_path', 'outputs')) / Path(path).stem
    content += planning_brief(inherited_dir)
    print(f"[Clarifier] Clarifying query from: {path}")

    if workflow_enabled:
        print(f"[LANDAU] Workflow: enabled")
    else:
        print("[LANDAU] Workflow: disabled")

    clr = Clarifier(clr_cfg, workflow_enabled=workflow_enabled, config_path=config_path)
    if hard_parameters:
        content += '\n\n## User-locked primary parameters (must not change)\n'+parameter_text(hard_parameters)
    structured_problem = clr.run(content)

    # Tag the contract with the source filename for traceability
    instruction_filename = Path(path).stem
    structured_problem["instruction_filename"] = instruction_filename
    task_name = get_task_name(structured_problem)

    # Create the output directory for this task (e.g. outputs/task_name/)
    output_root = clr_cfg.get("output_path", "outputs")
    task_dir = Path(output_root) / task_name
    os.makedirs(task_dir, exist_ok=True)
    bind_contract(structured_problem,hard_parameters or [],user_inputs or [content],task_dir)
    attach_context(structured_problem, task_dir)

    # Persist the contract so Theoretician subprocesses can read it from disk
    with open(task_dir / "contract.json", "w", encoding="utf-8") as f:
        json.dump(structured_problem, f, ensure_ascii=False, indent=2)
    print(f"[Clarifier] Structured problem saved to: {task_dir / 'contract.json'}")
    
    return structured_problem, task_dir, task_name


def main(config_path: str = "config.yaml"):
    cfg = load_config(config_path)
    pipeline = cfg.get('pipeline', {})
    task_dir = Path(pipeline.get('output_path', 'outputs')) / Path(
        pipeline.get('query_file', 'instructions/test.txt')).stem
    timing = RunTimer(task_dir)
    old_root = os.environ.get('PHY_TIMING_ROOT')
    os.environ['PHY_TIMING_ROOT'] = str(task_dir.resolve())
    success = False
    try:
        _run_pipeline(config_path, cfg, timing)
        success = True
    finally:
        timing.finish(success)
        if old_root is None:
            os.environ.pop('PHY_TIMING_ROOT', None)
        else:
            os.environ['PHY_TIMING_ROOT'] = old_root


def _run_pipeline(config_path, cfg, timing):
    """Orchestrate the full PhysMaster pipeline from config loading
    through to summary generation and optional visualization."""
    print(f"config file path: {config_path}")

    # ---- Extract config sections ----
    pipeline_cfg = cfg.get("pipeline", {})
    clarifier_cfg = cfg.get("clarifier", {})
    query_path = pipeline_cfg.get("query_file", "instructions/test.txt")
    output_root = pipeline_cfg.get("output_path", "outputs")
    clarifier_cfg["output_path"] = output_root
    integrity_cfg = cfg.get('integrity', {}) or {}
    user_inputs = pipeline_cfg.get('authoritative_user_inputs') or [Path(query_path).read_text(encoding='utf-8')]
    hard_parameters = resolve_parameters(user_inputs,integrity_cfg.get('hard_parameter_text'),integrity_cfg.get('hard_parameters'))

    # ---- LANDAU feature flags ----
    landau_cfg = cfg.get("landau", {})
    library_enabled = bool(landau_cfg.get("library_enabled", True))
    workflow_enabled = bool(landau_cfg.get("workflow_enabled", True))
    prior_enabled = bool(landau_cfg.get("prior_enabled", True))
    wisdom_save_enabled = bool(landau_cfg.get("wisdom_save_enabled", False))
    skills_cfg = cfg.get("skills", {})
    skills_enabled = bool(skills_cfg.get("enabled", True))

    mcts_cfg = cfg.get("mcts", {})
    vis_cfg = cfg.get("visualization",{})

    # ---- Resolve LANDAU paths relative to project root ----
    project_root = Path(__file__).resolve().parent
    library_root = (project_root / landau_cfg.get("library", "LANDAU/library")).resolve()
    workflow_root = (
        project_root
        / landau_cfg.get("workflow", landau_cfg.get("methodology", "LANDAU/workflow"))
    ).resolve()
    prior_root = (project_root / landau_cfg.get("prior", "LANDAU/prior")).resolve()

    # Pass the resolved workflow dir to the clarifier so it can find YAML files
    clarifier_cfg["workflow_dir"] = str(workflow_root)

    # ---- Stage 1: Clarify the query into a structured contract ----
    timing.change('clarifying')
    structured_problem, task_dir, task_name = clarify_query(
        query_path, clarifier_cfg, workflow_enabled=workflow_enabled, config_path=config_path,
        hard_parameters=hard_parameters,user_inputs=user_inputs,
    )
    structured_problem['validation_policy'] = {'python_enabled':bool(cfg.get('tools',{}).get('python_enabled',True))}
    from utils.live_updates import atomic_json
    atomic_json(task_dir/'contract.json',structured_problem)
    timing.change('initializing')

    # ---- Log enabled features ----
    if library_enabled:
        print(f"[LANDAU] Library: {library_root}")
    else:
        print("[LANDAU] Library: disabled")
    if workflow_enabled:
        print(f"[LANDAU] Workflow Dir: {workflow_root}")
    else:
        print("[LANDAU] Workflow Dir: disabled")
    if skills_enabled:
        print("[Skills] Roots:")
        for root in resolve_skill_roots(config_path):
            print(f"  - {root}")
    else:
        print("[Skills] disabled")
    if prior_enabled:
        print(f"[LANDAU] Prior: {prior_root}")
    else:
        print("[LANDAU] Prior: disabled")

    # ---- Stage 2: MCTS search via Supervisor ----
    processes = pipeline_cfg.get("parallel_processes", 2)
    max_rounds = pipeline_cfg.get("max_rounds", 8)

    supervisor = SupervisorOrchestrator(
        structured_problem=structured_problem,
        task_dir=task_dir,
        processes=processes,
        max_rounds=max_rounds,
        draft_expansion=mcts_cfg.get("draft_expansion", 2),
        revise_expansion=mcts_cfg.get("revise_expansion", 2),
        exploration_constant=mcts_cfg.get("exploration_constant", 1.414),
        active_beam_width=mcts_cfg.get("active_beam_width", 0),
        landau_library_enabled=library_enabled,
        landau_prior_enabled=prior_enabled,
        config_path=config_path,
        debug_logging=bool(pipeline_cfg.get("debug_logging", False)),
        timing=timing,
    )

    # Optional per-run inbox. A second terminal can submit updates at any time.
    inbox = None
    if pipeline_cfg.get("live_updates_enabled", False):
        original_query = Path(query_path).read_text(encoding="utf-8")
        instruction_filename = structured_problem["instruction_filename"]
        def rebuild_contract(history):
            amendments = "\n\n".join(
                f"Update {i}: {text}" for i, text in enumerate(history, 1)
            )
            text = (original_query + "\n\n## User amendments, in chronological order\n"
                    + amendments + "\n\nPreserve all non-conflicting requirements. "
                    + "Later amendments override earlier conflicting requirements. "
                    + "Replan ALL affected subtasks and validation criteria.")
            contract = Clarifier(clarifier_cfg, workflow_enabled=workflow_enabled,
                                 config_path=config_path).run(text)
            contract["instruction_filename"] = instruction_filename
            inputs = list(user_inputs)+list(history)
            params = resolve_parameters(inputs, configured=hard_parameters)
            bind_contract(contract,params,inputs,task_dir)
            attach_context(contract, task_dir)
            contract['validation_policy'] = structured_problem['validation_policy']
            return contract
        inbox = UpdateInbox(task_dir, timing=timing)
        supervisor.update_inbox = inbox
        supervisor.rebuild_contract = rebuild_contract
        print(f"[LiveUpdate] Inbox ready for task: {task_dir}")
    try:
        timing.change('orchestration')
        mcts_result = supervisor.run()
    finally:
        if inbox is not None:
            inbox.close()

    trajectory = mcts_result.get("trajectory", []) or []
    timing.change('delivery')
    completion = complete_run(structured_problem,trajectory,task_dir,mcts_result.get('stop_reason','unknown'))
    timing.change('finalization')
    mcts_result, completion = FinalizationAgent(task_dir,cfg.get('finalization')).run(
        structured_problem,mcts_result,completion,supervisor)
    trajectory=mcts_result.get('trajectory',[]) or []
    atomic_json(task_dir/'trajectory.json',trajectory)
    if getattr(supervisor,'logger',None):
        supervisor.logger.save_summary(mcts_result)

    # ---- Stage 4: Generate markdown summary from the best trajectory ----
    timing.change('summary')
    summarizer = TrajectorySummarizer(prompts_path="prompts/",config_path=config_path)
    summary_md_path = task_dir / "summary.md"
    contract_for_summary = json.dumps(dict(structured_problem,completion_report=completion), ensure_ascii=False, indent=2)
    summarizer.write_summary_markdown(
        summary_md_path,
        task_description=contract_for_summary,
        trajectory=trajectory,
    )
    completion = finalize_summary(summary_md_path,completion)
    write_delivery_index(task_dir,completion)
    summary_text = summary_md_path.read_text(encoding="utf-8")
    print("[Summrizer] Summary generated:", summary_md_path)

    # ---- Stage 3 (optional): Distill L3 wisdom into the prior index ----
    # L3 Wisdom accumulation
    if wisdom_save_enabled and prior_enabled and completion['status']=='passed':
        timing.change('wisdom')
        try:
            from LANDAU.prior.wisdom_store import WisdomStore
            ws = WisdomStore(prior_root, config_path=config_path)
            ws.save(
                structured_problem=structured_problem,
                trajectory=trajectory,
                completed_subtasks=mcts_result.get("completed_subtasks", []),
                task_name=task_name,
            )
        except Exception as e:
            print(f"[Wisdom] Failed to save wisdom: {e}")
    elif wisdom_save_enabled:
        print("[Wisdom] Skipped: prior disabled or research has not passed final checks")

    # ---- Stage 5 (optional): Generate interactive HTML tree visualization ----
    if vis_cfg.get("enabled",False):
        timing.change('visualization')
        vis_path = task_dir / "visualization.html"
        generate_vis(
            vis_path,
            supervisor.tree,
            task_description=structured_problem.get("task_description", ""),
            subtasks=supervisor.subtasks,
            summary=summary_text,
        )
        print("[Visulization] visualization saved:", vis_path)
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='physmaster')
    parser.add_argument('--cfg_file', '-c', type=str, default="config.yaml",
                        help='config file path')
    args = parser.parse_args()
    main(args.cfg_file)
