import json
import math
import multiprocessing as mp
import re
from concurrent.futures import ProcessPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml
from concurrent.futures.process import BrokenProcessPool

from utils.llm_client import call_model, call_model_without_tools
from utils.tool_schemas import (
    LIBRARY_SEARCH_TOOL,
    PRIOR_SEARCH_TOOL,
    WEB_SEARCH_TOOL,
    CODATA_TOOL
)
from utils.node_logger import PipelineLogger

from LANDAU.library import LibraryRetriever
from .mcts import MCTSNode, MCTSTree
from utils.live_updates import atomic_json
from utils.research_monitor import ResearchMonitor
from utils.runtime_timing import timed_stage
from utils.research_integrity import audit_node, enforce_audit, normalize_node_paths

try:
    from LANDAU.prior.prior_retrieve import PriorRetriever
except Exception:  # pragma: no cover - optional dependency (faiss)
    PriorRetriever = None

try:
    from .theoretician import run_theo_node
except Exception:  # pragma: no cover - optional dependency
    run_theo_node = None

_GLOBAL_POOL: ProcessPoolExecutor | None = None


def _init_worker():
    """Called once per subprocess to mark it as initialized. Prevents
    double-init when the pool reuses a worker process."""
    global _WORKER_INIT
    if "_WORKER_INIT" in globals():
        return
    _WORKER_INIT = True


class SupervisorOrchestrator:
    """MCTS-based orchestrator. Drives the search loop: select a leaf node,
    ask the Supervisor LLM for dispatch instructions, expand children via
    Theoretician workers in parallel, evaluate with Critic, backpropagate
    rewards, and optionally beam-prune."""

    def __init__(
        self,
        structured_problem,
        task_dir: str,
        processes: int = 2,
        max_rounds: int = 8,
        prompts_path: str = "prompts/",
        draft_expansion: int = 2,
        revise_expansion: int = 2,
        exploration_constant: float = 1.414,
        active_beam_width: int = 0,
        landau_library_enabled: bool = True,
        landau_prior_enabled: bool = True,
        config_path: str = 'config.yaml',
        debug_logging: bool = False,
        update_inbox=None,
        rebuild_contract=None,
        timing=None,
    ):
        self.timing = timing
        self.update_inbox = update_inbox
        self.rebuild_contract = rebuild_contract
        self.revision_start_round = 0
        self.live_revision = 0
        self.live_update_history = []
        self.structured_problem = structured_problem
        self.task_dir = task_dir
        self.monitor = ResearchMonitor(task_dir)
        self.processes = max(1, int(processes))
        self.max_rounds = max(1, int(max_rounds))
        self.revision_round_budget = self.max_rounds
        self.prompts_path = Path(prompts_path)
        self.draft_expansion = max(1, int(draft_expansion))
        self.revise_expansion = max(1, int(revise_expansion))
        self.exploration_constant = float(exploration_constant)
        self.active_beam_width = max(0, int(active_beam_width or 0))
        self.landau_library_enabled = bool(landau_library_enabled)
        self.landau_prior_enabled = bool(landau_prior_enabled)
        self.config_path = config_path

        if self.landau_prior_enabled and PriorRetriever is None:
            # faiss not installed: gracefully degrade
            print("[Supervisor] prior retriever unavailable; disable LANDAU prior search.")
            self.landau_prior_enabled = False

        self._prior_retriever: Optional[PriorRetriever] = None
        self._library_retriever: Optional[LibraryRetriever] = None
        # Register tool schemas the Supervisor and Critic can invoke mid-conversation
        self.kb_search_tools: List[Dict[str, Any]] = []
        if self.landau_library_enabled:
            self.kb_search_tools.append(LIBRARY_SEARCH_TOOL)
        if self.landau_prior_enabled:
            self.kb_search_tools.append(PRIOR_SEARCH_TOOL)
        with open(config_path, "r", encoding="utf-8") as f:
            _cfg = yaml.safe_load(f)
        self.tools_cfg = _cfg.get("tools", {}) or {}
        from utils.critic_policy import critic_settings_from_config
        self.critic_settings = critic_settings_from_config(_cfg)
        from core.repair_agent import repair_settings
        self.repair_settings = repair_settings(_cfg)
        self.web_enabled = bool(self.tools_cfg.get("web_enabled", False))
        self.codata_enabled = bool(self.tools_cfg.get("codata_enabled", True))

        if self.web_enabled:
            self.kb_search_tools.append(WEB_SEARCH_TOOL)
        if self.codata_enabled:
            self.kb_search_tools.append(CODATA_TOOL)

# 延迟初始化
        self._web_searcher = None
        # ---- 检索去重状态 ----
        self._seen_queries: set = set()   # 归一化后的 query
        self._seen_urls: set = set()      # 已经返回给 LLM 的 URL

        # ---- RetrievalCritic 配置 ----
        self.retrieval_critic_cfg = self.tools_cfg.get("retrieval_critic", {}) or {}
        self.retrieval_critic_enabled = bool(
            self.retrieval_critic_cfg.get("enabled", False)
        )
        self._retrieval_critic = None

        self._codata = None

        # Pre-fetch prior knowledge for the whole task so every node can reference it
        self.prior_knowledge = self._get_prior_knowledge(self.structured_problem)

        # Load all 8 prompt templates (system+user for each of 4 agents)
        prompt_files = {
            "critic_prompt": "critic_prompt.txt",
            "critic_system_prompt": "critic_system_prompt.txt",
            "supervisor_prompt": "supervisor_prompt.txt",
            "supervisor_system_prompt": "supervisor_system_prompt.txt",
            "theoretician_prompt": "theoretician_prompt.txt",
            "theoretician_system_prompt": "theoretician_system_prompt.txt",
            "promoter_prompt": "promoter_prompt.txt",
            "promoter_system_prompt": "promoter_system_prompt.txt",
        }
        for attr, filename in prompt_files.items():
            setattr(self, attr, self._load_prompt(filename))

        # Normalize subtask list from the contract (handles various key names and formats)
        self.subtasks = self._build_subtasks()

        # Create the search tree with a virtual root that acts as the starting point
        self.tree = MCTSTree(
            root_subtask_id=0,
            root_description="Virtual Root",
        )
        # Virtual root is pre-configured as "completed" so the first _select_leaf_node
        # returns it and triggers the initial expansion
        self.tree.root.node_type = "virtual"
        self.tree.root.status = "completed_expended"
        self.tree.root.subtask_description = "Virtual Root"
        self.tree.root.subtask_payload = None
        self.tree.root.evaluation = {
            "decision": "complete",
            "reward": 0.0,
            "verdict": "accept",
            "analysis": "Virtual root initialization.",
        }
        self.tree.root.supervisor_dispatch = {"node_type": "virtual", "description": "Virtual Root"}
        self.tree.root.supervisor_feedback = dict(self.tree.root.supervisor_dispatch)
        self.tree.root.visits = 1
        self.tree.root.total_reward = 0.0
        self.tree.root.average_reward = 0.0

        self.node_id_counter = 1
        self.round_counter = 0
        self.debug_logging = bool(debug_logging)
        self.logger = PipelineLogger(self.task_dir) if self.debug_logging else None
        
        # ---- 分工具预算 ----
        self._tool_call_counts = {
            "web_search": 0,
            "library_search": 0,
        }
        self._tool_budget = {
            "web_search": int(self.tools_cfg.get("max_web_calls", 4)),
            "library_search": int(self.tools_cfg.get("max_library_calls", 8)),
        }

        # Global pool is shared across supervisor instances to avoid spawn overhead
        global _GLOBAL_POOL
        if _GLOBAL_POOL is None:
            # spawn avoids CUDA fork issues in child processes
            mp.set_start_method("spawn", force=True)
            _GLOBAL_POOL = ProcessPoolExecutor(
                max_workers=self.processes,
                initializer=_init_worker,
            )

    @timed_stage('prior', enabled='landau_prior_enabled')
    def _get_prior_knowledge(self, structured_problem) -> str:
        """Retrieve top-3 prior references from the FAISS index at startup.
        These are included in every Theoretician prompt as background context."""
        if not self.landau_prior_enabled:
            return ""
        query = (
            structured_problem.get("task_description")
            or structured_problem.get("description")
            or structured_problem.get("topic")
            or ""
        )
        if not query.strip():
            return ""
        try:
            retriever = self._get_prior_retriever()
            results = retriever.retrieve(query=query, top_k=3)
            if not results:
                return ""
            blocks: List[str] = []
            for i, item in enumerate(results, 1):
                source = item.get("source", {}) or {}
                locator = item.get("locator", {}) or {}
                context = item.get("parent_text") or item.get("text", "")
                block_lines = [
                    f"[Prior Reference {i}]",
                    f"title={source.get('title', '')}",
                    f"citation={item.get('citation', '')}",
                    f"chapter:{locator.get('chapter', '')} section:{locator.get('section', '')}",
                    f"content={context}",
                ]
                blocks.append("\n".join(block_lines))
            prior_text = "\n\n".join(blocks)
            print(f"[Supervisor] Prior knowledge retrieved ({len(results)} references).")
            return prior_text
        except Exception as e:
            print(f"[Supervisor] Warning: prior knowledge retrieval failed: {e}. Continuing without prior context.")
            return ""

    def _load_prompt(self, filename: str) -> str:
        path = self.prompts_path / filename
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def run(self) -> Dict[str, Any]:
        """Main MCTS loop. Returns a summary dict with the best trajectory,
        completed subtasks, node count, round count, and tree statistics."""
        stop_reason = 'unknown'
        while True:
            self._control_checkpoint()
            self._apply_pending_updates()
            if self.round_counter >= self.max_rounds:
                stop_reason = 'round_budget_exhausted'
                break
            selected_node = self._select_leaf_node()
            if selected_node is None:
                stop_reason = 'no_selectable_node'
                break

            dispatch = self._resolve_dispatch(selected_node)
            if dispatch.get("stop_search", False):
                self._control_checkpoint()
                if self._apply_pending_updates():
                    continue
                stop_reason = 'no_dispatch'
                break

            if getattr(self, "monitor", None):
                self.monitor.start(self.round_counter,self.live_revision,dispatch,selected_node.node_id)
            selected_node.selected_round = self.round_counter
            new_nodes = self._expand_and_simulate_nodes(
                parent=selected_node,
                node_type=dispatch["node_type"],
                count=dispatch["expansion_count"],
                subtask=dispatch["subtask"],
                augmented_description=dispatch["description"],
                supervisor_dispatch=dispatch["supervisor_dispatch"],
                round_index=self.round_counter,
            )

            if getattr(self, "monitor", None):
                self.monitor.finish(new_nodes)

            # Log this round
            if self.logger:
                self.logger.log_round(
                    round_index=self.round_counter,
                    selected_node_id=selected_node.node_id,
                    dispatch=dispatch,
                    new_node_ids=[n.node_id for n in new_nodes],
                )

            self.round_counter += 1

            self._control_checkpoint()

            # Apply queued changes before testing completion under old conditions.
            if self._apply_pending_updates():
                continue

            # Early termination: all subtasks completed along one path
            if self._find_full_completion_path() is not None:
                stop_reason = 'all_subtasks_completed'
                break

        if self.update_inbox is not None:
            self.update_inbox.progress("summarizing", self.round_counter, self.live_revision)

        # After the loop, extract the best trajectory from the tree
        best_path_nodes = self._find_best_path_nodes()
        completed_subtasks = self._collect_completed_subtasks()
        trajectory = self._serialize_trajectory(best_path_nodes)

        summary = {
            "stop_reason": stop_reason,
            "contract_revision": self.live_revision,
            "completed_subtasks": completed_subtasks,
            "total_nodes": len(self.tree.get_all_nodes()),
            "total_rounds": self.round_counter,
            "tree_stats": self.tree.get_tree_stats(),
            "trajectory": trajectory,
        }

        if self.logger:
            self.logger.save_summary(summary)

        return summary

    def _control_checkpoint(self):
        if self.update_inbox is not None:
            self.update_inbox.checkpoint(self.round_counter, self.live_revision)

    def _apply_pending_updates(self):
        if self.update_inbox is None:
            return False
        items = self.update_inbox.pending()
        if not items:
            return False
        return self._replan_updates(items)

    @timed_stage('replanning')
    def _replan_updates(self, items):
        self.update_inbox.progress("replanning", self.round_counter, self.live_revision)
        history = self.live_update_history + [text for _, text in items]
        contract = self.rebuild_contract(history)
        if not isinstance(contract, dict) or not contract.get("task_description"):
            raise ValueError("Updated contract has no task_description; update not acknowledged")
        revision = self.live_revision + 1
        # Keep raw amendments even if the Clarifier omits a detail.
        contract["live_updates"] = history
        contract["contract_revision"] = revision
        # Replanning must not lose program-owned inherited source references.
        from utils.continuation import attach_context
        attach_context(contract, self.task_dir)
        archive = Path(self.task_dir) / "revisions" / f"revision_{self.live_revision}"
        atomic_json(archive / "contract.json", self.structured_problem)
        atomic_json(archive / "trajectory.json", self._find_best_trajectory())
        atomic_json(Path(self.task_dir) / "contract.json", contract)
        # No workers are active here. Mutate the shared dict so run.py's summary
        # and wisdom stages see the latest contract, too.
        self.structured_problem.clear()
        self.structured_problem.update(contract)
        self.subtasks = self._build_subtasks()
        # Scores and completion flags from previous conditions are invalid.
        # Preserve node files and monotonic node IDs, but begin a fresh tree.
        self.tree = MCTSTree(root_subtask_id=0, root_description="Virtual Root")
        root = self.tree.root
        root.node_type = "virtual"
        root.status = "completed_expended"
        root.evaluation = {"decision": "complete", "reward": 0.0,
                           "verdict": "accept", "analysis": "New contract revision"}
        root.visits = 1
        # A new task must be able to retrieve sources hidden by old dedup state.
        self._seen_queries.clear()
        self._seen_urls.clear()
        self._tool_call_counts = {name: 0 for name in self._tool_call_counts}
        self.prior_knowledge = self._get_prior_knowledge(self.structured_problem)
        self.revision_start_round = self.round_counter
        # Each accepted revision gets a fresh round budget.
        self.max_rounds = self.round_counter + self.revision_round_budget
        self.live_revision = revision
        self.live_update_history = history
        self.update_inbox.acknowledge(items, revision)
        self.update_inbox.progress("running", self.round_counter, revision)
        print(f"[LiveUpdate] Applied revision {revision}: {len(items)} update(s); replanning")
        return True

    @timed_stage('supervisor')
    def _resolve_dispatch(self, node: MCTSNode) -> Dict[str, Any]:
        """Ask the Supervisor LLM what to do next for this node, then figure
        out the target subtask, node type, and expansion count."""
        hcc_context = self.tree.get_context_for_node(node)

        supervisor_raw = ""
        try:
            supervisor_raw = self._call_supervisor(node, hcc_context=hcc_context) or ""
        except Exception:
            supervisor_raw = ""
            print("[Supervisor] call failed.")

        # Parse JSON from the Supervisor's free-text response
        supervisor_payload = self._extract_json_object(supervisor_raw)
        if not isinstance(supervisor_payload, dict):
            supervisor_payload = {}

        # Decide node_type: virtual root always drafts, otherwise follow critic decision
        default_decision = "to_redraft" if node.node_type == "virtual" else "to_revise"
        decision = str((node.evaluation or {}).get("decision", default_decision)).strip().lower()
        default_node_type = "draft" if node.node_type == "virtual" else self._decision_to_node_type(decision)

        # If no more subtasks left, stop the search
        default_subtask_id = self._default_next_subtask_id(node, decision)
        if default_subtask_id is None:
            return {"stop_search": True}

        requested_subtask_id = self._extract_requested_subtask_id(
            supervisor_payload, fallback=default_subtask_id)
        order = {int(s['id']): i for i, s in enumerate(self.subtasks)}
        dispatch_adjusted = requested_subtask_id != default_subtask_id
        dispatch_reason = ''
        if dispatch_adjusted:
            if order[requested_subtask_id] < order[default_subtask_id]:
                dispatch_reason = ('当前分支此前子任务已验收，拒绝隐式回退重做；'
                    f'模型建议 subtask {requested_subtask_id}，实际推进 subtask {default_subtask_id}。')
            else:
                dispatch_reason = ('当前分支的前置子任务尚未验收，不能跳过；'
                    f'模型建议 subtask {requested_subtask_id}，实际处理 subtask {default_subtask_id}。')
            print('[Supervisor] ' + dispatch_reason, flush=True)
        subtask_id = default_subtask_id
        subtask = self._get_subtask_by_id(subtask_id) or self._get_subtask_by_id(default_subtask_id)
        if not subtask:
            return {"stop_search": True}

        node_type = default_node_type if dispatch_adjusted else self._sanitize_node_type(supervisor_payload.get("node_type"), default_node_type)
        expansion_count = self._get_expansion_count_by_node_type(node_type)

        description = str(
            supervisor_payload.get("description")
            or supervisor_payload.get("subtask_description")
            or subtask.get("description")
            or node.subtask_description
            or ""
        ).strip() or str(subtask.get("description", "")).strip()

        if dispatch_adjusted:
            # A suggestion for a different task must not leak into this task's instructions.
            description = str(subtask.get('description', '')).strip()

        supervisor_dispatch = {
            "dispatch_adjusted": dispatch_adjusted,
            "dispatch_reason": dispatch_reason,
            "requested_subtask_id": requested_subtask_id,
            "actual_subtask_id": subtask_id,
            "node_type": node_type,
            "subtask_id": subtask["id"],
            "subtask": subtask,
            "description": description,
            "raw_supervisor_output": supervisor_raw,
        }
        return {
            "stop_search": False,
            "node_type": node_type,
            "expansion_count": max(1, int(expansion_count)),
            "subtask": subtask,
            "description": description,
            "supervisor_dispatch": supervisor_dispatch,
        }
    
    
    @timed_stage('promoter')
    def _call_promoter(self, node: MCTSNode) -> str:
        """Distill raw experience into compressed knowledge.
        The LLM reads the node's experience and evaluation,
        then produces a concise summary stored as node.knowledge
        and used in the tree context for future nodes."""

        if not self.promoter_prompt:
            return ""

        promotion_info = {
            "node_id": node.node_id,
            "subtask_id": node.subtask_id,
            "subtask_description": node.subtask_description,
            "node_type": node.node_type,
            "reward": node.reward,
            "evaluation": node.evaluation, 
            "raw_experience": node.experience
        }

        prompt = self.promoter_prompt.format(
            node_context=json.dumps(promotion_info, ensure_ascii=False, indent=2)
        )

        # Round-dependent model: exploration uses flash, convergence uses v4-pro
        if (self.round_counter - self.revision_start_round) < self.revision_round_budget * 2 // 3:
            promoter_role = "promoter"            # -> deepseek-flash
        else:
            promoter_role = "promoter_strong"     # -> deepseek-v4-pro
        print(
            f"[Supervisor] _call_promoter round={self.round_counter}/{self.max_rounds} "
            f"role={promoter_role}",
            flush=True,
        )

        response = call_model_without_tools(
            system_prompt=self.promoter_system_prompt,
            user_prompt=prompt,
            role=promoter_role,
            config_path=self.config_path
        )
        return response

    def _call_supervisor(self, node: MCTSNode, hcc_context: str) -> str:
        """Ask the Supervisor LLM to decide what subtask to work on next
        and whether to draft or revise. The supervisor sees the full
        structured problem plus the context built from the tree.
        It can also call library/prior search tools mid-conversation."""
        if not self.supervisor_prompt:
            return ""

        try:
            node_info = {
                "node_id": node.node_id,
                "subtask_id": node.subtask_id,
                "node_type": node.node_type,
                "subtask_description": node.subtask_description,
                "evaluation": node.evaluation,
                "result": node.result,
                "distilled_context": hcc_context,
            }
        except Exception:
            node_info = {}

        prompt = self.supervisor_prompt.format(
            structured=json.dumps(self.structured_problem, ensure_ascii=False, indent=2),
            node=json.dumps(node_info, ensure_ascii=False, indent=2),
        )

        response = call_model(
            system_prompt=self.supervisor_system_prompt,
            user_prompt=prompt,
            tools=self.kb_search_tools,
            tool_functions=self._kb_tool_functions("Supervisor", node),
            role="supervisor",
            config_path=self.config_path
        )
        return response

    @timed_stage('critic')
    def _call_critic(self, node: MCTSNode) -> Dict[str, Any]:
        """Evaluate a Theoretician's output. Returns decision, verdict,
        reward score, and textual analysis."""
        result_data = node.result or ""
        node_output = self._extract_json_object(result_data) if isinstance(result_data, str) else (result_data or {})
        if not isinstance(node_output, dict):
            node_output = {}

        core_results = node_output.get("core_results") or node_output.get("core_result") or ""
        analysis = node_output.get("analysis") or ""
        code = node_output.get("code") or ""
        files = node_output.get("files") or []

        from utils.review_evidence import collect_review_evidence
        context_str = json.dumps(
            {"analysis": analysis, "code": code, "files": files,
             "primary_parameters":node_output.get('primary_parameters'),
             "parameter_evidence":node_output.get('parameter_evidence'),
             "primary_cases":node_output.get('primary_cases'),
             "supplementary_cases":node_output.get('supplementary_cases'),
             "numerical_results":node_output.get('numerical_results'),
             "observed_file_evidence":collect_review_evidence(getattr(self,'task_dir',None),node)},
            ensure_ascii=False,
            indent=2,
        )
        from core.research_lifecycle import stage_contract
        stage_scope = stage_contract(getattr(node, "subtask_payload", {}))
        prompt = ("## Research stage scope\n" + json.dumps(stage_scope, ensure_ascii=False) + "\n## Current authoritative contract\n"
                  + json.dumps(self.structured_problem, ensure_ascii=False, indent=2)
                  + "\n## Current node scope\n" + json.dumps(dict(subtask_id=node.subtask_id,
                      subtask=getattr(node, 'subtask_payload', {}),description=getattr(node, 'subtask_description', '')),ensure_ascii=False)
                  + "\nEvaluate THIS subtask; do not demand a later subtask's CSV/PDF from an earlier reasoning node. "
                    "A final report node must verify its cited current-run sources. Hard parameters always apply.\n"
                  + "\nEvaluate compliance with the current contract and amendments.\n\n"
                  + self.critic_prompt.format(result=core_results, context=context_str,
                      accept_threshold=self.critic_settings['accept_threshold'],
                      redraft_threshold=self.critic_settings['redraft_threshold']))

        # Round-dependent model: exploration phase uses flash, convergence phase uses v4-pro
        total_rounds = self.revision_round_budget
        current_round = self.round_counter - self.revision_start_round
        if current_round < total_rounds * 2 // 3:
            critic_role = "critic"            # -> deepseek-flash
        else:
            critic_role = "critic_strong"     # -> deepseek-v4-pro
        print(
            f"[Supervisor] _call_critic round={current_round}/{total_rounds} "
            f"role={critic_role}",
            flush=True,
        )

        # Critic can also use library/prior search to verify the solution
        response = call_model(
            system_prompt=self.critic_system_prompt,
            user_prompt=prompt,
            tools=self.kb_search_tools,
            tool_functions=self._kb_tool_functions("Critic", node),
            role=critic_role,
            config_path=self.config_path
        )                                                                                                                                                                                                                                                                                                                        
        parsed = self._extract_json_object(response) 
        if not isinstance(parsed, dict):
            parsed = {}

        # Modern clients may explicitly label the scientific score. Legacy reward
        # remains accepted here; the composite is computed only after the audit.
        if 'science_reward' in parsed:
            parsed['reward'] = parsed['science_reward']

        from utils.critic_policy import gate_evaluation
        evaluation = gate_evaluation(parsed, self.critic_settings)
        blocking = parsed.get('blocking_issues', ['Critic 未明确返回 blocking_issues，关键问题尚未核对'])
        if not isinstance(blocking,list):blocking=['Critic blocking_issues 字段格式无效']
        evaluation['review_valid'] = isinstance(parsed.get('blocking_issues'), list) and evaluation.get('decision_valid', True)
        evaluation['research_stage'] = stage_scope['research_stage']
        evaluation['blocking_issues']=blocking
        if blocking:
            decision='to_redraft' if evaluation['decision']=='to_redraft' else 'to_revise'
            evaluation.update(decision=decision,verdict='reject' if decision=='to_redraft' else 'refine')
            evaluation['policy_reason']='；'.join(filter(None,[evaluation.get('policy_reason'),
                '存在未解决的关键评审问题或评审字段缺失，不能完成。']))
        evaluation['decision_adjusted']=evaluation['decision']!=evaluation['model_decision']
        opinion = self._to_natural_text(parsed.get("opinion"))
        analysis_text = self._to_natural_text(parsed.get("analysis") or parsed.get("summary") or opinion)
        if evaluation['policy_reason']:
            analysis_text += '\n[Critic 阈值规则] ' + evaluation['policy_reason']

        return {
            **evaluation,
            "opinion": opinion,
            "analysis": analysis_text,
            "code": code,
        }

    @timed_stage('theoretician')
    def _expand_and_simulate_nodes(
        self,
        parent: MCTSNode,
        node_type: str,
        count: int,
        subtask: Dict[str, Any],
        augmented_description: str,
        supervisor_dispatch: Dict[str, Any],
        round_index: int,
    ) -> List[MCTSNode]:
        """Spawn `count` Theoretician workers in parallel, collect their
        outputs, run Critic on each, distill knowledge, then backpropagate rewards."""
        global _GLOBAL_POOL  # <--- 关键：允许在重试时重新赋值全局变量

        if parent and parent.status == "completed_closed":
            return []

        subtask_id = int(subtask.get("id", 1))
        subtask_type = str(subtask.get("subtask_type", "reasoning"))

        if run_theo_node is None:
            raise RuntimeError("run_theo_node is unavailable.")

        # Step 1: Pre-create all child nodes and collect payloads (不立即提交)
        child_nodes: List[MCTSNode] = []
        payloads: List[Dict[str, Any]] = []  # <--- 新增：暂存所有 payload

        for _ in range(max(1, int(count))):
            node_id = int(self.node_id_counter)
            self.node_id_counter += 1

            child_node = MCTSNode(
                subtask_id=subtask_id,
                subtask_payload=subtask,
                node_id=node_id,
                node_type=node_type,
                subtask_description=augmented_description,
                status="open",
                created_by="supervisor",
            )
            child_node.supervisor_dispatch = dict(supervisor_dispatch)
            child_node.supervisor_feedback = dict(supervisor_dispatch)
            child_node.selected_round = round_index

            self.tree.add_node(child_node)
            parent.add_child(child_node)
            child_nodes.append(child_node)
            from core.research_lifecycle import stage_for
            child_node.research_stage = stage_for(subtask)
            self.monitor.node(child_node,"solving")

            # Step 2: Build context for this child (now parent's knowledge is visible)
            hcc_context = self.tree.get_context_for_node(child_node)

            # For revise nodes, highlight parent's critic feedback at the top of the prompt
            parent_feedback = None
            if node_type == "revise" and parent.evaluation:
                parent_feedback = {
                    "decision": parent.evaluation.get("decision"),
                    "reward": parent.evaluation.get("reward"),
                    "opinion": parent.evaluation.get("opinion"),
                    "analysis": parent.evaluation.get("analysis"),
                }

            payload = {
                "depth": parent.get_depth() + 1,
                "node_id": node_id,
                "node_type": node_type,
                "structured_problem": self.structured_problem,
                "subtask": {
                    "id": subtask_id,
                    "description": augmented_description,
                    "subtask_type": subtask_type,
                    "input": subtask.get("input"),
                    "expected_output": subtask.get("expected_output"),
                    "research_stage": stage_for(subtask),
                },
                "task_dir": self.task_dir,
                "hcc_context": hcc_context,
                "parent_critic_feedback": parent_feedback,
                "library_enabled": self.landau_library_enabled,
                "prior_knowledge": self.prior_knowledge,
            }
            payloads.append(payload)  # <--- 收集 payload，不在这里提交

            print(
                f"[Supervisor] "
                f"(node_id={node_id} subtask_id={subtask_id} node_type={node_type}) "
                f"task assigned 📖"
            )

        # Step 3: 统一提交任务，带进程池损坏重试机制
        futures = []
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                futures = [
                    _GLOBAL_POOL.submit(run_theo_node, payload, self.config_path)
                    for payload in payloads
                ]
                break  # 提交成功，跳出重试循环
            except BrokenProcessPool as e:
                print(f"[Supervisor] Broken process pool. (Try {attempt + 1}/{max_retries + 1}): {e}")
                if attempt == max_retries:
                    raise  # 重试耗尽，向上抛出

                # 诊断：打印子进程退出码（-9 通常是 OOM，-11 是段错误）
                for pid, proc in getattr(_GLOBAL_POOL, '_processes', {}).items():
                    if proc.exitcode is not None and proc.exitcode != 0:
                        print(f"  Child pool PID={pid} terminated unexpectedly，exitcode={proc.exitcode}")

                # 关闭旧池
                try:
                    _GLOBAL_POOL.shutdown(wait=False)
                except Exception:
                    pass

                # 重建进程池
                old_max_workers = getattr(_GLOBAL_POOL, '_max_workers', None) or 2
                try:
                    mp_context = mp.get_context('fork')  # Linux/macOS 推荐
                except ValueError:
                    mp_context = mp.get_context('spawn')  # Windows 必须
                _GLOBAL_POOL = ProcessPoolExecutor(
                    max_workers=old_max_workers,
                    mp_context=mp_context,
                )
                print(f"[Supervisor] Process pool has been rebuilt，max_workers={old_max_workers}")

        # Step 4: Wait for all Theoretician workers to complete
        wait(futures)

        # Every returned attempt, including worker failures, enters the live controller.
        from core.v93_pipeline import NodePipeline
        pipeline = NodePipeline(self)
        completed_nodes = []
        for child_node, future, payload in zip(child_nodes, futures, payloads):
            try:
                output = future.result()
                if not isinstance(output, dict):
                    raise ValueError('Worker returned no structured output')
            except Exception as exc:
                output = {'result': {'execution_error': type(exc).__name__ + ': ' + str(exc),
                                     'delivery_status': 'failed'}, 'tool_calls': []}
            completed_nodes.extend(pipeline.process(child_node, output, payload, subtask))
        child_nodes = completed_nodes

        # Mark parent as expanded so future selection skips it
        if child_nodes:
            self._apply_beam_pruning(parent.get_depth() + 1)
            if parent.status not in {"completed_closed", "failed"}:
                parent.status = "completed_expended"

        return child_nodes

    def _finish_pipeline_node(self, child_node, node_output):
        evaluation = child_node.evaluation
        reward = child_node.reward
        node_log = self.logger.get_node_logger(child_node.node_id) if self.logger else None
        if node_log:
            node_log.log_input(
                subtask_id=child_node.subtask_id,
                node_type=child_node.node_type,
                subtask_description=child_node.subtask_description,
                context=self.tree.get_context_for_node(child_node),
                prior_knowledge=self.prior_knowledge,
            )
            node_log.log_output(result=child_node.result)
            for tc in node_output.get("tool_calls", []):
                node_log.log_tool_call(
                    tool_name=tc.get("tool", ""),
                    arguments=tc.get("arguments", {}),
                    result=tc.get("result", ""),
                )

        self.monitor.node(child_node,"distilling")

        try:
            child_node.knowledge = self._call_promoter(child_node)
        except Exception as exc:
            # Knowledge distillation is auxiliary: retain completed evidence
            # and review if its model request fails.
            child_node.knowledge = '[知识整理失败；请查看节点成果和评审] ' + type(exc).__name__ + ': ' + str(exc)
        if evaluation.get('decision') != 'complete':
            child_node.knowledge = '[UNACCEPTED ATTEMPT — do not reuse as a verified conclusion]\n' + child_node.knowledge
        child_node.is_compressed = True
        child_node.experience = []

        if node_log:
            node_log.log_evaluation(evaluation, reward)
            node_log.log_knowledge(child_node.knowledge)
            node_log.save()

        print(
            f"[Critic] "
            f"(node_id={child_node.node_id} subtask_id={child_node.subtask_id} node_type={child_node.node_type}) "
            f"evaluation completed decision={evaluation.get('decision', '')} reward={reward} 🧪"
        )
        child_node.status = "completed"
        child_node.backpropagate(reward)
        self.monitor.node(child_node,"finished")


    @timed_stage('repair')
    def _execute_repair(self, parent, payload, attempt):
        """Submit a real worker into a fresh node directory; retain failed attempts."""
        global _GLOBAL_POOL
        node_id = self.node_id_counter
        self.node_id_counter += 1
        subtask = dict(parent.subtask_payload)
        node = MCTSNode(subtask_id=parent.subtask_id, subtask_payload=subtask,
            node_id=node_id, node_type='revise', subtask_description=payload['subtask']['description'],
            status='open', created_by='repair_agent')
        node.repair_parent_id = parent.node_id
        node.repair_attempt = attempt
        node.repair_status = 'running'
        node.selected_round = parent.selected_round
        node.supervisor_dispatch = dict(parent.supervisor_dispatch or {})
        node.supervisor_feedback = node.supervisor_dispatch
        from core.research_lifecycle import stage_for
        node.research_stage = stage_for(subtask)
        self.tree.add_node(node)
        parent.add_child(node)
        payload['node_id'] = node_id
        payload['depth'] = node.get_depth()
        self.monitor.node(node, 'solving')
        try:
            try:
                future = _GLOBAL_POOL.submit(run_theo_node, payload, self.config_path)
            except BrokenProcessPool:
                # A killed original worker must not make every subsequent repair fail.
                _GLOBAL_POOL.shutdown(wait=False, cancel_futures=True)
                _GLOBAL_POOL = ProcessPoolExecutor(max_workers=self.processes, mp_context=mp.get_context('spawn'))
                future = _GLOBAL_POOL.submit(run_theo_node, payload, self.config_path)
            output = future.result()
            if not isinstance(output, dict):
                raise ValueError('Repair worker returned no structured output')
        except Exception as exc:
            output = {'result': {'execution_error': type(exc).__name__ + ': ' + str(exc),
                                  'delivery_status': 'failed'}, 'tool_calls': []}
        return node, output

    # Tools
    @timed_stage('integrity')
    def _audit_node_result(self,node,subtask,tools,evaluation):
        audit=audit_node(self.structured_problem,node.result,Path(self.task_dir)/f'node_{node.node_id}',subtask,tools)
        from core.research_lifecycle import audit_stage
        audit = audit_stage(subtask, audit, tools)
        return enforce_audit(evaluation,audit)

    def _get_prior_retriever(self) -> Optional[Any]:
        """Lazy-init the prior retriever. Returns None if unavailable."""
        if PriorRetriever is None:
            if self.landau_prior_enabled:
                print("[Supervisor] Warning: PriorRetriever unavailable (faiss not installed). Disabling prior search.")
                self.landau_prior_enabled = False
            return None
        if self._prior_retriever is None:
            try:
                self._prior_retriever = PriorRetriever()
            except Exception as e:
                print(f"[Supervisor] Warning: PriorRetriever init failed: {e}. Disabling prior search.")
                self.landau_prior_enabled = False
                return None
        return self._prior_retriever

    def _get_library_retriever(self) -> Optional[Any]:
        """Lazy-init the library retriever. Returns None if unavailable."""
        if self._library_retriever is None:
            try:
                self._library_retriever = LibraryRetriever()
            except Exception as e:
                print(f"[Supervisor] Warning: LibraryRetriever init failed: {e}. Disabling library search.")
                self.landau_library_enabled = False
                return None
        return self._library_retriever

    def _prior_search(
        self,
        query: str,
        top_k: int = 3,
        expand_context: bool = False,
        return_format: str = "text",
        source_ids: List[str] | None = None,
        chapter: str | None = None,
        section_prefix: str | None = None,
        keywords: List[str] | None = None,
        rewrite_query: bool = True,
    ):
        """Tool function: search the FAISS-backed prior knowledge base.
        Called by the Supervisor or Critic during their LLM tool loops."""
        retriever = self._get_prior_retriever()
        if retriever is None:
            return "[prior_search] prior knowledge base is not available."
        try:
            results = retriever.retrieve(
                query=query,
                top_k=int(top_k) if top_k is not None else 3,
                expand_context=bool(expand_context),
                source_ids=source_ids,
                chapter=chapter,
                section_prefix=section_prefix,
                keywords=keywords,
                rewrite_query=bool(rewrite_query),
            )
            if return_format == "json":
                return results
            blocks: List[str] = []
            for i, item in enumerate(results, 1):
                source = item.get("source", {}) or {}
                locator = item.get("locator", {}) or {}
                context = item.get("parent_text") or item.get("text", "")
                block_lines = [
                    f"[Prior Reference {i}]",
                    f"title={source.get('title', '')}",
                    f"citation={item.get('citation', '')}",
                    f"chapter:{locator.get('chapter', '')} section:{locator.get('section', '')}",
                    f"content={context}",
                ]
                blocks.append("\n".join(block_lines))
            return "\n\n".join(blocks)
        except Exception as e:
            return f"[prior_search] failed: {e}"

    def _library_search(self, query: str, top_k: int = 5):
        """Tool function: search arXiv, with dedup + critic."""
        self._tool_call_counts["library_search"] += 1
        if self._tool_call_counts["library_search"] > self._tool_budget["library_search"]:
            return ("[library_search] Budget exhausted. Use `web_search` "
                    "for arXiv papers or proceed with existing evidence.")
        print(f"\n[LibrarySearch] >>> query={query!r} top_k={top_k}")
        retriever = self._get_library_retriever()
        if retriever is None:
            return "[library_search] arXiv search is not available."

        # ① query 去重（加 lib:: 前缀，避免和 web 撞车）
        norm_q = self._normalize_query(query)
        dedup_key = "lib::" + norm_q
        if dedup_key in self._seen_queries:
            print(f"[LibrarySearch] duplicate query, skipping: {query!r}")
            return (f"[library_search] This arXiv query has already been "
                    f"searched. Do NOT repeat it. Try different terms.")

        try:
            raw = retriever.search(query=query, top_k=int(top_k) * 2)

            # ③ URL 去重
            fresh = []
            for r in raw:
                url = (r.get("link") or r.get("pdf_url") or "").strip()
                if url and url in self._seen_urls:
                    continue
                fresh.append(r)
            print(f"[LibrarySearch] after url-dedup: {len(fresh)} fresh")

            # ④ 质量过滤
            if self.retrieval_critic_enabled and fresh:
                fresh = self._get_retrieval_critic().filter(query, fresh)
                print(f"[LibrarySearch] after critic: {len(fresh)} kept")

            self._seen_queries.add(dedup_key)

            if not fresh:
                return (f"[library_search] No new arXiv results for '{query}'. "
                        f"All hits were already reviewed or filtered out. "
                        f"Proceed with existing evidence or rephrase.")

            results = fresh[:int(top_k)]
            for r in results:
                url = (r.get("link") or r.get("pdf_url") or "").strip()
                if url:
                    self._seen_urls.add(url)

            return retriever.format_for_llm(results)
        except Exception as e:
            print(f"[LibrarySearch] !!! exception: {e}")
            return f"[library_search] failed: {e}"

    @staticmethod
    def _normalize_query(q: str) -> str:
        """Lowercase, collapse whitespace, strip punctuation for dedup."""
        import re
        return re.sub(r"\s+", " ", (q or "").strip().lower())

    def _get_retrieval_critic(self):
        """Lazy-init RetrievalCritic."""
        if self._retrieval_critic is None:
            from core.retrieval_critic import RetrievalCritic
            self._retrieval_critic = RetrievalCritic(
                config_path=self.config_path,
                threshold=float(self.retrieval_critic_cfg.get("threshold", 0.5)),
            )
        return self._retrieval_critic

    
    def _web_search(self, query: str, top_k: int = 3) -> str:
        """Tool function: general web search via Tavily, with dedup + critic."""
        self._tool_call_counts["web_search"] += 1
        if self._tool_call_counts["web_search"] > self._tool_budget["web_search"]:
            return ("[web_search] Budget exhausted. Use `library_search` "
                    "for arXiv papers or proceed with existing evidence.")
        print(f"\n[WebSearch] >>> query={query!r} top_k={top_k}")
        if not self.web_enabled:
            return "[web_search] not enabled."

        # ① query 去重
        norm_q = self._normalize_query(query)
        if norm_q in self._seen_queries:
            print(f"[WebSearch] duplicate query, skipping: {query!r}")
            return (f"[web_search] This query has already been searched in a "
                    f"previous step. Do NOT repeat it. Try a substantially "
                    f"different phrasing or move to another subtask.")

        try:
            if self._web_searcher is None:
                from tools.web_search import WebSearch
                self._web_searcher = WebSearch(self.tools_cfg)

            raw = self._web_searcher.search(query, k=int(top_k) * 2)
            print(f"[WebSearch] <<< got {len(raw)} raw results")

            # ③ URL 去重
            fresh = []
            for r in raw:
                url = (r.get("url") or "").strip()
                if url and url in self._seen_urls:
                    continue
                fresh.append(r)
            print(f"[WebSearch] after url-dedup: {len(fresh)} fresh")

            # ④ 质量过滤
            if self.retrieval_critic_enabled and fresh:
                fresh = self._get_retrieval_critic().filter(query, fresh)
                print(f"[WebSearch] after critic: {len(fresh)} kept")

            # 无论结果如何，这个 query 已搜过
            self._seen_queries.add(norm_q)

            if not fresh:
                print(f"[WebSearch] no fresh results; returning notice")
                return (f"[web_search] No new results. STOP searching — this query "
                        f"and similar ones have been exhausted. Use the evidence you "
                        f"already have from previous steps and produce your answer NOW.")

            results = fresh[:int(top_k)]
            for r in results:
                url = (r.get("url") or "").strip()
                if url:
                    self._seen_urls.add(url)

            lines = []
            for i, r in enumerate(results, 1):
                lines.append(
                    f"[{i}] {r.get('title', '')}\n"
                    f"    URL: {r.get('url', '')}\n"
                    f"    {r.get('snippet', '')[:300]}"
                )
            out = "\n\n".join(lines)
            print(f"[WebSearch] returning {len(out)} chars to LLM")
            return out
        except Exception as e:
            print(f"[WebSearch] !!! exception: {e}")
            return f"[web_search] failed: {e}"


    def _codata_lookup(self, name: str) -> str:
        """Tool function: query CODATA constants from local table."""
        if not self.codata_enabled:
            return "[codata_lookup] not enabled."
        try:
            if self._codata is None:
                from tools.codata import Codata
                self._codata = Codata()
            entry = self._codata.lookup(name)
            if entry is None:
                return f"[codata_lookup] '{name}' not found in CODATA table."
            return (
                f"{entry['name']} = {entry['value']} {entry.get('unit','')} "
                f"(uncertainty: {entry.get('uncertainty', 'N/A')})"
            )
        except Exception as e:
            return f"[codata_lookup] failed: {e}"

    def _log_tool_call(self, agent_label: str, node: MCTSNode, tool_name: str):
        print(
            f"[{agent_label}] "
            f"(node_id={node.node_id} subtask_id={node.subtask_id} node_type={node.node_type}) "
            f"tool call {tool_name} 🛠️"
        )

    def _kb_tool_functions(self, agent_label: str, node: MCTSNode) -> Dict[str, Any]:
        """Build a dict of callable tool functions for library/prior search,
        wired with logging for the given agent and node."""
        functions: Dict[str, Any] = {}
        if self.landau_library_enabled:
            functions["library_search"] = lambda **kwargs: (
                self._log_tool_call(agent_label, node, "library_search"),
                self._library_search(**kwargs),
            )[1]
        if self.landau_prior_enabled:
            functions["prior_search"] = lambda **kwargs: (
                self._log_tool_call(agent_label, node, "prior_search"),
                self._prior_search(**kwargs),
            )[1]
        if self.web_enabled:
            functions["web_search"] = lambda **kwargs: (
                self._log_tool_call(agent_label, node, "web_search"),
                self._web_search(**kwargs),
            )[1]
        if self.codata_enabled:
            functions["codata_lookup"] = lambda **kwargs: (
                self._log_tool_call(agent_label, node, "codata_lookup"),
                self._codata_lookup(**kwargs),
            )[1]
        return functions

    # --- Node selection / pruning ---

    def _select_leaf_node(self) -> Optional[MCTSNode]:
        """UCB1-based leaf selection across all open nodes."""
        if not self.tree.root.children:
            return self.tree.root

        candidates = [
            node
            for node in self.tree.get_all_nodes()
            if node.node_type != "virtual" and node.is_leaf()
            and node.status not in {"completed_closed", "completed_expended", "failed"}
        ]
        if not candidates:
            return None

        # Preserve a coherent accepted prefix before exploring equivalent attempts.
        # Scores alone cannot turn an unresolved prerequisite into progress.
        progress = {n.node_id: self._accepted_prefix_length(self._get_path_nodes(n)) for n in candidates}
        frontier = max(progress.values())
        candidates = [n for n in candidates if progress[n.node_id] == frontier]

        def ucb(node: MCTSNode) -> float:
            if node.visits <= 0:
                return float("inf")
            parent_visits = node.parent.visits if node.parent else self.tree.root.visits
            parent_visits = max(1, int(parent_visits))
            exploit = node.average_reward
            explore = self.exploration_constant * math.sqrt(math.log(parent_visits + 1) / node.visits)
            return exploit + explore

        selected = max(
            candidates,
            key=lambda n: (ucb(n), n.get_depth(), -n.node_id),
        )
        return selected

    def _accepted_prefix_length(self, path_nodes):
        _, completed = self._count_completed_subtasks_in_path(path_nodes)
        count = 0
        for subtask in self.subtasks:
            if int(subtask['id']) not in completed:
                break
            count += 1
        return count

    def _apply_beam_pruning(self, depth: int):
        """Close low-reward nodes at the given depth when they exceed
        the active beam width. Keeps only the top-k by reward."""
        if self.active_beam_width <= 0:
            return

        candidates = [
            n
            for n in self.tree.get_all_nodes()
            if n.node_type != "virtual"
            and n.status not in {"completed_closed", "failed"}
            and n.get_depth() == depth
        ]
        if len(candidates) <= self.active_beam_width:
            return

        ranked = sorted(
            candidates,
            key=lambda n: (
                float(n.reward or 0.0),
                float(n.get_reward_value()),
                int(n.visits),
                -n.node_id,
            ),
            reverse=True,
        )
        keep = {n.node_id for n in ranked[: self.active_beam_width]}
        for node in candidates:
            if node.node_id not in keep:
                node.status = "completed_closed"

    # --- Subtask normalization ---

    def _build_subtasks(self) -> List[Dict[str, Any]]:
        """Normalize the sub-tasks from the structured problem into a
        consistent list with sequential integer IDs."""
        subtasks_payload = (
            self.structured_problem.get("sub-tasks")
            or self.structured_problem.get("sub_tasks")
            or self.structured_problem.get("subtasks")
            or []
        )
        if isinstance(subtasks_payload, dict):
            subtasks_payload = list(subtasks_payload.values())
        elif isinstance(subtasks_payload, str):
            subtasks_payload = [subtasks_payload]
        elif not isinstance(subtasks_payload, list):
            subtasks_payload = []

        if not subtasks_payload:
            subtasks_payload = [
                {
                    "id": 1,
                    "description": self.structured_problem.get("task_description", ""),
                    "subtask_type": "reasoning",
                    "input": self.structured_problem.get("input", ""),
                    "expected_output": self.structured_problem.get("expected_output", ""),
                }
            ]

        normalized: List[Dict[str, Any]] = []
        for item in subtasks_payload:
            if isinstance(item, dict):
                description = str(
                    item.get("description")
                    or item.get("objective")
                    or item.get("task")
                    or item.get("name")
                    or ""
                ).strip()
                sid = self._to_int(item.get("id"))
                normalized.append(
                    {
                        "id": sid,
                        "subtask_type": str(item.get("subtask_type", "reasoning")).strip() or "reasoning",
                        "research_stage": item.get("research_stage"),
                        "input": item.get("input", self.structured_problem.get("input", "")),
                        "expected_output": item.get("expected_output", ""),
                        "description": description,
                    }
                )
            else:
                text = str(item).strip()
                normalized.append(
                    {
                        "id": None,
                        "subtask_type": "reasoning",
                        "input": self.structured_problem.get("input", ""),
                        "expected_output": self.structured_problem.get("expected_output", ""),
                        "description": text,
                    }
                )

        used_ids = set()
        next_id = 1
        for item in normalized:
            sid = item.get("id")
            if sid is None or sid in used_ids:
                while next_id in used_ids:
                    next_id += 1
                sid = next_id
            used_ids.add(sid)
            next_id = max(next_id, sid + 1)
            item["id"] = sid
            if not str(item.get("description", "")).strip():
                item["description"] = f"Subtask {sid}"

        normalized.sort(key=lambda x: int(x.get("id", 0)))
        return normalized

    def _default_next_subtask_id(self, node, decision):
        if not self.subtasks:
            return None
        if node.node_type == "virtual":
            return int(self.subtasks[0]["id"])

        current_subtask_id = int(node.subtask_id)
        is_complete = node.is_subtask_complete()

        if is_complete:
            next_subtask = self._get_next_subtask(current_subtask_id)
            return int(next_subtask["id"]) if next_subtask else None
        return current_subtask_id


    def _count_failures_for_subtask(self, sid):
        """返回该子任务下所有节点里，decision=to_revise/to_redraft 的累计数。"""
        count = 0
        for n in self.tree.get_all_nodes():
            if int(n.subtask_id) != int(sid):
                continue
            ev = n.evaluation or {}
            if ev.get("decision") in ("to_revise", "to_redraft"):
                count += 1
        return count

    def _extract_requested_subtask_id(self, payload: Dict[str, Any], fallback: int) -> int:
        """Try to read the Supervisor's preferred subtask_id from its JSON
        output. Fall back to the default if not present or invalid."""
        sid = self._to_int(payload.get("subtask_id"))
        if sid is not None and self._get_subtask_by_id(sid) is not None:
            return sid

        subtask_obj = payload.get("subtask")
        if isinstance(subtask_obj, dict):
            sid = self._to_int(subtask_obj.get("id"))
            if sid is not None and self._get_subtask_by_id(sid) is not None:
                return sid
        return fallback

    def _get_subtask_by_id(self, subtask_id: int) -> Optional[Dict[str, Any]]:
        for subtask in self.subtasks:
            if int(subtask.get("id", -1)) == int(subtask_id):
                return subtask
        return None

    def _get_next_subtask(self, current_subtask_id: int) -> Optional[Dict[str, Any]]:
        for idx, subtask in enumerate(self.subtasks):
            if int(subtask.get("id", -1)) == int(current_subtask_id):
                next_idx = idx + 1
                return self.subtasks[next_idx] if next_idx < len(self.subtasks) else None
        return None

    # --- Best trajectory extraction ---

    def _get_path_nodes(self, node: MCTSNode) -> List[MCTSNode]:
        path: List[MCTSNode] = []
        current: Optional[MCTSNode] = node
        while current is not None:
            path.append(current)
            current = current.parent
        path.reverse()
        return path

    def _path_reward_sum(self, path_nodes: List[MCTSNode]) -> float:
        return float(sum(float(n.reward or 0.0) for n in path_nodes if n.node_type != "virtual"))

    def _count_completed_subtasks_in_path(self, path_nodes: List[MCTSNode]) -> Tuple[int, set[int]]:
        latest = {}
        order={int(s['id']):i for i,s in enumerate(self.subtasks)}
        for node in path_nodes:
            if node.node_type == "virtual":
                continue
            if int(node.subtask_id) not in order:continue
            if int(node.subtask_id) in order:
                latest={sid:n for sid,n in latest.items() if order[sid]<=order[int(node.subtask_id)]}
            latest[int(node.subtask_id)] = node
        expected = {int(s['id']) for s in self.subtasks}
        completed = {sid for sid,node in latest.items() if sid in expected and node.is_subtask_complete()
                     and (node.evaluation or {}).get('integrity_audit',{}).get('status')=='pass'}
        return len(completed), completed

    def _find_full_completion_path(self) -> Optional[List[MCTSNode]]:
        """Return a root-to-leaf path that completes ALL subtasks, or None."""
        total_subtasks = len(self.subtasks)
        if total_subtasks <= 0:
            return None

        candidates: List[List[MCTSNode]] = []
        for node in self.tree.get_all_nodes():
            if node.node_type == "virtual" or node.visits <= 0:
                continue
            path = self._get_path_nodes(node)
            completed_count, _ = self._count_completed_subtasks_in_path(path)
            if completed_count >= total_subtasks:
                candidates.append(path)

        if not candidates:
            return None
        return max(
            candidates,
            key=lambda path: (
                self._path_reward_sum(path),
                len(path),
                path[-1].visits if path else 0,
                path[-1].node_id if path else -1,
            ),
        )

    def _resolve_best_path(self) -> List[MCTSNode]:
        """Fallback when no path completes all subtasks. Picks the path
        that completed the most subtasks, breaking ties by total reward."""
        candidates = [n for n in self.tree.get_all_nodes() if n.node_type != "virtual" and n.visits > 0]
        if not candidates:
            return [self.tree.root]

        return max(
            (self._get_path_nodes(node) for node in candidates),
            key=lambda path: (
                self._count_completed_subtasks_in_path(path)[0],
                self._path_reward_sum(path),
                len(path),
                path[-1].get_reward_value() if path else 0.0,
                path[-1].node_id if path else -1,
            ),
        )

    def _find_best_path_nodes(self) -> List[MCTSNode]:
        """First try to find a path that completes all subtasks; if none
        exists, fall back to the best partial path."""
        full = self._find_full_completion_path()
        if full is not None:
            return full
        return self._resolve_best_path()

    def _find_best_trajectory(self) -> List[Dict[str, Any]]:
        """Backward-compatible alias: returns serialized best trajectory."""
        return self._serialize_trajectory(self._find_best_path_nodes())

    def _serialize_trajectory(self, path_nodes: List[MCTSNode]) -> List[Dict[str, Any]]:
        """Convert a list of path nodes into a JSON-serializable trajectory,
        skipping the virtual root."""
        if not path_nodes:
            return []
        return [
            {
                "node_id": node.node_id,
                "depth": node.get_depth(),
                "subtask_id": node.subtask_id,
                "node_type": node.node_type,
                "subtask": node.subtask_payload,
                "description": node.subtask_description,
                "result": node.result,
                "reward": node.reward,
                "visits": node.visits,
                "memory": node.knowledge,
                "log_path": node.log_path,
                "supervisor_dispatch": node.supervisor_dispatch,
                "critic_feedback": node.evaluation,
                "research_stage": getattr(node, "research_stage", None),
                "repair_parent_id": getattr(node, "repair_parent_id", None),
                "repair_attempt": getattr(node, "repair_attempt", 0),
                "repair_status": getattr(node, "repair_status", None),
                "theoretician_output": node.theoretician_output,
            }
            for node in path_nodes
            if node.node_type != "virtual"
        ]

    def _collect_completed_subtasks(self) -> List[Dict[str, Any]]:
        """Gather the best completed node per subtask across the entire tree."""
        best_by_subtask: Dict[int, MCTSNode] = {}
        path=self._find_best_path_nodes()
        _,completed_ids=self._count_completed_subtasks_in_path(path)
        for node in path:
            if node.node_type == "virtual" or not node.is_subtask_complete():
                continue
            sid = int(node.subtask_id)
            if sid not in completed_ids:continue
            prev = best_by_subtask.get(sid)
            if prev is None:
                best_by_subtask[sid] = node
                continue
            best_by_subtask[sid] = node

        completed: List[Dict[str, Any]] = []
        for sid in sorted(best_by_subtask.keys()):
            node = best_by_subtask[sid]
            completed.append(
                {
                    "subtask_id": node.subtask_id,
                    "description": node.subtask_description,
                    "result": node.result,
                    "reward": node.reward,
                    "log_path": node.log_path,
                }
            )
        return completed

    # --- Visualization export ---

    def serialize_nodes_for_visualization(self) -> List[Dict[str, Any]]:
        """Dump all tree nodes into plain dicts for the HTML visualization."""
        payload: List[Dict[str, Any]] = []
        for node in self.tree.get_all_nodes():
            payload.append(
                {
                    "node_id": node.node_id,
                    "parent_id": node.parent.node_id if node.parent else None,
                    "children": [c.node_id for c in node.children],
                    "depth": node.get_depth(),
                    "node_type": node.node_type,
                    "subtask": node.subtask_payload
                    if node.subtask_payload is not None
                    else {
                        "id": node.subtask_id if node.subtask_id > 0 else None,
                        "description": node.subtask_description,
                    },
                    "description": node.subtask_description,
                    "reward": node.reward,
                    "visits": node.visits,
                    "status": node.status,
                    "created_by": node.created_by,
                    "memory": node.knowledge,
                    "theoretician_output": node.theoretician_output,
                    "supervisor_dispatch": node.supervisor_dispatch or {},
                    "critic_feedback": node.evaluation or {},
                    "supervisor_feedback": node.supervisor_feedback or {},
                    "selected_round": node.selected_round,
                }
            )
        return payload

    # --- Helpers ---

    def _get_safe_name(self) -> str:
        instr_name = (
            self.structured_problem.get("instruction_filename")
            or self.structured_problem.get("topic")
        )
        try:
            instr_stem = Path(str(instr_name)).stem
        except Exception:
            instr_stem = str(instr_name)
        return "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(instr_stem))

    def _get_expansion_count_by_node_type(self, node_type: str) -> int:
        """Draft nodes expand by draft_expansion count, revise nodes
        by revise_expansion count."""
        ntype = (node_type or "").lower()
        if ntype == "draft":
            return self.draft_expansion
        if ntype == "revise":
            return self.revise_expansion
        return self.revise_expansion

    def _decision_to_node_type(self, decision: str) -> str:
        """Map critic decision string to a node type.
        to_redraft/complete -> draft, to_revise -> revise."""
        d = (decision or "").lower()
        if d in ("to_redraft", "complete"):
            return "draft"
        if d == "to_revise":
            return "revise"
        return "revise"

    def _sanitize_node_type(self, node_type: Any, fallback: str) -> str:
        ntype = str(node_type or fallback or "draft").strip().lower()
        if ntype not in {"draft", "revise"}:
            return fallback if fallback in {"draft", "revise"} else "revise"
        return ntype

    def _extract_reward(self, payload: Dict[str, Any]) -> float:
        if not isinstance(payload, dict):
            return 0.0
        value = payload.get("reward")
        try:
            return float(value) if value is not None else 0.0
        except Exception:
            return 0.0

    def _subtask_brief(self, subtask: Any) -> str:
        if isinstance(subtask, dict):
            sid = subtask.get("id")
            stype = subtask.get("subtask_type")
            desc = str(subtask.get("description") or "").strip()
            bits = []
            if sid is not None:
                bits.append(f"#{sid}")
            if stype:
                bits.append(str(stype))
            if desc:
                bits.append(desc)
            return " | ".join(bits)
        return str(subtask or "").strip()

    def _to_natural_text(self, value: Any) -> str:
        """Recursively flatten nested dicts/lists into a readable string."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return " ".join(self._to_natural_text(v) for v in value if self._to_natural_text(v)).strip()
        if isinstance(value, dict):
            parts = []
            for k, v in value.items():
                t = self._to_natural_text(v)
                if t:
                    parts.append(f"{k}: {t}")
            return "; ".join(parts).strip()
        return str(value).strip()

    def _extract_json_object(self, text: Any) -> Any:
        """Best-effort JSON extraction from LLM text. Tries raw parse,
        then fenced code blocks, then first/last brace or bracket."""
        if text is None:
            return {}
        if isinstance(text, (dict, list)):
            return text

        content = str(text).strip()
        if not content:
            return {}

        try:
            return json.loads(content)
        except Exception:
            pass

        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", content)
        for block in blocks:
            try:
                return json.loads(block.strip())
            except Exception:
                continue

        left = content.find("{")
        right = content.rfind("}")
        if left != -1 and right != -1 and right > left:
            try:
                return json.loads(content[left : right + 1])
            except Exception:
                pass

        left = content.find("[")
        right = content.rfind("]")
        if left != -1 and right != -1 and right > left:
            try:
                return json.loads(content[left : right + 1])
            except Exception:
                pass

        return {}

    def _to_int(self, value: Any) -> Optional[int]:
        try:
            if value is None or value == "":
                return None
            return int(value)
        except Exception:
            return None
