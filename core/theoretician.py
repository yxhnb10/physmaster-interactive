import json
import os
from pathlib import Path
from typing import Dict, Any, List
import yaml
from utils.python_utils import execution_receipt
from utils.research_integrity import node_contract_for_solver

from LANDAU.library import LibraryRetriever
from utils.llm_client import call_model, LLMResponseError
from utils.python_utils import run_python_code
from utils.skill_loader import build_skill_brief_prompt, load_skill_specs
from utils.tool_schemas import LIBRARY_TOOLS, THEORETICIAN_CORE_TOOLS
from utils.capabilities import capability_values
from utils.continuation import tool_schemas, list_files, read_file, reuse_file, finish_reuse, baseline_previews

class Theoretician:
    """The solver agent. Given a subtask description and accumulated context,
    it calls the LLM with tool access to produce a physics solution."""

    def __init__(self, prompts_path: str = "prompts/", library_enabled: bool = True, config_path :str = 'config.yaml'):
        self.prompts_path = Path(prompts_path)
        self.config_path = config_path
        cfg_path = Path(config_path)
        cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) if cfg_path.is_file() else {}
        flags = capability_values(cfg or {})
        self.library_enabled = bool(library_enabled) and flags['arxiv_search']
        self.python_enabled = flags['python']
        self.skills_enabled = flags['skills']
        self.library_retriever = None
        if self.library_enabled:
            try:
                self.library_retriever = LibraryRetriever()
            except Exception as e:
                print(f"[Theoretician] Warning: LibraryRetriever init failed: {e}. Library search disabled for this node.")
                self.library_enabled = False
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
    ) -> tuple[str, list]:
        """Return the final response and tool receipts, including on delivery failure."""
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

        allowed = set()
        if self.python_enabled:
            allowed.add('Python_code_interpreter')
        if self.skills_enabled:
            allowed.add('load_skill_specs')
        tools = [tool for tool in THEORETICIAN_CORE_TOOLS if tool['function']['name'] in allowed]
        if self.library_enabled:
            tools += LIBRARY_TOOLS

        # Wrap raw tool functions with logging so we can trace tool usage per node
        system_prompt = self.theoretician_system_prompt
        tool_call_log: list = []  # collects {"tool", "args", "result"} for node_log

        def _wrap(name, fn):
            def wrapper(**kwargs):
                self._log_tool_call(name, node_metadata)
                result = fn(**kwargs)
                record = {
                    "tool": name,
                    "arguments": kwargs,
                    "result": result,
                }
                if name == 'Python_code_interpreter':
                    record['execution'] = execution_receipt(result)
                tool_call_log.append(record)
                return result
            return wrapper

        wrapped_tool_functions = {}
        inherited_task = (node_metadata or {}).get('task_dir')
        if inherited_task and (Path(inherited_task) / 'inheritance.json').is_file():
            tools += tool_schemas()
            wrapped_tool_functions['list_inherited_files'] = _wrap('list_inherited_files',
                lambda **kw: list_files(inherited_task, **kw))
            wrapped_tool_functions['read_inherited_file'] = _wrap('read_inherited_file',
                lambda **kw: read_file(inherited_task, output_dir, **kw))
            wrapped_tool_functions['reuse_inherited_file'] = _wrap('reuse_inherited_file',
                lambda **kw: reuse_file(inherited_task, output_dir, **kw))
        if self.python_enabled:
            wrapped_tool_functions['Python_code_interpreter'] = _wrap('Python_code_interpreter',
                lambda **kw: run_python_code(cwd=output_dir or None, **kw))
        else:
            prompt = ('Python execution is disabled for this task. Do not claim code or numerical '
                      'checks were executed. Clearly label unexecuted code and unverified results.\n\n' + prompt)
        if self.skills_enabled:
            wrapped_tool_functions['load_skill_specs'] = _wrap('load_skill_specs',
                lambda **kw: load_skill_specs(config_path=self.config_path, **kw))
        if self.library_enabled:
            wrapped_tool_functions["library_search"] = _wrap("library_search", self._library_search)

        # Prepend a brief of all available skills so the LLM can decide to load any
        if self.skills_enabled:
            prompt = build_skill_brief_prompt(config_path=self.config_path) + "\n\n" + prompt

        node_type_for_role = (node_metadata or {}).get("node_type", "draft")
        if node_type_for_role == "draft":
            chosen_model = "deepseek-flash"
        else:
            chosen_model = "deepseek-v4-pro"

        try:
            response = call_model(
                system_prompt=system_prompt,
                user_prompt=prompt,
                tools=tools,
                tool_functions=wrapped_tool_functions,
                model_name=chosen_model,
                max_tool_calls=8,
                config_path=self.config_path,
            )
        except Exception as exc:
            # Keep completed tool receipts attached to the node. Files on disk
            # are candidates for repair, never automatically accepted artifacts.
            diagnostic = (exc.diagnostic if isinstance(exc, LLMResponseError)
                          else dict(reason='api_or_client_error', error_type=type(exc).__name__,
                                    message=str(exc)))
            response = json.dumps(dict(
                delivery_status='failed', delivery_error=diagnostic,
                analysis='Final answer delivery failed. Prior tool receipts are retained; '
                         'no result or artifact is accepted from this failure response.',
                primary_parameters={}, parameter_evidence={}, files=[],
                partial_response_unverified=getattr(exc, 'partial_response', ''),
            ), ensure_ascii=False)
            print(f"[Theoretician] final answer delivery failed: {exc}", flush=True)

        return response, tool_call_log


def run_theo_node(payload: Dict[str, Any],config_path:str = 'config.yaml') -> Dict[str, Any]:
        """Top-level function called by ProcessPoolExecutor in a subprocess.
        Deserializes the payload, runs one Theoretician solve, and returns
        the result dict with log_path."""

        depth = payload["depth"]
        node_id = int(payload["node_id"])
        structured_problem = payload["structured_problem"]
        from core.research_lifecycle import stage_contract
        description = payload["subtask"]["description"] + "\n## Research stage scope\n" + json.dumps(stage_contract(payload["subtask"]), ensure_ascii=False)
        task_dir = str(Path(payload["task_dir"]).resolve())

        theoretician = Theoretician(library_enabled=bool(payload.get("library_enabled", True)),config_path=config_path)

        # Each node gets its own output directory for generated files
        node_output_dir = str((Path(task_dir) / f"node_{node_id}").resolve())
        os.makedirs(node_output_dir, exist_ok=True)

        # Keep the dispatcher's contract unchanged. Solver paths refer to its
        # own node; final archive paths are explicitly owned by the program.
        solver_contract = node_contract_for_solver(structured_problem,node_output_dir)
        if solver_contract.get('continuation_context'):
            solver_contract['inherited_code_previews'] = baseline_previews(task_dir, node_output_dir)
        description = ("## Authoritative task contract\n"
                       + json.dumps(solver_contract, ensure_ascii=False, indent=2)
                       + "\nLater live_updates override conflicting earlier conditions; "
                       + "do not treat earlier results as verified under changed assumptions.\n"
                       + "Generate files only in current_node_dir. Return node-relative file and "
                       + "parameter_evidence paths. Final archive paths are handled by the program.\n"
                       + "## Current subtask\n" + description)

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
        from utils.research_integrity import parse_output
        finish_reuse(task_dir, node_output_dir, parse_output(result))
        try:
            delivery_failed = json.loads(result).get('delivery_status') == 'failed'
        except (ValueError, AttributeError, TypeError):
            delivery_failed = False
        print(
            f"[Theoretician] "
            f"(node_id={node_id} subtask_id={payload['subtask']['id']} node_type={payload['node_type']}) "
            + ("node finished; final answer delivery failed ❌" if delivery_failed else "node response returned ✅")
        )

        return {
            "result": result,
            "tool_calls": tool_call_log,
            "log_path": str(node_output_dir),
            "depth": depth,
            "node_id": node_id,
        }
