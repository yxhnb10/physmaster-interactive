# Configuration Reference

## Overview

PhysMaster uses a single `config.yaml` at the project root. All sections except `llm` are optional with sensible defaults.

## Full Schema

```yaml
# ── LLM ──────────────────────────────────────────────
llm:
  base_url: "https://api.openai.com/v1"   # OpenAI-compatible endpoint (required)
  api_key: "sk-..."                        # API key (required)
  model: "gpt-4o"                          # Model name (required)

# ── Pipeline ─────────────────────────────────────────
pipeline:
  query_file: "instructions/test.txt"      # Path to the physics problem file
  output_path: "outputs"                   # Output directory
  max_rounds: 10                           # MCTS round budget
  parallel_processes: 2                    # Concurrent Theoretician subprocesses

# ── MCTS ─────────────────────────────────────────────
mcts:
  draft_expansion: 2                       # Children per draft expansion
  revise_expansion: 1                      # Children per revise expansion
  exploration_constant: 1.414              # UCB1 exploration weight (sqrt(2))
  active_beam_width: 0                     # 0 = no pruning; N = keep top-N per depth

# ── Clarifier ────────────────────────────────────────
clarifier:
  max_key_concpets: 5                      # Max key concepts to extract

# ── Skills ───────────────────────────────────────────
skills:
  enabled: true                            # Enable LANDAU skill packages
  roots:
    - "LANDAU/skills"                      # Directories to scan for skills

# ── LANDAU Knowledge Modules ─────────────────────────
landau:
  library_enabled: true                    # arXiv paper search
  library: "LANDAU/library"

  workflow_enabled: true                   # Problem-solving YAML templates
  workflow: "LANDAU/workflow"

  prior_enabled: true                      # FAISS RAG knowledge base
  prior: "LANDAU/prior"

  wisdom_save_enabled: false               # Persist distilled wisdom after each task

# ── Visualization ────────────────────────────────────
visualization:
  enabled: true                            # Generate interactive HTML tree
```

## Parameter Details

### pipeline

| Key | Type | Default | Description |
|---|---|---|---|
| `query_file` | string | `"instructions/test.txt"` | Path to the input problem file (plain text, LaTeX OK) |
| `output_path` | string | `"outputs"` | Root directory for task outputs |
| `max_rounds` | int | `10` | Maximum MCTS iterations before forced stop |
| `parallel_processes` | int | `2` | Number of Theoretician worker subprocesses (uses `ProcessPoolExecutor` with `spawn`) |

### mcts

| Key | Type | Default | Description |
|---|---|---|---|
| `draft_expansion` | int | `2` | Number of child nodes generated per draft expansion round |
| `revise_expansion` | int | `2` | Number of child nodes generated per revise expansion round |
| `exploration_constant` | float | `1.414` | UCB1 exploration term weight. Higher = more exploration |
| `active_beam_width` | int | `0` | Beam pruning: 0 = disabled. When > 0, low-reward nodes beyond this width are closed at each depth |

### landau

| Key | Type | Default | Description |
|---|---|---|---|
| `library_enabled` | bool | `true` | Enable arXiv paper search tool for all agents |
| `workflow_enabled` | bool | `true` | Enable workflow template matching in the Clarifier |
| `prior_enabled` | bool | `true` | Enable FAISS-backed prior knowledge retrieval |
| `wisdom_save_enabled` | bool | `false` | After task completion, distill wisdom and write back to prior index |

### Graceful Degradation

All LANDAU modules (library, prior, wisdom) are **fail-safe**. If a dependency is missing or initialization fails:

1. A warning is printed (e.g., `[Supervisor] Warning: PriorRetriever init failed: ...`)
2. The feature is automatically disabled for the rest of the run
3. The pipeline continues using the LLM alone

This means you can safely set `prior_enabled: true` even if the FAISS index hasn't been built yet — the system will warn and continue.
