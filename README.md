# PhysMaster Interactive

**基于 PhysMaster 的非官方交互扩展版，用于本地物理建模与研究过程监看。**

本项目在 PhysMaster 的研究框架上加入网页工作台、运行中追加条件、轮间暂停与继续、每轮成果监看，以及 Windows 文件锁兼容处理。当前工程整合版本为 **v8**；该版本编号仅用于本扩展项目，不代表 PhysMaster 上游的官方版本。

本项目由个人维护，与上海交通大学及 PhysMaster 原团队不存在官方隶属、背书或联合发布关系。

## 来源与致谢

PhysMaster 的原始研究框架、MCTS 搜索、多 Agent 协作及 LANDAU 知识层来自上游项目。本仓库的交互扩展不替代原团队的研究贡献。

- 上游仓库：[SJTU-SAI-Agents / PhysMaster](https://github.com/sjtu-sai-agents/PhysMaster)
- 论文：[PhysMaster: Building an Autonomous AI Physicist for Theoretical and Computational Physics Research](https://arxiv.org/abs/2512.19799)
- 原 README 存档：[README-upstream.md](README-upstream.md)
- 许可证：[MIT License](LICENSE)

当前整合基于已有的联网改进版源码，不保证与上游最新提交保持同步。网页交互、动态条件与成果监看扩展在开发中使用了 AI 辅助；功能是否可用以代码、测试和运行记录为依据。

## 功能与来源

| 层次 | 功能 | 来源/状态 |
| --- | --- | --- |
| 研究框架 | Clarifier、Supervisor、Theoretician、Critic、Summarizer | 基于 PhysMaster 上游 |
| 搜索与知识 | MCTS、分层记忆、LANDAU | 基于 PhysMaster 上游；知识数据需另行准备 |
| 联网工具 | Tavily 搜索、检索去重与过滤、模型分工 | 沿用已有联网改进版 |
| 网页工作台 | 创建任务、查看日志与最终总结 | 本交互扩展已实现 |
| 动态条件 | 运行中提交条件、版本标记、生效回执 | 本交互扩展已实现，轮间生效 |
| 任务控制 | 本轮结束后暂停、继续计算 | 本交互扩展已实现 |
| 成果监看 | 每轮目标、节点输出、Critic 评审、知识摘要与生成文件 | 本交互扩展已接入 Supervisor |
| Windows 兼容 | 原子写入重试、非关键进度写入失败降级 | 本交互扩展已实现，仍需真实 Windows 验证 |

## 快速开始

### 1. 环境与依赖

使用 Python 3.10 或更高版本，以及能够访问所选模型 API 的运行环境。若已有可运行 PhysMaster 联网版的环境，可以继续使用。

下载或克隆本仓库后，在包含 `run.py`、`web_server.py` 的项目根目录打开终端：

```bash
python -m pip install -r requirements.txt
```

启用 Tavily 时还需要其 SDK：

```bash
python -m pip install tavily-python
```

先验检索、索引构建、飞书机器人等依赖属于上游的可选功能；使用对应功能时需要准备相应依赖和资源。

### 2. 本地配置

复制配置示例。Windows CMD / Anaconda Prompt：

```bat
copy config.example.yaml config.yaml
```

macOS / Linux：

```bash
cp config.example.yaml config.yaml
```

编辑本地 `config.yaml` 中的模型设置：

```yaml
llm:
  base_url: "你的模型服务地址"
  api_key: "你的模型API密钥"
  model: "你的模型名称"
```

以上为字段示意，不要用它替换整个配置文件。当前版本沿用联网改进版的模型分工；更换模型服务时，也要检查 `llm.model_overrides` 和 `core/theoretician.py` 中显式指定的模型名称，并确认服务支持工具调用。

不使用网页搜索时，将 `tools.web_enabled` 设为 `false`。启用 Tavily 时使用：

```yaml
tools:
  web_enabled: true
  search_provider: tavily
  api_key_env: TAVILY_API_KEY
```

`api_key_env` 表示环境变量的名字，**不是密钥本身**。上述字段应合并到配置已有的 `tools` 节点。

在启动服务器的同一个窗口中设置搜索密钥。Windows CMD / Anaconda Prompt：

```bat
set "TAVILY_API_KEY=你的Tavily密钥"
```

Windows PowerShell：

```powershell
$env:TAVILY_API_KEY = "你的Tavily密钥"
```

macOS / Linux：

```bash
export TAVILY_API_KEY="你的Tavily密钥"
```

模型 API 密钥与 Tavily 密钥分别配置。修改环境变量后，需要重新启动服务器并创建新任务。

### 3. 启动网页

```bash
python web_server.py --cfg_file config.yaml
```

浏览器访问：<http://127.0.0.1:8765>

端口被占用时：

```bash
python web_server.py --cfg_file config.yaml --port 8766
```

随后访问 <http://127.0.0.1:8766>。网页模式不需要另外运行 `run.py`，服务器会启动独立的研究工作进程。

## 使用方式

1. 在“开始研究”输入初始问题并启动任务。
2. 查看进度、日志；在“每轮成果”中跟随最新轮次或选择历史轮次。
3. 展开节点，查看实际求解输出、Critic 决策/评分、整理后的知识与生成文件。
4. 在“条件对话”提交补充或纠正条件。消息显示“已生效”后，表示条件已纳入新的任务契约。
5. 想批量应用多条条件时，先点击“本轮结束后暂停”，等待“已暂停”，提交条件后再点击“继续计算”。
6. 最终总结阶段不再接收条件；任务结束后需要创建新任务。

例如，可以先要求建立总质量 190 kg 的太空跳伞下降模型，再补充迎风面积、大气密度或热模型的要求。得到数值结果后，应结合代码、假设及评审记录检查其有效性。

条件变化会重建任务与搜索树，并获得新一版的轮数和检索预算，可能增加 API 调用费用。旧轮次保留原条件版本，不自动认定为新条件下已验证。

## 每轮成果与输出

每轮监看记录来自实际派发目标和 MCTS 节点成果，不展示模型内部思维过程，也不人为补充未经输出支持的结论。

- 一轮可能包含多个并行节点。
- 节点执行时显示求解状态；当前批次返回后逐步显示输出、评审与知识摘要。
- Critic 的 reward 是模型评分，不是统计置信度，也不构成物理正确性的保证。
- 当前界面不流式展示模型 token 或数值仿真的每个时间步。

网页任务输出目录：

```text
outputs/web/<任务编号>/query/
```

| 文件或目录 | 内容 |
| --- | --- |
| `contract.json` | 当前结构化任务契约 |
| `node_<编号>/` | 各节点生成的代码与结果文件 |
| `research_monitor/round_XXXXXX.json` | 每轮成果记录 |
| `live_updates/` | 条件消息、回执和任务控制状态 |
| `revisions/` | 条件更新前的契约及轨迹 |
| `summary.md` | 最终总结 |

上一级目录包含运行日志及本次任务配置。运行配置可能含密钥，不应上传整个输出目录。

## 当前限制

- 所有聊天输入仍视为任务条件，尚未实现独立的解释问答模式。
- 暂停在轮间生效，不即时中断模型请求、检索或 Python 计算。
- 未实现跨进程断点恢复、自动实验规划和参数扫描闭环。
- 当前仅允许一个网页研究任务同时运行。
- 刷新或关闭网页不会停止任务；服务器重启后不恢复任务列表。
- 关闭服务时，在终端按 Ctrl+C；服务停止接收请求并等待当前工作进程完成。
- 当前服务面向本地使用，监听 `127.0.0.1`。它会执行模型生成的 Python 代码，不应直接对公网开放；公众在线服务需要另行实现身份认证、费用控制与隔离执行。

## 测试情况

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

当前开发环境中已通过 13 项自动测试，覆盖条件队列、重新规划、暂停/继续、本地 HTTP 接口、模拟 Windows 文件锁、每轮成果记录、文件下载和目录越界拒绝。修改后的 Python 文件通过编译检查。

实际前端 JavaScript 还在模拟 DOM 中连接模拟工作进程，验证了创建任务、追加条件、回执、暂停/继续、最终总结及每轮输出/评审展示。

尚未完成真实浏览器布局验收、真实 Windows 文件锁环境验证，以及真实模型/搜索 API 的完整端到端科研验证。自动测试不代表物理结论已验证。

## 贡献与问题反馈

欢迎提交可复现的错误报告或工程改进。反馈时请提供运行环境、复现步骤和脱敏后的报错；不要公开 API 密钥、私人配置或研究数据。

对于修改模型假设或科学结论的改动，请同时说明所用数据、公式来源、计算方法和验证依据。

## 许可证与署名

本仓库保留上游 MIT 许可证及原版权声明：

```text
Copyright (c) 2026 SJTU-SAI-Agents
```

完整许可见 [LICENSE](LICENSE)。原团队的研究贡献与版权声明应继续保留，新增工程扩展不改变这些来源。

第三方依赖、论文、图片、模型权重、语料和数据应遵守各自的许可或使用条款，不能仅凭本仓库的代码许可证推定可再分发。
