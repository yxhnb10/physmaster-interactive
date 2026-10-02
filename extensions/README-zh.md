# PhysMaster Extensions（扩展）

本目录包含 PhysMaster 的**可选扩展**：一个用于支持 skill 的 AI agent 平台（如 OpenClaw、Claude Code）的 **PhysMaster Skill** 包，以及开发者可集成的**预构建自定义工具**。

## 目录结构

| 路径 | 用途 |
|------|------|
| [`skills/physmaster/`](skills/physmaster/) | Skill 元数据（`SKILL.md`）、参考文档、可执行脚本和安装辅助工具 |
| [`tools/`](tools/) | 预构建自定义工具，可直接集成（保留用于未来扩展） |

## `skills/physmaster` — PhysMaster Skill 包

**physmaster** skill 是一个结构化指南，帮助 AI agent 理解和使用 PhysMaster：前置条件、环境配置、MCTS 架构、工具能力、配置选项和故障排查。

### 文件结构

```
skills/physmaster/
├── SKILL.md                 Agent 入口文档
├── reference/
│   ├── configuration.md     完整 config.yaml 配置模式
│   ├── pipeline.md          MCTS 内部机制：选择、扩展、评估、反向传播、剪枝
│   ├── tools.md             所有工具：Python、arXiv、先验检索、技能包
│   └── prior_knowledge.md   FAISS RAG：摄入、检索、智慧积累
├── scripts/
│   ├── run_physmaster.py    运行完整管线（CLI + 可导入）
│   └── arxiv_search.py      独立 arXiv 论文搜索
├── install_cc.sh            Claude Code 一键安装
└── install_openclaw.sh      OpenClaw 一键安装
```

### 前置条件

使用 skill 之前，必须先 clone PhysMaster 项目并安装依赖：

```bash
git clone https://github.com/AdrianMiao27/PHY_Master.git
cd PHY_Master
pip install -r requirements.txt
```

Skill 包告诉 agent *如何*使用 PhysMaster，但不负责安装项目本身。

### 安装到 Claude Code

```bash
bash extensions/skills/physmaster/install_cc.sh
```

这会将 `SKILL.md` 复制到 `~/.claude/commands/physmaster.md`。之后在任意 Claude Code 会话中输入 `/physmaster` 即可加载 skill 上下文。在任何目录下都有效。

### 安装到 OpenClaw

```bash
bash extensions/skills/physmaster/install_openclaw.sh /path/to/evomaster/skills
```

这会将完整 skill 包复制到 OpenClaw 的 skills 目录。然后配置你的 agent：

```yaml
agents:
  my_agent:
    skills: ["physmaster"]
```

Agent 可以通过 `use_skill` 工具使用 PhysMaster：

| 动作 | 作用 | 示例 |
|------|------|------|
| `get_info` | 读取 `SKILL.md` | `use_skill(name="physmaster", action="get_info")` |
| `get_reference` | 读取特定参考文档 | `use_skill(name="physmaster", action="get_reference", path="tools.md")` |
| `run_script` | 执行脚本 | `use_skill(name="physmaster", action="run_script", path="run_physmaster.py --query '...'")` |

### 手动安装（备选方案）

如果不想使用安装脚本：

**Claude Code** — 手动复制文件：

```bash
mkdir -p ~/.claude/commands
cp extensions/skills/physmaster/SKILL.md ~/.claude/commands/physmaster.md
```

**OpenClaw** — 手动复制目录：

```bash
cp -r extensions/skills/physmaster /path/to/evomaster/skills/physmaster
```

## `tools/` — 预构建自定义工具

保留用于未来可集成到其他 agent 框架的 PhysMaster 专用工具。

## 另见

- 主项目：[README.md](../README.md)
- Skill 入口：[skills/physmaster/SKILL.md](skills/physmaster/SKILL.md)
