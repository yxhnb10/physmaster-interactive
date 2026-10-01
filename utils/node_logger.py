"""Node-level debug logging for PhysMaster MCTS pipeline.

Writes detailed logs per node under:
  outputs/<task_name>/node_<id>/node_log.json

Contains the FULL content of:
  - input context sent to the Theoretician
  - every tool call (name, arguments, full result)
  - Theoretician output
  - Critic evaluation
  - distilled knowledge
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class NodeLogger:
    """Captures full debug data for a single MCTS node."""

    def __init__(self, log_dir: str | Path, node_id: int):
        self.log_dir = Path(log_dir)
        self.node_id = node_id
        self.log_path = self.log_dir / "node_log.json"
        self._start_time = time.time()
        self._data: Dict[str, Any] = {
            "node_id": node_id,
            "start_time": self._start_time,
            "input": {},
            "tool_calls": [],
            "output": None,
            "evaluation": None,
            "knowledge": None,
            "timing": {},
        }

    def log_input(
        self,
        subtask_id: int,
        node_type: str,
        subtask_description: str,
        context: str,
        prior_knowledge: str,
    ):
        """Save the full context and prior knowledge that the Theoretician sees."""
        self._data["input"] = {
            "subtask_id": subtask_id,
            "node_type": node_type,
            "subtask_description": subtask_description,
            "context": context,
            "context_chars": len(context),
            "prior_knowledge": prior_knowledge,
            "prior_knowledge_chars": len(prior_knowledge),
        }

    def log_tool_call(self, tool_name: str, arguments: Any, result: str):
        """Save a complete tool call: name, full arguments, full result."""
        self._data["tool_calls"].append({
            "tool": tool_name,
            "arguments": arguments,
            "result": result,
            "time": round(time.time() - self._start_time, 2),
        })

    def log_output(self, result: Any):
        """Save the full Theoretician output."""
        self._data["output"] = result

    def log_evaluation(self, evaluation: Any, reward: float):
        """Save the full Critic evaluation."""
        self._data["evaluation"] = {
            "raw": evaluation,
            "reward": reward,
        }

    def log_knowledge(self, knowledge: str):
        """Save the distilled knowledge string."""
        self._data["knowledge"] = knowledge

    def save(self):
        self._data["timing"]["total_seconds"] = round(time.time() - self._start_time, 2)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)


class PipelineLogger:
    """Manages per-node loggers and writes a pipeline-level summary."""

    def __init__(self, task_dir: str | Path):
        self.task_dir = Path(task_dir)
        self._node_loggers: Dict[int, NodeLogger] = {}
        self._round_log: List[Dict[str, Any]] = []

    def get_node_logger(self, node_id: int) -> NodeLogger:
        if node_id not in self._node_loggers:
            log_dir = self.task_dir / f"node_{node_id}"
            self._node_loggers[node_id] = NodeLogger(log_dir, node_id)
        return self._node_loggers[node_id]

    def log_round(self, round_index: int, selected_node_id: int, dispatch: Dict[str, Any], new_node_ids: List[int]):
        self._round_log.append({
            "round": round_index,
            "selected_node": selected_node_id,
            "node_type": dispatch.get("node_type", ""),
            "subtask_id": dispatch.get("subtask", {}).get("id", ""),
            "expansion_count": dispatch.get("expansion_count", 0),
            "new_nodes": new_node_ids,
        })

    def save_summary(self, mcts_result: Dict[str, Any]):
        summary = {
            "total_rounds": mcts_result.get("total_rounds", 0),
            "total_nodes": mcts_result.get("total_nodes", 0),
            "completed_subtasks": len(mcts_result.get("completed_subtasks", [])),
            "rounds": self._round_log,
        }
        summary_path = self.task_dir / "pipeline_log.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
