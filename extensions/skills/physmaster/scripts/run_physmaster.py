#!/usr/bin/env python3
"""Run the PhysMaster pipeline from command line or as an importable function.

Usage:
    # Command line
    python run_physmaster.py --query "Derive the Euler-Lagrange equation"
    python run_physmaster.py --query_file instructions/test.txt
    python run_physmaster.py --query "..." --config path/to/config.yaml

    # From Python
    from scripts.run_physmaster import run_physmaster
    result = run_physmaster(query="your problem", config_path="config.yaml")
"""

import argparse
import json
import os
import sys
from pathlib import Path


def _find_project_root():
    """Find PHY_Master project root by looking for marker files."""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "core").is_dir() and (p / "run.py").is_file():
            return p
        p = p.parent
    raise RuntimeError(
        "Cannot find PHY_Master project root. "
        "Expected to find 'core/' directory and 'run.py' file."
    )


# Ensure project root is on sys.path
_PROJECT_ROOT = _find_project_root()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(str(_PROJECT_ROOT))

from run import load_config, get_task_name


def run_physmaster(
    query: str = "",
    query_file: str = "",
    config_path: str = "config.yaml",
) -> dict:
    """Run the full PhysMaster pipeline.

    Parameters
    ----------
    query : str
        Physics problem text. Mutually exclusive with query_file.
    query_file : str
        Path to a text file containing the problem. Mutually exclusive with query.
    config_path : str
        Path to config.yaml.

    Returns
    -------
    dict with keys: task_name, summary, task_dir
    """
    from core.clarifier import Clarifier
    from core.supervisor import SupervisorOrchestrator
    from core.summarizer import TrajectorySummarizer

    if query_file and not query:
        query = Path(query_file).read_text(encoding="utf-8")
    if not query.strip():
        raise ValueError("Either --query or --query_file must be provided.")

    cfg_path = (_PROJECT_ROOT / config_path).resolve()
    cfg = load_config(str(cfg_path))

    pipeline_cfg = cfg.get("pipeline", {})
    clarifier_cfg = cfg.get("clarifier", {})
    output_root = pipeline_cfg.get("output_path", "outputs")
    clarifier_cfg["output_path"] = output_root

    landau_cfg = cfg.get("landau", {})
    mcts_cfg = cfg.get("mcts", {})

    workflow_enabled = bool(landau_cfg.get("workflow_enabled", True))
    library_enabled = bool(landau_cfg.get("library_enabled", True))
    prior_enabled = bool(landau_cfg.get("prior_enabled", True))
    wisdom_save_enabled = bool(landau_cfg.get("wisdom_save_enabled", False))

    project_root = _PROJECT_ROOT
    workflow_root = (
        project_root
        / landau_cfg.get("workflow", landau_cfg.get("methodology", "LANDAU/workflow"))
    ).resolve()
    prior_root = (project_root / landau_cfg.get("prior", "LANDAU/prior")).resolve()
    clarifier_cfg["workflow_dir"] = str(workflow_root)

    # 1. Clarify
    print("[PhysMaster] Stage 1/4: Clarifying problem...")
    clr = Clarifier(clarifier_cfg, workflow_enabled=workflow_enabled, config_path=str(cfg_path))
    structured_problem = clr.run(query)
    structured_problem["instruction_filename"] = "physmaster_query"

    task_name = get_task_name(structured_problem)
    task_dir = Path(output_root) / task_name
    os.makedirs(task_dir, exist_ok=True)
    with open(task_dir / "contract.json", "w", encoding="utf-8") as f:
        json.dump(structured_problem, f, ensure_ascii=False, indent=2)

    # 2. MCTS
    print("[PhysMaster] Stage 2/4: Running MCTS search...")
    supervisor = SupervisorOrchestrator(
        structured_problem=structured_problem,
        task_dir=task_dir,
        processes=pipeline_cfg.get("parallel_processes", 2),
        max_rounds=pipeline_cfg.get("max_rounds", 8),
        draft_expansion=mcts_cfg.get("draft_expansion", 2),
        revise_expansion=mcts_cfg.get("revise_expansion", 2),
        exploration_constant=mcts_cfg.get("exploration_constant", 1.414),
        active_beam_width=mcts_cfg.get("active_beam_width", 0),
        landau_library_enabled=library_enabled,
        landau_prior_enabled=prior_enabled,
        config_path=str(cfg_path),
    )
    mcts_result = supervisor.run()
    trajectory = mcts_result.get("trajectory", []) or []
    print(
        f"[PhysMaster] MCTS done: {mcts_result.get('total_rounds', 0)} rounds, "
        f"{mcts_result.get('total_nodes', 0)} nodes, "
        f"{len(mcts_result.get('completed_subtasks', []))} subtask(s) completed."
    )

    # 3. Wisdom (optional)
    if wisdom_save_enabled and prior_enabled:
        print("[PhysMaster] Stage 3/4: Saving wisdom...")
        try:
            from LANDAU.prior.wisdom_store import WisdomStore
            ws = WisdomStore(prior_root, config_path=str(cfg_path))
            ws.save(
                structured_problem=structured_problem,
                trajectory=trajectory,
                completed_subtasks=mcts_result.get("completed_subtasks", []),
                task_name=task_name,
            )
        except Exception as e:
            print(f"[PhysMaster] Warning: wisdom save failed: {e}")
    else:
        print("[PhysMaster] Stage 3/4: Wisdom save skipped.")

    # 4. Summarize
    print("[PhysMaster] Stage 4/4: Generating summary...")
    summarizer = TrajectorySummarizer(prompts_path="prompts/", config_path=str(cfg_path))
    summary_md_path = task_dir / "summary.md"
    summarizer.write_summary_markdown(
        summary_md_path,
        task_description=json.dumps(structured_problem, ensure_ascii=False, indent=2),
        trajectory=trajectory,
    )
    summary_text = summary_md_path.read_text(encoding="utf-8")
    print(f"[PhysMaster] Done. Output: {task_dir}")

    return {
        "task_name": task_name,
        "summary": summary_text,
        "task_dir": str(task_dir),
    }


def main():
    parser = argparse.ArgumentParser(description="Run the PhysMaster pipeline")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", type=str, help="Physics problem text")
    group.add_argument("--query_file", "-f", type=str, help="Path to problem text file")
    parser.add_argument("--config", "-c", type=str, default="config.yaml", help="Config file path")
    args = parser.parse_args()

    result = run_physmaster(
        query=args.query or "",
        query_file=args.query_file or "",
        config_path=args.config,
    )
    print("\n" + "=" * 60)
    print(f"Task: {result['task_name']}")
    print(f"Output: {result['task_dir']}")
    print("=" * 60)
    print(result["summary"][:2000])
    if len(result["summary"]) > 2000:
        print(f"\n... ({len(result['summary'])} chars total, see summary.md for full text)")


if __name__ == "__main__":
    main()
