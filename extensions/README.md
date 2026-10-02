# PhysMaster Extensions

This directory contains **optional extensions** for PhysMaster: a **PhysMaster Skill** package for skill-capable agents (such as OpenClaw, Claude Code), plus **pre-built custom tools** developers can integrate.

## Layout

| Path | Purpose |
|------|---------|
| [`skills/physmaster/`](skills/physmaster/) | Skill metadata (`SKILL.md`), reference docs, executable scripts, and install helpers |
| [`tools/`](tools/) | Pre-built custom tools for direct integration (reserved for future extensions) |

## `skills/physmaster` — PhysMaster Skill Package

The **physmaster** skill is a structured guide for AI agents to understand and use PhysMaster: prerequisites, environment setup, MCTS architecture, tool capabilities, configuration options, and troubleshooting.

### File Structure

```
skills/physmaster/
├── SKILL.md                 Entry point for agents
├── reference/
│   ├── configuration.md     Full config.yaml schema
│   ├── pipeline.md          MCTS internals: select, expand, evaluate, backprop, prune
│   ├── tools.md             All tools: Python, arXiv, prior search, skills
│   └── prior_knowledge.md   FAISS RAG: ingestion, retrieval, wisdom accumulation
├── scripts/
│   ├── run_physmaster.py    Run the full pipeline (CLI + importable)
│   └── arxiv_search.py      Standalone arXiv paper search
├── install_cc.sh            One-click install for Claude Code
└── install_openclaw.sh      One-click install for OpenClaw
```

### Prerequisites

Before using the skill, the PhysMaster project must be cloned and its dependencies installed:

```bash
git clone https://github.com/AdrianMiao27/PHY_Master.git
cd PHY_Master
pip install -r requirements.txt
```

The skill package tells agents *how* to use PhysMaster, but it does not install the project itself.

### Install for Claude Code

```bash
bash extensions/skills/physmaster/install_cc.sh
```

This copies `SKILL.md` to `~/.claude/commands/physmaster.md`. After that, type `/physmaster` in any Claude Code session to load the skill context. Works from any directory.

### Install for OpenClaw

```bash
bash extensions/skills/physmaster/install_openclaw.sh /path/to/evomaster/skills
```

This copies the full skill package into OpenClaw's skills directory. Then configure your agent:

```yaml
agents:
  my_agent:
    skills: ["physmaster"]
```

The agent can then use PhysMaster through the `use_skill` tool:

| Action | What it does | Example |
|--------|-------------|---------|
| `get_info` | Read `SKILL.md` | `use_skill(name="physmaster", action="get_info")` |
| `get_reference` | Read a specific reference doc | `use_skill(name="physmaster", action="get_reference", path="tools.md")` |
| `run_script` | Execute a script | `use_skill(name="physmaster", action="run_script", path="run_physmaster.py --query '...'")` |

### Manual Installation (Alternative)

If you prefer not to use the install scripts:

**Claude Code** — copy the file manually:

```bash
mkdir -p ~/.claude/commands
cp extensions/skills/physmaster/SKILL.md ~/.claude/commands/physmaster.md
```

**OpenClaw** — copy the directory manually:

```bash
cp -r extensions/skills/physmaster /path/to/evomaster/skills/physmaster
```

## `tools/` — Pre-built Custom Tools

Reserved for future PhysMaster-specific tools that can be integrated into other agent frameworks.

## See also

- Main project: [README.md](../README.md)
- Skill entry: [skills/physmaster/SKILL.md](skills/physmaster/SKILL.md)
