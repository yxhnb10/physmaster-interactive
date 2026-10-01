<div align="center">
<br>

<h1>🔬 PhysMaster</h1>

<p><strong>Solve physics problems with LLM-driven Monte Carlo Tree Search</strong></p>

<p>
<a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"></a>&nbsp;
<a href="#-getting-started"><img src="https://img.shields.io/badge/API-OpenAI%20Compatible-412991?style=for-the-badge&logo=openai&logoColor=white" alt="OpenAI Compatible"></a>&nbsp;
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge" alt="MIT License"></a>&nbsp;
</p>

<p>
<a href="README_CN.md">中文文档</a> ·
📄 Paper:&nbsp;<a href="https://arxiv.org/abs/2512.19799"><img src="https://img.shields.io/badge/arXiv-2512.19799-b31b1b?logo=arxiv&logoColor=white" alt="arXiv" height="18"/></a>
</p>

<br>
</div>

PhysMaster decomposes a physics problem into subtasks, explores multiple solution strategies **in parallel** through an MCTS search tree, evaluates and refines them with a Critic, and distills reusable knowledge &mdash; from a single node all the way up to a cross-task wisdom store.

## 📋 Core Features

- **MCTS-Driven Search**: Explores multiple solution paths in parallel, balancing exploitation and exploration via UCB1
- **Multi-Agent Pipeline**: 5 specialized agents (Clarifier → Supervisor → Theoretician → Critic → Summarizer) collaborate in a structured loop
- **Hierarchical Memory**: Per-node experience → compressed knowledge → cross-task wisdom, with cognitive reinforcement on high-reward paths
- **LANDAU Knowledge Layer**: RAG over 74 physics textbooks, real-time arXiv search, 12 domain skill packs, and workflow templates
- **Minimal Config**: Only `llm` + `pipeline.query_file` required; everything else has sensible defaults

---

## 🏗 Architecture

<div align="center">
<img src="assets/workflow.png" alt="PhysMaster Architecture" width="800"/>
</div>

**Component** | **Role**
:--|:--
🔍 **Clarifier** | Parse problem into structured subtasks
🎯 **Supervisor** | Read tree context, pick next subtask, decide draft vs. revise
⚡ **Theoretician** | Solve subtask with Python, skills, arXiv, prior knowledge
🧪 **Critic** | Score solution (0&ndash;1): `complete` · `to_revise` · `to_redraft`
📄 **Summarizer** | Extract best trajectory, write markdown report
📦 **LANDAU** | External knowledge layer &mdash; provides prior RAG, arXiv search, domain skills, and workflow templates

> **The loop stops** when all subtasks are completed along some path, or the round budget runs out.

---

## 🚀 Getting Started

> **Prerequisites:** Python 3.10+, an OpenAI-compatible LLM API key

```bash
# 1. Install
git clone https://github.com/AdrianMiao27/PHY_Master.git
cd PHY_Master
pip install -r requirements.txt

# 2. Configure — edit config.yaml
#    llm.base_url / api_key / model  +  pipeline.query_file

# 3. Run
python run.py                    # default config.yaml
python run.py -c custom.yaml     # custom config
```

Output lands in `outputs/<task_name>/` — includes `summary.md`, `visualization.html` (interactive MCTS tree), and per-node working directories.

> 💡 **Minimal mode** — run without external knowledge: set `skills.enabled: false` and all `landau.*_enabled: false`.

<details>
<summary><b>⚙ Full config reference</b></summary>

```yaml
# ── LLM ──────────────────────────────────────────────
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "sk-..."
  model: "gpt-4o"

# ── Pipeline ─────────────────────────────────────────
pipeline:
  query_file: "instructions/test.txt"
  output_path: "outputs"
  max_rounds: 10              # MCTS round budget
  parallel_processes: 2       # concurrent Theoretician workers
  debug_logging: false        # detailed per-node logs in outputs/<task>/log/

# ── MCTS ─────────────────────────────────────────────
mcts:
  draft_expansion: 2          # children per draft expansion
  revise_expansion: 1         # children per revise expansion
  exploration_constant: 1.414 # UCB1 exploration weight (sqrt 2)
  active_beam_width: 0        # 0 = no pruning; N = keep top-N per depth

# ── Clarifier ────────────────────────────────────────
clarifier:
  max_key_concpets: 5

# ── Skills ───────────────────────────────────────────
skills:
  enabled: true
  roots:
    - "LANDAU/skills"

# ── LANDAU knowledge modules ─────────────────────────
landau:
  library_enabled: true       # arXiv paper search
  library: "LANDAU/library"
  workflow_enabled: true       # problem-solving templates
  workflow: "LANDAU/workflow"
  prior_enabled: true          # FAISS RAG knowledge base
  prior: "LANDAU/prior"
  wisdom_save_enabled: false   # persist distilled wisdom after each task

# ── Visualization ────────────────────────────────────
visualization:
  enabled: true
```

**Key parameters:**

| Key | Description | Default |
|:----|:------------|:-------:|
| `pipeline.max_rounds` | Total MCTS iterations before forced stop | `10` |
| `pipeline.parallel_processes` | Theoretician subprocesses | `2` |
| `pipeline.debug_logging` | Write detailed per-node JSON logs | `false` |
| `mcts.draft_expansion` | Child nodes per draft round | `2` |
| `mcts.revise_expansion` | Child nodes per revise round | `2` |
| `mcts.exploration_constant` | UCB1 exploration term | `1.414` |
| `mcts.active_beam_width` | Beam pruning width (0 = off) | `0` |

</details>

---

## 🌳 Core Method

### MCTS Search

PhysMaster does **not** solve linearly. It maintains a tree of solution attempts and navigates it like a game:

| Step | What happens |
|:-----|:-------------|
| **Select** | UCB1 picks the most promising leaf, balancing reward vs. exploration |
| **Expand** | N Theoretician workers spawn **in parallel** and produce child nodes |
| **Evaluate** | Critic scores each child on a 0&ndash;1 scale |
| **Backpropagate** | Reward flows upward; high-reward nodes (&gt;0.8) reinforce ancestors with verified knowledge |
| **Prune** | If beam width is set, low-reward nodes beyond the budget are closed |

The search terminates when a complete path is found or `max_rounds` is hit. The best root-to-leaf path is extracted for the summary.

### Memory System

The search tree carries knowledge forward at **three scopes**:

| Scope | Description |
|:------|:------------|
| **🔬 Per-Node Experience** | Full Theoretician output — reasoning, tool calls, code. Available to the Critic for evaluation, then compressed. |
| **📦 Compressed Knowledge** | Distilled summary attached to each node. Ancestors and siblings share insights through tree context. |
| **🌐 Cross-Task Wisdom** | Best trajectory distilled post-task and written back to the FAISS index for future retrieval. |

> High-reward nodes (&gt;0.8) trigger **cognitive reinforcement**: verified knowledge is propagated to ancestors during backpropagation.

---

## 📦 LANDAU Knowledge System

The `LANDAU/` directory provides the external knowledge layer that powers the Theoretician's domain expertise.

| Module | Path | What it does |
|:-------|:-----|:-------------|
| **📚 Prior (RAG)** | `LANDAU/prior/` | PDF/MD/Text → parent-child chunks → `bge-small-en-v1.5` → FAISS index. Dense + BM25 hybrid retrieval with RRF. |
| **🔎 Library** | `LANDAU/library/` | Real-time arXiv paper search & retrieval at solve time. |
| **🔧 Skills** | `LANDAU/skills/` | 12 domain knowledge packs — Theoretician loads on demand. |
| **📋 Workflows** | `LANDAU/workflow/` | YAML solving templates — Clarifier auto-matches by keyword overlap. |

**Pre-built knowledge base**: [PhysLib on HuggingFace](https://huggingface.co/datasets/Kev1n-J1N/PhysLib) — 78k chunks from 74 physics textbooks.

<details>
<summary><b>Ingestion commands</b></summary>

```bash
python LANDAU/prior/prior_store.py                             # ingest all
python LANDAU/prior/prior_store.py --target path/to/file.pdf   # single file
python LANDAU/prior/prior_store.py --reset                     # full rebuild
```

</details>

<details>
<summary><b>12 built-in skills</b></summary>

| Skill | Coverage |
|:------|:---------|
| Classical Electrodynamics | Maxwell's equations, radiation, waveguides |
| Quantum Mechanics | Schrodinger equation, scattering, angular momentum |
| Thermodynamics & Statistical Mechanics | Partition functions, phase transitions, ensembles |
| Conservation Laws | Noether's theorem, conserved currents |
| Perturbation Expansion | Regular/singular perturbation, asymptotic series |
| Variational Methods | Euler-Lagrange, Rayleigh-Ritz |
| Dimensional Analysis | Pi theorem, natural units, scaling laws |
| Symmetry Analysis | Group theory, Lie algebras, representations |
| Fourier & Spectral Analysis | Fourier/Laplace transforms, spectral methods |
| Numerical ODE/PDE | Runge-Kutta, finite difference/element |
| Statistical Error Analysis | Error propagation, fitting, Monte Carlo |
| LaMET Asymptotic Expansion | Large-momentum effective theory |

</details>

---

## 🔌 Integrations

| Platform | How |
|:---------|:----|
| **🤖 Feishu Bot** | Send a physics problem in chat → pipeline runs in background → summary pushed back. See [feishu/README.md](feishu/README.md). |
| **🧩 Claude Code** | `bash extensions/skills/physmaster/install_cc.sh` → use `/physmaster` in any session. |
| **🧩 OpenClaw** | `bash extensions/skills/physmaster/install_openclaw.sh /path/to/skills` → `use_skill(name="physmaster", ...)`. |

See **[extensions/README.md](extensions/README.md)** for details.

---

## 📁 Project Structure

```
PHY_Master/
├── run.py                       Entry point
├── config.yaml                  Configuration
├── requirements.txt
├── core/                        Core pipeline
│   ├── clarifier.py               Query → structured contract
│   ├── supervisor.py              MCTS orchestrator
│   ├── mcts.py                    MCTSNode / MCTSTree
│   ├── theoretician.py            Solver agent (subprocess)
│   ├── summarizer.py              Trajectory → markdown report
│   └── visualization.py           Tree → interactive HTML
├── LANDAU/                      Knowledge modules
│   ├── skills/                    12 built-in physics skills
│   ├── workflow/                  Problem-solving YAML templates
│   ├── library/                   arXiv paper search & retrieval
│   └── prior/                     FAISS RAG knowledge base
├── utils/                       Utilities
├── prompts/                     14 prompt templates (7 agents)
├── instructions/                Query files
├── extensions/                  Skill plugins for CC / OpenClaw
├── feishu/                      Feishu bot integration
└── outputs/                     Generated at runtime
```

---

## 💬 Community

<div align="center">
<img src="assets/wechat_qr.jpg" alt="WeChat Group QR Code" width="200"/>
<br><em>Scan to join the WeChat group</em>
</div>

---

## 📄 Citation

If you use PhysMaster in your research, please cite:

```bibtex
@misc{miao2025physmaster,
      title={PhysMaster: Building an Autonomous AI Physicist for Theoretical and Computational Physics Research},
      author={Tingjia Miao and Jiawen Dai and Jingkun Liu and Jinxin Tan and Muhua Zhang and Wenkai Jin and Yuwen Du and Tian Jin and Xianghe Pang and Zexi Liu and Tu Guo and Zhengliang Zhang and Yunjie Huang and Shuo Chen and Rui Ye and Yuzhi Zhang and Linfeng Zhang and Kun Chen and Wei Wang and Weinan E and Siheng Chen},
      year={2025},
      eprint={2512.19799},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2512.19799},
}
```

---

## License

[MIT](LICENSE)
