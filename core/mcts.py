"""MCTS tree structures and node utilities."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MCTSNode:
    """Single node in the MCTS search tree. Each node represents one
    attempt at solving a subtask, either as a fresh draft or a revision."""

    subtask_id: int
    node_id: int
    node_type: str  # "virtual" | "draft" | "revise"
    subtask_description: str

    subtask_payload: Optional[Dict[str, Any]] = None
    created_by: str = "supervisor"

    # "open" | "completed" | "completed_expended" | "completed_closed" | "failed"
    status: str = "open"

    # MCTS statistics
    visits: int = 0
    total_reward: float = 0.0
    average_reward: float = 0.0
    reward: float = 0.0          # latest reward from Critic

    # Tree links
    parent: Optional["MCTSNode"] = None
    children: List["MCTSNode"] = field(default_factory=list)

    # Node artifacts produced during the solve
    result: Optional[Any] = None               # Theoretician output
    evaluation: Optional[Dict[str, Any]] = None  # Critic evaluation
    supervisor_dispatch: Optional[Dict[str, Any]] = None
    supervisor_feedback: Optional[Dict[str, Any]] = None
    theoretician_output: Optional[Any] = None

    # Memory fields: raw experience and distilled knowledge
    experience: List[Dict[str, Any]] = field(default_factory=list)
    knowledge: str = ""
    is_compressed: bool = False  # True after experience is distilled into knowledge

    selected_round: Optional[int] = None
    log_path: Optional[str] = None

    def __post_init__(self):
        if self.node_id is None:
            self.node_id = 0

    def is_leaf(self) -> bool:
        """A node with no children is a leaf in the tree."""
        return len(self.children) == 0

    def is_fully_expanded(self) -> bool:
        """Closed or failed nodes cannot be expanded further."""
        # This project controls expansion in supervisor; keep semantic helper.
        return self.status in {"completed_closed", "failed"}

    def get_reward_value(self) -> float:
        """Return the best available reward: prefer the critic evaluation's
        reward, then the node-level reward, then the running average."""
        if self.evaluation and self.evaluation.get("reward") is not None:
            try:
                return float(self.evaluation["reward"])
            except Exception:
                pass
        if self.reward is not None:
            return float(self.reward)
        return float(self.average_reward or 0.0)

    def get_ucb1_value(self, exploration_constant: float = 1.414) -> float:
        """Compute UCB1 value for child selection."""
        if self.visits <= 0:
            return float("inf")

        if self.parent is None:
            return self.get_reward_value()

        parent_visits = max(1, int(self.parent.visits))
        exploitation = self.average_reward
        exploration = exploration_constant * math.sqrt(
            math.log(parent_visits + 1) / self.visits
        )
        return exploitation + exploration

    def select_best_child(self, exploration_constant: float = 1.414) -> Optional["MCTSNode"]:
        """Use UCB1 to select best child while skipping closed/failed nodes."""
        if not self.children:
            return None

        candidates = [c for c in self.children if c.status not in {"completed_closed", "failed"}]
        if not candidates:
            return None

        return max(candidates, key=lambda c: c.get_ucb1_value(exploration_constant))

    def add_child(self, child: "MCTSNode"):
        child.parent = self
        self.children.append(child)

    def update_stats(self, reward: float):
        """Increment visit count and update the running average reward."""
        self.visits += 1
        self.total_reward += reward
        self.average_reward = self.total_reward / self.visits

    def backpropagate(self, reward: float):
        """Walk up to the root, incrementing visit counts and accumulating
        reward. When reward >= 0.8, also apply cognitive reinforcement
        to ancestors so high-quality knowledge propagates upward."""
        current: Optional["MCTSNode"] = self
        while current is not None:
            current.update_stats(reward)
            if reward >= 0.8 and current != self:
                self._apply_cognitive_reinforcement(source=self, target=current)
            current = current.parent

    def _apply_cognitive_reinforcement(self, source: "MCTSNode", target: "MCTSNode"):
        """Tag ancestor nodes with verified knowledge from a high-reward
        descendant. Only the original knowledge (not nested verified blocks)
        is propagated to prevent exponential growth."""
        if not source.knowledge:
            return

        # Extract only the original knowledge, strip out any [Verified by ...] blocks
        original_knowledge = self._extract_original_knowledge(source.knowledge)
        if not original_knowledge.strip():
            return

        verification_tag = f"## Verified Knowledge from Node {source.node_id}"

        if verification_tag not in (target.knowledge or ""):
            new_insight = f"\n{verification_tag}\n{original_knowledge}"

            if not target.knowledge:
                target.knowledge = new_insight.strip()
            else:
                combined = target.knowledge + new_insight
                # Cap at 5000 chars to prevent unbounded growth
                if len(combined) > 5000:
                    target.knowledge = combined[-5000:]
                else:
                    target.knowledge = combined

            target.is_compressed = True

    def _extract_original_knowledge(self, knowledge: str) -> str:
        """Extract the original knowledge portion, removing any nested
        [Verified by ...] or ## Verified Knowledge blocks."""
        if not knowledge:
            return ""

        lines = knowledge.split('\n')
        result = []
        skip_block = False

        for line in lines:
            # Skip old-style [Verified by Node X] blocks
            if line.strip().startswith('[Verified by Node'):
                skip_block = True
                continue
            # Skip new-style ## Verified Knowledge blocks
            if line.strip().startswith('## Verified Knowledge from Node'):
                skip_block = True
                continue
            # End of a verified block when we hit another section marker or empty line after content
            if skip_block and (line.strip().startswith('##') or line.strip().startswith('RESULTS') or line.strip().startswith('INSIGHTS')):
                skip_block = False

            if not skip_block:
                result.append(line)

        return '\n'.join(result).strip()

    def get_depth(self) -> int:
        """Count the number of edges from this node up to the root."""
        depth = 0
        current = self
        while current.parent is not None:
            depth += 1
            current = current.parent
        return depth

    def is_subtask_complete(self) -> bool:
        """Check whether the critic judged this subtask as complete.
        Looks for decision=='complete' or verdict=='accept'."""
        feedback = self.evaluation or {}
        decision = str(feedback.get("decision", "")).strip().lower()
        verdict = str(feedback.get("verdict", "")).strip().lower()
        return decision == "complete" or verdict == "accept"

    def node_id_number(self) -> int:
        return int(self.node_id)

    def to_dict(self) -> Dict[str, Any]:
        """Compact serialization for logging and debugging."""
        return {
            "node_id": self.node_id,
            "subtask_id": self.subtask_id,
            "node_type": self.node_type,
            "status": self.status,
            "visits": self.visits,
            "reward": self.reward,
            "average_reward": self.average_reward,
            "l2_knowledge": self.knowledge,
            "l1_size": len(self.experience),
            "children_count": len(self.children),
            "has_result": self.result is not None,
        }


class MCTSTree:
    """Container for the full MCTS search tree. Tracks all nodes by ID
    and maintains subtask root references."""

    def __init__(self, root_subtask_id: int, root_description: str, prior_knowlege=None):
        self.root = MCTSNode(
            subtask_id=root_subtask_id,
            node_id=0,
            node_type="virtual",
            subtask_description=root_description,
            status="completed_expended",
            created_by="root",
        )
        self.nodes: Dict[int, MCTSNode] = {self.root.node_id: self.root}
        self.subtask_roots: Dict[int, MCTSNode] = {root_subtask_id: self.root}

        self.prior_knowlege=prior_knowlege

    def get_node(self, node_id: int) -> Optional[MCTSNode]:
        return self.nodes.get(node_id)

    def get_node_by_id(self, node_id: int) -> Optional[MCTSNode]:
        return self.nodes.get(node_id)

    def add_node(self, node: MCTSNode):
        """Register a node in the tree. First node for a given subtask_id
        also becomes that subtask's root reference."""
        self.nodes[node.node_id] = node
        if node.subtask_id not in self.subtask_roots:
            self.subtask_roots[node.subtask_id] = node

    def _get_best_path(self) -> List[MCTSNode]:
        """Greedy walk: always pick the child with the highest average reward."""
        path = []
        curr = self.root
        while curr.children:
            curr = max(curr.children, key=lambda c: c.average_reward)
            path.append(curr)
        return path


    # Maximum context chars sent to the LLM (peers + ancestors + target combined)
    CONTEXT_CHAR_LIMIT = 16000

    def get_context_for_node(self, node: MCTSNode) -> str:
        """Assemble context for the Supervisor/Theoretician by combining:
        1. Peer insights from sibling branches working on the same subtask
        2. Ancestor knowledge summaries along the path from root
           - Current subtask ancestors: full knowledge
           - Prior subtask ancestors: only the last node per subtask, condensed
        3. Raw experience for the target node itself

        Deduplicates verified knowledge blocks across ancestors, and
        truncates the final result to CONTEXT_CHAR_LIMIT."""
        path = []
        curr = node
        while curr:
            path.insert(0, curr)
            curr = curr.parent

        hcc_segments = []

        # 1. Peer insights (same subtask, not in path)
        peers = [
            n for n in self.get_all_nodes()
            if n.subtask_id == node.subtask_id and n.node_id != node.node_id and n not in path
        ]

        peer_insights = []
        for p_node in peers:
            if p_node.is_compressed and p_node.knowledge:
                status = "SUCCESS" if p_node.reward > 0.8 else "FAILED/LIMITATION"
                # Only include original knowledge in peer summaries
                original = p_node._extract_original_knowledge(p_node.knowledge)
                if original.strip():
                    # Truncate each peer insight to 800 chars to prevent context explosion
                    truncated = original[:800] + "..." if len(original) > 800 else original
                    peer_insights.append(f"- Peer Node {p_node.node_id} ({status}): {truncated}")

        if peer_insights:
            hcc_segments.append("### [Peer Insights (Parallel Branches)]\n" + "\n".join(peer_insights[:3]))

        # 2. Ancestor knowledge with layered detail
        # Track which verified blocks have been included globally
        seen_verified = set()

        # Group ancestors by subtask
        current_subtask_id = node.subtask_id
        prior_subtasks = {}  # subtask_id -> last node in path for that subtask
        current_subtask_ancestors = []

        for path_node in path:
            if path_node.node_id == node.node_id:
                continue  # target node handled separately
            if path_node.node_type == "virtual":
                continue  # skip virtual root
            if path_node.subtask_id == current_subtask_id:
                current_subtask_ancestors.append(path_node)
            else:
                # Prior subtask: keep only the last (most refined) node
                prior_subtasks[path_node.subtask_id] = path_node

        hcc_segments.append("### [Ancestry & Current Node Details]")

        # 2a. Prior subtasks: condensed knowledge (RESULTS + INSIGHTS only)
        if prior_subtasks:
            prior_lines = []
            for sid in sorted(prior_subtasks.keys()):
                pnode = prior_subtasks[sid]
                condensed = self._condense_knowledge(pnode.knowledge)
                if condensed.strip():
                    prior_lines.append(f"-> Prior Subtask {sid} (Node {pnode.node_id}): {condensed}")
            if prior_lines:
                hcc_segments.append("\n".join(prior_lines))

        # 2b. Current subtask ancestors: full knowledge with deduplication
        for path_node in current_subtask_ancestors:
            knowledge = path_node.knowledge or ""
            if knowledge:
                deduped = self._deduplicate_knowledge(knowledge, seen_verified)
            else:
                deduped = f"Subtask: {path_node.subtask_description}"
            hcc_segments.append(f"-> Ancestor Node {path_node.node_id}: {deduped}")

        # 2c. Target node: knowledge only (experience is for Critic/Promoter, not for context)
        if node.knowledge:
            deduped = self._deduplicate_knowledge(node.knowledge, seen_verified)
            hcc_segments.append(f">> Target Node {node.node_id} (Active, compressed):\n{deduped}")
        else:
            hcc_segments.append(f">> Target Node {node.node_id} (Active): No prior context available.")

        result = "\n\n".join(hcc_segments)

        # Hard cap: if still too long, truncate from the middle, keeping
        # the beginning (peers + first ancestors) and end (target node)
        if len(result) > self.CONTEXT_CHAR_LIMIT:
            keep_head = self.CONTEXT_CHAR_LIMIT * 2 // 3
            keep_tail = self.CONTEXT_CHAR_LIMIT - keep_head - 50
            result = (
                result[:keep_head]
                + "\n\n... [context truncated] ...\n\n"
                + result[-keep_tail:]
            )

        return result

    def _condense_knowledge(self, knowledge: str) -> str:
        """Extract only RESULTS and INSIGHTS sections from knowledge,
        dropping GUIDANCE and verified blocks. Used for prior subtasks."""
        if not knowledge:
            return ""

        lines = knowledge.split('\n')
        result = []
        in_results = False
        in_insights = False
        skip_verified = False

        for line in lines:
            stripped = line.strip()

            # Skip verified blocks entirely
            if stripped.startswith('## Verified Knowledge') or stripped.startswith('[Verified by Node'):
                skip_verified = True
                continue
            if skip_verified and (stripped.startswith('RESULTS') or stripped.startswith('INSIGHTS') or stripped.startswith('GUIDANCE')):
                skip_verified = False

            if skip_verified:
                continue

            # Track sections
            if stripped.startswith('RESULTS'):
                in_results = True
                in_insights = False
                result.append(line)
            elif stripped.startswith('INSIGHTS'):
                in_results = False
                in_insights = True
                result.append(line)
            elif stripped.startswith('GUIDANCE'):
                in_results = False
                in_insights = False
                # Don't include GUIDANCE for prior subtasks
            elif in_results or in_insights:
                result.append(line)

        condensed = '\n'.join(result).strip()

        # Fallback: if no section markers found, return first 600 chars
        if not condensed and knowledge.strip():
            return knowledge.strip()[:600] + ("..." if len(knowledge.strip()) > 600 else "")

        return condensed

    def _deduplicate_knowledge(self, knowledge: str, seen: set) -> str:
        """Remove ## Verified Knowledge blocks that have already been
        included in a previous ancestor. Updates `seen` in place."""
        if not knowledge:
            return ""

        lines = knowledge.split('\n')
        result = []
        current_block_tag = None
        skip_current_block = False

        for line in lines:
            # Detect start of a verified block
            stripped = line.strip()
            if stripped.startswith('## Verified Knowledge from Node'):
                current_block_tag = stripped
                if current_block_tag in seen:
                    skip_current_block = True
                else:
                    seen.add(current_block_tag)
                    skip_current_block = False
                    result.append(line)
                continue

            # Detect old-style [Verified by Node X] blocks
            if stripped.startswith('[Verified by Node'):
                current_block_tag = stripped.split(']')[0] + ']'
                if current_block_tag in seen:
                    skip_current_block = True
                else:
                    seen.add(current_block_tag)
                    skip_current_block = False
                    result.append(line)
                continue

            # Detect end of verified block: another heading or top-level section
            if current_block_tag and (stripped.startswith('## ') and not stripped.startswith('## Verified')):
                current_block_tag = None
                skip_current_block = False
                result.append(line)
                continue

            if not skip_current_block:
                result.append(line)

        return '\n'.join(result).strip()

    def selection(self, exploration_constant: float = 1.414) -> MCTSNode:
        """Selection phase: walk from root with UCB1 until leaf."""
        current = self.root
        visited = set()
        while current.children:
            if current.node_id in visited:
                break
            visited.add(current.node_id)
            nxt = current.select_best_child(exploration_constant)
            if nxt is None:
                break
            current = nxt
        return current

    def get_subtask_root(self, subtask_id: int) -> Optional[MCTSNode]:
        return self.subtask_roots.get(subtask_id)

    def get_all_nodes(self) -> List[MCTSNode]:
        return list(self.nodes.values())

    def get_tree_stats(self) -> Dict[str, Any]:
        """Return summary statistics: total nodes, per-subtask counts,
        per-depth counts, and node type distribution."""
        depths: Dict[int, int] = {}
        node_type_counts: Dict[str, int] = {}
        for node in self.nodes.values():
            depths[node.get_depth()] = depths.get(node.get_depth(), 0) + 1
            node_type_counts[node.node_type] = node_type_counts.get(node.node_type, 0) + 1
        return {
            "total_nodes": len(self.nodes),
            "subtasks": len(self.subtask_roots),
            "nodes_by_subtask": {
                sid: len([n for n in self.nodes.values() if n.subtask_id == sid])
                for sid in self.subtask_roots
            },
            "nodes_by_depth": depths,
            "node_type_counts": node_type_counts,
        }
