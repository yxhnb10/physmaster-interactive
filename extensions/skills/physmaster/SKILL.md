---
name: physmaster
description: MCTS-based autonomous physics problem solver with arXiv search, prior knowledge retrieval, and multi-agent reasoning. Use when you need to solve physics problems, search arXiv for relevant papers, or generate structured physics solutions with iterative refinement.
---

# PhysMaster Skill

PhysMaster is an LLM-powered agent system that solves physics problems through Monte Carlo Tree Search. It decomposes a problem into subtasks, explores multiple solution strategies in parallel, evaluates and refines them with a Critic, and produces a structured Markdown report.

## Prerequisites

Before running PhysMaster, ensure the following:

- **LLM API Key** (required): Any OpenAI-compatible API endpoint. Set `base_url`, `api_key`, and `model` in `config.yaml`.
- **Python 3.10+** (required)
- **faiss-cpu + sentence-transformers** (optional): For the FAISS-backed prior knowledge base. Not needed if `prior_enabled: false`.
- **lark-oapi** (optional): Only for the Feishu bot deployment.

Store API keys in `config.yaml` at the project root. See `reference/configuration.md` for the full config schema.

## Environment Setup

### Install

```bash
git clone https://github.com/AdrianMiao27/PHY_Master.git
cd PHY_Master
pip install -r requirements.txt
```

### Verify

```bash
python -c "from core.supervisor import SupervisorOrchestrator; print('PhysMaster ready')"
```

### Minimal Config

Edit `config.yaml`:

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "sk-..."
  model: "gpt-4o"

pipeline:
  query_file: "instructions/test.txt"
  output_path: "outputs"
  max_rounds: 10
  parallel_processes: 2
```

## Architecture

Five agents collaborate inside an MCTS loop:

```
Query ──▶ Clarifier ──▶ [ MCTS Search Loop ] ──▶ Summarizer ──▶ Report
                        │                     │
                        │ Supervisor ─▶ Theoretician(s) │
                        │      ▲            │           │
                        │      └──── Critic ◀┘           │
                        │                                │
                        │ select─expand─evaluate         │
                        │ backpropagate─prune             │
                        └────────────────────────────────┘
```

| Agent | Role |
|---|---|
| **Clarifier** | Parse problem into a structured contract with subtasks |
| **Supervisor** | Read tree context, pick next subtask, decide draft vs. revise |
| **Theoretician** | Solve a subtask — can call Python, skills, arXiv search, prior knowledge |
| **Critic** | Score the solution (0–1) and decide: `complete` / `to_revise` / `to_redraft` |
| **Summarizer** | Extract best trajectory and write a Markdown report |

## Running PhysMaster

### From command line

```bash
python run.py                    # default config.yaml
python run.py -c custom.yaml     # custom config
```

### From Python code

```python
from scripts.run_physmaster import run_physmaster

result = run_physmaster(
    query="Derive the Euler-Lagrange equation from Hamilton's principle.",
    config_path="config.yaml",
)
print(result["summary"])
```

### From an agent (OpenClaw / Claude Code)

Use the `run_script` action to execute `scripts/run_physmaster.py`:

```
python {baseDir}/scripts/run_physmaster.py --query "your physics problem" --config config.yaml
```

Or call `scripts/arxiv_search.py` for standalone arXiv search:

```
python {baseDir}/scripts/arxiv_search.py --query "quantum error correction" --top_k 5
```

## Tools Available to the Theoretician

During solving, the Theoretician agent has access to:

| Tool | Description |
|---|---|
| `Python_code_interpreter` | Execute Python code (numpy, scipy, sympy, matplotlib available) |
| `load_skill_specs` | Load LANDAU domain skills (12 built-in physics skills) |
| `library_search` | Search arXiv for papers by keyword, author, title, or arXiv ID |
| `prior_search` | Search the FAISS prior knowledge base (if enabled) |

## Feature Toggles

All features are optional and controlled via `config.yaml`:

| Feature | Config key | Default |
|---|---|---|
| Skills (domain knowledge) | `skills.enabled` | `true` |
| arXiv search | `landau.library_enabled` | `true` |
| Workflow templates | `landau.workflow_enabled` | `true` |
| Prior knowledge (RAG) | `landau.prior_enabled` | `true` |
| Cross-task wisdom | `landau.wisdom_save_enabled` | `false` |
| HTML visualization | `visualization.enabled` | `true` |

Set any to `false` to run without it. The system degrades gracefully — disabled features produce a warning and the pipeline continues.

## Output Structure

```
outputs/<task_name>/
├── contract.json           # Structured problem decomposition
├── node_1/                 # Theoretician output for MCTS node 1
├── node_2/                 # ...
├── summary.md              # Final solution report
└── visualization.html      # Interactive MCTS tree (open in browser)
```

## Reference Documentation

Detailed guides in the `reference/` directory:

| Document | Description |
|---|---|
| `reference/configuration.md` | Full config.yaml schema with all parameters |
| `reference/pipeline.md` | MCTS pipeline internals: select, expand, evaluate, backprop, prune |
| `reference/tools.md` | All tools available to agents (Python, arXiv, prior search, skills) |
| `reference/prior_knowledge.md` | FAISS RAG system: ingestion, retrieval, wisdom accumulation |

## Scripts

Executable scripts in the `scripts/` directory:

| Script | Purpose |
|---|---|
| `scripts/run_physmaster.py` | Run the full pipeline from command line or programmatically |
| `scripts/arxiv_search.py` | Standalone arXiv paper search tool |

## Troubleshooting

| Error | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'core'` | Run from the project root, or add root to `PYTHONPATH` |
| `FileNotFoundError: prompts/...` | Working directory must be the project root |
| `prior knowledge retrieval failed` | Prior index not built yet. Run `python LANDAU/prior/prior_store.py` or set `prior_enabled: false` |
| `PriorRetriever unavailable` | `faiss-cpu` or `sentence-transformers` not installed. Set `prior_enabled: false` |
| arXiv search returns empty | Check network connectivity. arXiv API requires 3s between requests |
| `max_rounds` exhausted on subtask 1 | Increase `max_rounds` or simplify the query to have clearer completion criteria |
