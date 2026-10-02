"""
Feishu Bot — Pipeline worker

Calls the PhysMaster pipeline (Clarifier → MCTS → Summarizer) and returns
the summary text.  Designed to be invoked from a background thread so the
bot can reply asynchronously.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Ensure project root is on sys.path so we can import core.* / utils.* etc.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# CWD must be project root before importing core modules, because
# clarifier.py loads prompt files relative to CWD at import time.
os.chdir(str(_PROJECT_ROOT))

# Heavy imports (torch, faiss, sentence_transformers, etc.) are deferred
# to solve() so that bot.py can start the WebSocket connection instantly.
from run import load_config, get_task_name


def solve(
    query_text: str,
    config_path: str = "config.yaml",
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Execute the full PhysMaster pipeline for *query_text* and return a result
    dict with keys: task_name, summary, task_dir.

    Parameters
    ----------
    query_text : str
        Raw physics problem text (e.g. from the Feishu message).
    config_path : str
        Path to the main PhysMaster config.yaml (relative to project root).
    progress_cb : callable, optional
        Called with a status string at each major pipeline stage.
        e.g. progress_cb("Clarifier done, starting MCTS...")

    Returns
    -------
    dict  {"task_name": str, "summary": str, "task_dir": str}
    """
    # --- resolve config_path relative to project root ---
    cfg_path = (_PROJECT_ROOT / config_path).resolve()
    config_path_str = str(cfg_path)
    cfg = load_config(config_path_str)

    # Make sure CWD is project root so that relative paths in config work
    prev_cwd = os.getcwd()
    os.chdir(str(_PROJECT_ROOT))

    try:
        return _run_pipeline(query_text, cfg, config_path_str, progress_cb)
    finally:
        os.chdir(prev_cwd)


def _run_pipeline(
    query_text: str,
    cfg: dict,
    config_path: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    def _report(msg: str):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    _report("[1/4] Loading modules...")

    # Lazy import: these pull in torch/faiss/etc. Only pay the cost when actually solving.
    from core.clarifier import Clarifier
    from core.supervisor import SupervisorOrchestrator
    from core.summarizer import TrajectorySummarizer

    pipeline_cfg = cfg.get("pipeline", {})
    clarifier_cfg = cfg.get("clarifier", {})
    output_root = pipeline_cfg.get("output_path", "outputs")
    clarifier_cfg["output_path"] = output_root

    landau_cfg = cfg.get("landau", {})
    library_enabled = bool(landau_cfg.get("library_enabled", True))
    workflow_enabled = bool(landau_cfg.get("workflow_enabled", True))
    prior_enabled = bool(landau_cfg.get("prior_enabled", True))
    wisdom_save_enabled = bool(landau_cfg.get("wisdom_save_enabled", False))

    mcts_cfg = cfg.get("mcts", {})

    project_root = _PROJECT_ROOT
    workflow_root = (
        project_root
        / landau_cfg.get("workflow", landau_cfg.get("methodology", "LANDAU/workflow"))
    ).resolve()
    prior_root = (project_root / landau_cfg.get("prior", "LANDAU/prior")).resolve()

    clarifier_cfg["workflow_dir"] = str(workflow_root)

    # ---- 1. Clarify ----
    _report("[2/4] Clarifier: analyzing problem structure...")
    clr = Clarifier(clarifier_cfg, workflow_enabled=workflow_enabled, config_path=config_path)
    structured_problem = clr.run(query_text)
    structured_problem["instruction_filename"] = "feishu_query"

    task_name = get_task_name(structured_problem)
    task_dir = Path(output_root) / task_name
    os.makedirs(task_dir, exist_ok=True)

    with open(task_dir / "contract.json", "w", encoding="utf-8") as f:
        json.dump(structured_problem, f, ensure_ascii=False, indent=2)

    # count subtasks for the status message
    subtask_list = (
        structured_problem.get("sub-tasks")
        or structured_problem.get("sub_tasks")
        or structured_problem.get("subtasks")
        or []
    )
    n_sub = len(subtask_list) if isinstance(subtask_list, list) else 1
    _report(f"[2/4] Clarifier done. {n_sub} subtask(s) identified. Starting MCTS search...")

    # ---- 2. MCTS ----
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
    )
    mcts_result = supervisor.run()
    trajectory = mcts_result.get("trajectory", []) or []

    total_nodes = mcts_result.get("total_nodes", 0)
    total_rounds = mcts_result.get("total_rounds", 0)
    completed = len(mcts_result.get("completed_subtasks", []))
    _report(
        f"[3/4] MCTS search done. "
        f"{total_rounds} rounds, {total_nodes} nodes explored, "
        f"{completed}/{n_sub} subtask(s) completed. Generating summary..."
    )

    # ---- 3. Wisdom (optional) ----
    if wisdom_save_enabled and prior_enabled:
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

    # ---- 4. Summarize ----
    _report("[4/4] Writing summary...")
    summarizer = TrajectorySummarizer(prompts_path="prompts/", config_path=config_path)
    summary_md_path = task_dir / "summary.md"
    contract_for_summary = json.dumps(structured_problem, ensure_ascii=False, indent=2)
    summarizer.write_summary_markdown(
        summary_md_path,
        task_description=contract_for_summary,
        trajectory=trajectory,
    )
    summary_text = summary_md_path.read_text(encoding="utf-8")

    return {
        "task_name": task_name,
        "summary": summary_text,
        "task_dir": str(task_dir),
    }
