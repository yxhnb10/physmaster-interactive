# Tools Reference

## Overview

The Theoretician agent has access to four categories of tools during problem-solving. All tools are optional and can be disabled via config.

## Built-in Tools

### Python_code_interpreter

Execute Python code in a subprocess. Available packages:

- **Numerical**: `numpy`, `scipy`, `mpmath`
- **Symbolic**: `sympy`
- **Data**: `pandas`
- **Plotting**: `matplotlib`

**Usage:**

```python
Python_code_interpreter(code="""
import numpy as np
from scipy.integrate import odeint

def model(y, t):
    return -0.5 * y

t = np.linspace(0, 10, 100)
y = odeint(model, 1.0, t)
print(f"Final value: {y[-1][0]:.4f}")
""")
```

**Output:** Captured stdout/stderr as a string.

**Working directory:** Each node gets `outputs/<task_name>/node_<id>/` as its working directory. Files written by the code are saved there.

### load_skill_specs

Load LANDAU domain skills by name. Returns the full skill documentation as plain text.

**Usage:**

```python
load_skill_specs(skill_names=["classical-mechanics", "quantum-mechanics"])
```

**Available skills** (in `LANDAU/skills/`):

| Skill | Domain |
|---|---|
| `classical-mechanics` | Lagrangian, Hamiltonian, conservation laws |
| `quantum-mechanics` | Schrödinger equation, operators, perturbation theory |
| `statistical-mechanics` | Partition functions, ensembles, phase transitions |
| `electrodynamics` | Maxwell equations, gauge theory, radiation |
| `general-relativity` | Einstein field equations, geodesics, black holes |
| `quantum-field-theory` | Path integrals, Feynman diagrams, renormalization |
| `condensed-matter` | Band structure, superconductivity, topological phases |
| `particle-physics` | Standard Model, symmetries, cross sections |
| `astrophysics` | Stellar structure, cosmology, gravitational waves |
| `computational-physics` | Numerical methods, Monte Carlo, finite elements |
| `mathematical-methods` | Special functions, group theory, differential geometry |
| `experimental-techniques` | Data analysis, error propagation, instrumentation |

Each skill contains:
- **Concepts**: Key definitions and principles
- **Formulas**: Core equations with LaTeX
- **Methods**: Standard solution techniques
- **Examples**: Worked problems

## LANDAU Tools

### library_search

Search arXiv for physics papers. Uses the official arXiv API v1.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `query` | string | Search query. Supports field prefixes: `ti:` (title), `au:` (author), `abs:` (abstract), `cat:` (category). Can also be an arXiv ID like `2301.08727` |
| `top_k` | int | Number of results (default 5) |

**Usage:**

```python
library_search(query="quantum error correction surface code", top_k=5)
library_search(query="ti:black hole au:Hawking", top_k=3)
library_search(query="2301.08727", top_k=1)  # Lookup by arXiv ID
```

**Output:** Formatted text with title, authors, arXiv ID, published date, abstract snippet, and PDF link for each paper.

**Rate limit:** 3 seconds between requests (enforced by the tool).

**Availability:** Enabled by default. Set `landau.library_enabled: false` to disable.

### prior_search

Search the FAISS-backed prior knowledge base for relevant chunks from textbooks, papers, and past task wisdom.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `query` | string | Search query |
| `top_k` | int | Number of chunks (default 3) |
| `expand_context` | bool | Include prev/next chunks (default false) |
| `source_ids` | list[str] | Filter by source file, e.g. `["griffiths_qm.pdf"]` |
| `chapter` | string | Filter by chapter number |
| `section_prefix` | string | Filter by section prefix, e.g. `"2.1"` |
| `keywords` | list[str] | Filter by keywords |
| `rewrite_query` | bool | Apply query expansion (default true) |

**Usage:**

```python
prior_search(query="Noether's theorem", top_k=3)
prior_search(query="gauge symmetry", source_ids=["peskin_qft.pdf"], top_k=5)
```

**Output:** Formatted text with title, citation, chapter/section, and content for each chunk.

**Availability:** Requires `faiss-cpu` and `sentence-transformers`. Set `landau.prior_enabled: false` to disable. If the index hasn't been built yet, the tool returns a warning and the pipeline continues.

## Tool Availability Matrix

| Tool | Always Available | Requires Config | Requires Dependencies |
|---|---|---|---|
| `Python_code_interpreter` | ✓ | — | numpy, scipy, sympy (in requirements.txt) |
| `load_skill_specs` | ✓ | `skills.enabled: true` | — |
| `library_search` | — | `landau.library_enabled: true` | — (uses stdlib urllib) |
| `prior_search` | — | `landau.prior_enabled: true` | faiss-cpu, sentence-transformers |

## Tool Call Logging

All tool calls are logged to stdout:

```
[Theoretician] (node_id=3 subtask_id=1 node_type=draft) tool call library_search 🛠️
```

This helps trace which node used which tool during the MCTS search.
