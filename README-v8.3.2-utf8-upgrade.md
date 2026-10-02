# PhysMaster v8.3.2 + UTF-8 修复：合并升级包

这个包一次覆盖应用 v8.3.2 全部升级文件和 UTF-8 修复。适用于已有 v8.3.1（含计时增强版）或 v8.3.2 项目，不是完整项目包。已经装过单独 UTF-8 补丁也可以重复覆盖。

## 升级

1. 等当前研究结束，在服务器终端按 Ctrl+C，等待服务退出。关闭服务仍会等待研究工作进程收尾；它不是立即取消按钮。
2. 备份项目，把本包内容直接解压到项目根目录，允许覆盖同名文件。解压后 run.py、web_server.py、start_physmaster_utf8.cmd 应在同一级。
3. 保留自己的 config.yaml、环境变量、知识数据及 outputs。本包不包含私有配置、API 密钥或研究成果，不新增第三方依赖。
4. 在已经激活原 Python 环境的 Anaconda Prompt / CMD 里进入项目根目录，运行：

```bat
start_physmaster_utf8.cmd
```

也可以手动启动：

```bat
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
python -X utf8 web_server.py --cfg_file config.yaml
```

PowerShell：

```powershell
$env:PYTHONUTF8="1"
$env:PYTHONIOENCODING="utf-8"
python -X utf8 web_server.py --cfg_file config.yaml
```

5. 浏览器打开 http://127.0.0.1:8765 ，按 Ctrl+F5 刷新。版本标题仍是 v8.3.2。启动脚本可附加 --port 8766 等服务器参数。

不要另外嵌套一层补丁目录。若服务器或工作进程未重启，它们可能继续使用升级前导入的旧代码。

## 合并内容

- v8.3.2：用户硬参数核对、Critic 阻塞问题检查、禁止强制跳过未通过前置任务、真实执行回执、部分完成状态、当前分支文件核对和下载、Summary 程序核对记录。详细说明见 README-v8.3.2.md。
- 保留此前的总时长与阶段计时、能力开关、Critic 阈值、动态条件和完成后续研。
- UTF-8 修复：外层读取显式 UTF-8／替换无法解码字节；Python 执行解释器启用 -X utf8，并传递 PYTHONUTF8=1 和 PYTHONIOENCODING=utf-8 给后续子进程。详细说明见 README-utf8-fix.md。

只合并既有两份补丁，没有新增内存优化、扩大并行度或修改物理结果。旧结果不自动重算；无法恢复此前读线程丢失的输出。生成代码显式指定错误编码或外部工具输出非 UTF-8 时仍需要核查。

## 验证

本包在 v8.3.1 计时增强版上应用合并文件后验证，升级文件与 v8.3.2 完整包一致，仅 Python 工具采用后续 UTF-8 修复，并新增启动脚本、测试及文档。

合并后的 58 项自动测试全部通过；47 个 Python 源文件和网页 JavaScript 通过语法检查。测试含实际 Python 子进程编码验证，模型与研究工作进程部分使用模拟接口。

真实 Windows／Anaconda 原报错及真实模型 API 完整科研流程尚未端到端验证；测试通过不是科学正确性证明。
