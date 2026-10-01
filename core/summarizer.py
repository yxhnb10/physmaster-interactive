import json
from pathlib import Path
from typing import Any, Dict, List

from utils.llm_client import call_model_without_tools


class TrajectorySummarizer:
    """Final pipeline stage. Takes the best MCTS trajectory and asks the
    LLM to produce a readable markdown summary of the solution."""

    def __init__(self, prompts_path: str = "prompts/",config_path:str = 'config.yaml'):
        self.prompts_path = Path(prompts_path)
        self.summarizer_prompt = self._load_prompt("summarizer_prompt.txt")
        self.summarizer_system_prompt = self._load_prompt("summarizer_system_prompt.txt")
        self.config_path = config_path

    def _load_prompt(self, filename: str) -> str:
        """Read a prompt template from the prompts directory."""
        with open(self.prompts_path / filename, "r", encoding="utf-8") as f:
            return f.read()

    def build_summary_markdown(
        self,
        *,
        task_description: str,
        trajectory: List[Dict[str, Any]],
    ) -> str:
        """Generate a markdown summary via LLM. Falls back to a plain
        template if the LLM call fails."""
        prompt = self.summarizer_prompt.format(
            task_description=task_description or "",
            trajectory=json.dumps(trajectory or [], ensure_ascii=False, indent=2),
        )
        try:
            summary_md = call_model_without_tools(
                system_prompt=self.summarizer_system_prompt,
                user_prompt=prompt,
                role="summarizer",
                config_path=self.config_path
            ).strip()
            if summary_md:
                return summary_md
        except Exception:
            pass

        # Fallback summary to keep pipeline robust.
        lines = ["# Summary", "", "## Task", task_description or "", "", "## Trajectory"]
        for i, node in enumerate(trajectory or [], start=1):
            lines.append(
                f"{i}. node={node.get('node_id')} subtask={node.get('subtask_id')} reward={node.get('reward')}"
            )
            desc = str(node.get("description") or "").strip()
            if desc:
                lines.append(f"   - {desc}")
        return "\n".join(lines).strip() + "\n"

    def write_summary_markdown(
        self,
        output_path: str | Path,
        *,
        task_description: str,
        trajectory: List[Dict[str, Any]],
    ) -> Path:
        """Build the summary and write it to disk. Returns the output path."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary_md = self.build_summary_markdown(
            task_description=task_description,
            trajectory=trajectory,
        )
        out.write_text(summary_md + ("" if summary_md.endswith("\n") else "\n"), encoding="utf-8")
        return out
