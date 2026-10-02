# Pipeline Internals

## MCTS Search Loop

PhysMaster uses Monte Carlo Tree Search to explore multiple solution strategies. Unlike a linear solve-revise loop, the system maintains a tree of attempts and navigates it using UCB1 selection.

## Execution Flow

```
┌─────────────────────────────────────────────────────────┐
│                    run.py / main()                       │
│                                                         │
│  1. load_config(config.yaml)                            │
│  2. clarify_query() → Clarifier.run(text) → contract    │
│  3. SupervisorOrchestrator(contract, ...) → tree        │
│  4. supervisor.run() → MCTS loop                        │
│  5. WisdomStore.save() (optional)                       │
│  6. TrajectorySummarizer.write_summary_markdown()       │
│  7. generate_vis() (optional)                           │
└─────────────────────────────────────────────────────────┘
```

## MCTS Loop Detail

Each round of `supervisor.run()`:

### 1. Select

UCB1 picks the most promising open node:

```
UCB(node) = average_reward + C * sqrt(ln(parent_visits) / node_visits)
```

where `C` is `exploration_constant` (default 1.414). Unvisited nodes get infinite UCB (explored first). Ties broken by depth (deeper preferred) then node ID.

### 2. Resolve Dispatch

The Supervisor LLM examines the selected node's context and decides:
- Which subtask to work on next
- Whether to `draft` (new attempt) or `revise` (improve existing)
- An augmented description for the Theoretician

The Supervisor can also call `library_search` and `prior_search` tools during dispatch.

### 3. Expand

`expansion_count` child nodes are created (controlled by `draft_expansion` or `revise_expansion`). Each is submitted to a **separate subprocess** via `ProcessPoolExecutor` with `spawn` start method.

Each Theoretician subprocess:
- Re-loads the contract from `contract.json` (for subprocess isolation)
- Receives the subtask description + tree context
- Calls the LLM with tool access (Python, skills, arXiv, prior)
- Writes output to `node_<id>/`

All children expand in parallel — this is the main throughput multiplier.

### 4. Evaluate

The Critic LLM scores each child node. The output is a JSON with:

```json
{
  "decision": "complete | to_revise | to_redraft",
  "verdict": "accept | refine | reject",
  "reward": 0.0 - 1.0,
  "summary": "...",
  "opinion": "..."
}
```

The Critic also has access to `library_search` and `prior_search` for verification.

### 5. Distill Knowledge

After evaluation, the raw experience is compressed into a concise knowledge summary and stored on the node. This compressed knowledge is propagated through the tree context so future nodes see what ancestors learned.

### 6. Backpropagate

The reward propagates upward from child to root:

```python
node.visits += 1
node.total_reward += reward
node.average_reward = total_reward / visits
```

**Cognitive reinforcement**: When a node scores above 0.8, its verified knowledge is tagged onto ancestor nodes, strengthening context quality for future expansions on the same branch.

### 7. Prune

If `active_beam_width > 0`, nodes at each depth level beyond the budget are closed (`status = "completed_closed"`). Ranking: reward → average reward → visits → recency.

### 8. Termination

The loop stops when:
- A root-to-leaf path completes **all** subtasks, OR
- `max_rounds` is reached

The best path is extracted by: (1) preferring paths that complete all subtasks, (2) highest cumulative reward, (3) longest path, (4) highest leaf reward.

## Node Lifecycle

```
open → completed → completed_expended (has children)
                 → completed_closed   (pruned)
     → failed                         (subprocess error)
```

## Subprocess Architecture

Theoretician workers run in separate processes (not threads) via `ProcessPoolExecutor` with `spawn`:

- Avoids GIL contention for CPU-bound LLM token processing
- Each subprocess gets a fresh Python interpreter (no shared state)
- Contract is re-read from disk in each subprocess
- The global pool is shared across rounds (initialized once)
