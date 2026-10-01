# PhysMaster v8.2：总结下载与独立能力开关

基于非官方交互扩展版 v8.1；保留完成后续研、历史恢复、轮间追加条件、暂停及成果监看。
保留 PhysMaster 原项目的 MIT 许可证及署名。这是本扩展项目的版本号，不是上游版本号。

## 已有 v8.1：安装升级补丁

1. 等当前研究完成，在启动服务器的终端按 Ctrl+C，等待服务退出。
2. 备份现有项目。解压 PhysMaster-v8.2-upgrade.zip，把其中的文件按原目录结构
   复制到现有项目根目录，覆盖同名文件。尤其要复制 utils/capabilities.py 和 core/theoretician.py，
   不能只替换网页和 web_server.py。
3. 保留你的 config.yaml、outputs、知识库数据和环境变量配置。补丁不包含这些私人文件。
   config.example.yaml 仅是更新后的示例；不要用它替换已经配好的 config.yaml。
4. 在原来的 Python 环境启动：

```powershell
python web_server.py --cfg_file config.yaml
```

5. 浏览器打开 http://127.0.0.1:8765，按 Ctrl+F5 刷新。无需增加 Python 依赖。

完整包 PhysMaster-v8.2.zip 包含项目源码。升级现有安装时优先用补丁包。
新安装需按 README.md 安装原有依赖，并从 config.example.yaml 创建配置。

## 下载 summary

选择已经完成的任务，在“研究结果”中选择格式并点击“下载总结”。历史任务也支持下载。

- Markdown (.md)：保存 summary.md 的文字内容。
- 纯文本 (.txt)：同一份总结原文，保留 Markdown/LaTeX 标记。
- HTML (.html)：不依赖外部资源、经过 HTML 转义的可打印文档。用浏览器打开后，
  按 Ctrl+P 选择“另存为 PDF”。保留原文标记，不自动渲染公式或嵌入节点图件。

任务未完成时不能下载；任务失败且没有 summary.md 时也不能下载。
这只导出已有总结，不会额外调用模型，也不替代节点应生成的完整 PDF 科研报告。
下载文件名包含任务编号前八位，方便区分不同轮次。

## 七项独立能力

| 网页开关 | 对应配置 | 说明 |
| --- | --- | --- |
| WebSearch | tools.web_enabled | Tavily 等网页搜索服务 |
| arXiv 论文检索 | landau.library_enabled | 独立的联网论文检索 |
| CODATA | tools.codata_enabled | 本地物理常数表 |
| Python 执行 | tools.python_enabled | 数值计算、代码执行与生成文件 |
| Skills | skills.enabled | 技能摘要和技能文件加载 |
| Workflow | landau.workflow_enabled | Clarifier 的研究流程匹配 |
| Prior | landau.prior_enabled | 已有向量知识库检索 |

界面默认值来自 config.yaml。选择只写入新任务的 web_config.yaml，不改基础配置。
任务启动后开关固定，控件禁用。任务完成后可调整开关，再点击“补充条件并重新研究”。
续研未指定开关时继承上一轮；指定部分开关时仅覆盖那些项，其余继承。
界面修改不会在每秒轮询时被重置。切换历史任务时显示该任务记录的设置。
“恢复配置默认值”恢复基础配置的开关，用于下一次新建/续研任务。

关闭 WebSearch 不会自动关闭 arXiv；若要关闭这两种检索，应分别关闭。
开关控制本项目提供的工具/流程，不是操作系统网络隔离或执行沙箱。
模型 API 仍可能需要联网；Python 开启时执行的代码也可能联网。
关闭 Python 后不能通过本项目的 Python 工具执行代码；模型可以输出未执行的代码文本，
不应将其视为已经完成的计算或验证。
开关不会自动申请 API 密钥、安装 SDK、生成索引或保证相关服务可用。
其他原有设置（如知识沉淀、检索评审和树可视化）仍通过 config.yaml 配置。

## API

GET /api/capabilities 返回开关说明和基础配置默认值，不返回模型或搜索密钥。

POST /api/tasks
请求：{"query":"研究问题", "capabilities":{"web_search":false,"arxiv_search":true}}

POST /api/tasks/<任务UUID>/continue
请求：{"text":"新条件", "capabilities":{"python":true,"skills":false}}

capabilities 可以省略；值必须是 JSON 布尔值，未知键会被拒绝。
任务查询返回 capabilities；task.json 中保存开关以支持重启后的历史恢复。
旧 v8/v8.1 任务的设置从其原 web_config.yaml 读取。

GET /api/tasks/<任务UUID>/summary?format=md
支持 format=md、txt、html；返回 Content-Disposition: attachment。
运行中返回 409，无总结返回 404，不支持的格式返回 400。
下载仅指向该任务的 summary.md，不接受任意文件路径。
原有本地 Host、Origin 和 POST 请求 token 校验继续保留。

## 验证

24 项自动测试通过，包括总结导出、HTML 转义、下载路径限制、开关参数校验、
配置写入与继承、重启恢复、禁用工具不注册、Python/Skills 独立控制及原有 v8.1 功能。
前端 JavaScript 在 DOM 环境连接真实本地 HTTP 服务与模拟研究进程，验证了开关选择、
运行中固定设置、三种总结下载、续研调整、轮询保留选择和历史切换。
Python 编译检查与 git diff --check 通过。
测试不调用真实模型或搜索 API；尚需真实 Windows 和完整模型 API 流程验证。
本版本仍面向本地使用，没有加入公网部署、账号登录或计算沙箱。
