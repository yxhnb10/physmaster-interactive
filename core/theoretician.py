import json
import os
from pathlib import Path
from typing import Dict, Any, List

from LANDAU.library import LibraryRetriever
from utils.llm_client import call_model
from utils.python_utils import run_python_code
from utils.skill_loader import build_skill_brief_prompt, load_skill_specs
from utils.tool_schemas import LIBRARY_TOOLS, THEORETICIAN_CORE_TOOLS

class Theoretician:
    """The solver agent. Given a subtask description and accumulated context,
    it calls the LLM with tool access to produce a physics solution."""

    def __init__(self, prompts_path: str = "prompts/", library_enabled: bool = True, config_path :str = 'config.yaml'):
        self.prompts_path = Path(prompts_path)
        self.library_enabled = bool(library_enabled)
        self.library_retriever = None
        if self.library_enabled:
            try:
                self.library_retriever = LibraryRetriever()
            except Exception as e:
                print(f"[Theoretician] Warning: LibraryRetriever init failed: {e}. Library search disabled for this node.")
                self.library_enabled = False
        self.config_path = config_path
        prompt_files = {
            "theoretician_prompt": "theoretician_prompt.txt",
            "theoretician_system_prompt": "theoretician_system_prompt.txt",
        }
        for attr, filename in prompt_files.items():
            setattr(self, attr, self._load_prompt(filename))
        self.prompt_template = self.theoretician_prompt

    def _load_prompt(self, filename: str) -> str:
        """Read a prompt template file from the prompts directory."""
        path = self.prompts_path / filename
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _library_search(self, query: str, top_k: int = 5):
        if self.library_retriever is None:
            return "[library_search] arXiv search is not available."
        try:
            results = self.library_retriever.search(query=query, top_k=int(top_k) if top_k is not None else 5)
            return self.library_retriever.format_for_llm(results)
        except Exception as e:
            return f"[library_search] failed: {e}"

    def _log_tool_call(self, tool_name: str, node_metadata: Dict[str, Any] | None):
        node_id = node_metadata.get("node_id", "") if node_metadata else ""
        subtask_id = node_metadata.get("subtask_id", "") if node_metadata else ""
        node_type = node_metadata.get("node_type", "") if node_metadata else ""
        print(
            f"[Theoretician] "
            f"(node_id={node_id} subtask_id={subtask_id} node_type={node_type}) "
            f"tool call {tool_name} 🛠️"
        )


    def solve(
        self,
        subtask_description: str,
        path_memory: str | None = None,
        node_metadata: Dict[str, Any] | None = None,
        prior_knowledge: str | None = None,
        parent_critic_feedback: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Run the LLM with tools to solve a single subtask. Returns the
        raw LLM response string."""
        output_dir = str(node_metadata.get("output_dir", "")) if node_metadata else ""

        prompt = self.prompt_template.format(
            subtask=subtask_description,
            memory=(path_memory or ""),
            node_metadata=json.dumps(node_metadata or {}, ensure_ascii=False),
            path=output_dir,
        )

        # Prepend prior knowledge as reference material if available
        if prior_knowledge:
            prompt = f"## Prior Knowledge (Reference Materials)\n{prior_knowledge}\n\n{prompt}"

        # For revise nodes: prepend parent's critic feedback so the LLM
        # sees "what went wrong last time" before anything else
        if parent_critic_feedback:
            decision = parent_critic_feedback.get("decision", "")
            reward = parent_critic_feedback.get("reward", "")
            opinion = parent_critic_feedback.get("opinion", "")
            analysis = parent_critic_feedback.get("analysis", "")

            feedback_block = (
                f"## Prior Attempt Feedback (Revision Target)\n"
                f"Decision: {decision} | Reward: {reward}\n\n"
                f"Issues to address:\n{opinion}\n\n"
                f"Guidance for this revision:\n{analysis}\n\n"
                f"---\n\n"
            )
            prompt = feedback_block + prompt

        tools = THEORETICIAN_CORE_TOOLS + (LIBRARY_TOOLS if self.library_enabled else [])
        tool_functions = {
            "Python_code_interpreter": run_python_code,
            "load_skill_specs": load_skill_specs,
        }
        if self.library_enabled:
            tool_functions["library_search"] = self._library_search

        # Wrap raw tool functions with logging so we can trace tool usage per node
        system_prompt = self.theoretician_system_prompt
        tool_call_log: list = []  # collects {"tool", "args", "result"} for node_log

        def _wrap(name, fn):
            def wrapper(**kwargs):
                self._log_tool_call(name, node_metadata)
                result = fn(**kwargs)
                tool_call_log.append({
                    "tool": name,
                    "arguments": kwargs,
                    "result": result,
                })
                return result
            return wrapper

        wrapped_tool_functions = {
            "Python_code_interpreter": _wrap("Python_code_interpreter",
                                             lambda **kw: run_python_code(cwd=output_dir or None, **kw)),
            "load_skill_specs": _wrap("load_skill_specs", load_skill_specs),
        }
        if self.library_enabled:
            wrapped_tool_functions["library_search"] = _wrap("library_search", self._library_search)

        # Prepend a brief of all available skills so the LLM can decide to load any
        prompt = build_skill_brief_prompt() + "\n\n" + prompt

        node_type_for_role = (node_metadata or {}).get("node_type", "draft")
        if node_type_for_role == "draft":
            chosen_model = "deepseek-flash"
        else:
            chosen_model = "deepseek-v4-pro"

        response = call_model(
            system_prompt=system_prompt,
            user_prompt=prompt,
            tools=tools,
            tool_functions=wrapped_tool_functions,
            model_name=chosen_model,
            max_tool_calls=8,
            config_path=self.config_path,
        )

        return response, tool_call_log


def run_theo_node(payload: Dict[str, Any],config_path:str = 'config.yaml') -> Dict[str, Any]:
        """Top-level function called by ProcessPoolExecutor in a subprocess.
        Deserializes the payload, runs one Theoretician solve, and returns
        the result dict with log_path."""

        depth = payload["depth"]
        node_id = int(payload["node_id"])
        structured_problem = payload["structured_problem"]
        description = payload["subtask"]["description"]
        task_dir = str(Path(payload["task_dir"]).resolve())

        # Immutable contract snapshot from the dispatcher, shared by this batch.
        description = ("## Authoritative task contract\n"
                       + json.dumps(structured_problem, ensure_ascii=False, indent=2)
                       + "\nLater live_updates override conflicting earlier conditions; "
                       + "do not treat earlier results as verified under changed assumptions.\n"
                       + "## Current subtask\n" + description)

        theoretician = Theoretician(library_enabled=bool(payload.get("library_enabled", True)),config_path=config_path)

        # Each node gets its own output directory for generated files
        node_output_dir = str((Path(task_dir) / f"node_{node_id}").resolve())
        os.makedirs(node_output_dir, exist_ok=True)

        node_metadata = {
            "depth": depth,
            "node_id": node_id,
            "subtask_id": payload["subtask"]["id"],
            "node_type": payload["node_type"],
            "task_dir": task_dir,
            "output_dir": node_output_dir,
        }

        result, tool_call_log = theoretician.solve(
            subtask_description=description,
            path_memory=payload.get("hcc_context", payload.get("path_memory", "")),
            node_metadata=node_metadata,
            prior_knowledge=payload.get("prior_knowledge", ""),
            parent_critic_feedback=payload.get("parent_critic_feedback"),
        )
        print(
            f"[Theoretician] "
            f"(node_id={node_id} subtask_id={payload['subtask']['id']} node_type={payload['node_type']}) "
            f"task completed ✅"
        )

        return {
            "result": result,
            "tool_calls": tool_call_log,
            "log_path": str(node_output_dir),
            "depth": depth,
            "node_id": node_id,
        }
