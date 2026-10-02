# PhysMaster v8.3.2 UTF-8 补丁

针对 Windows subprocess 读取线程出现 UnicodeDecodeError: gbk 的问题。在 v8.3.2 上覆盖应用，不新增依赖。

## 原因与范围

该日志说明某个子进程输出读取器使用 GBK 解码但收到不兼容字节。它是输出读取故障，不能单凭 traceback 判定数值计算是否成功。

v8.3.2 的外层 Python 工具已经显式使用 UTF-8、errors=replace，网页进程日志读取也已显式使用 UTF-8。用户可能仍在运行升级前启动的旧进程，或生成代码内部再次启动子进程时采用默认 GBK。当前日志没有足够信息确定是哪一层。

本补丁让 Python 工具启动的解释器使用 -X utf8，并向它和后续子进程传递 PYTHONUTF8=1、PYTHONIOENCODING=utf-8。新增启动脚本也从服务器启动时设置这些变量，使新研究工作进程继承。原有执行退出码、超时、目录和文件逻辑保留。

这能处理 Python 的默认编码路径；不会覆盖生成代码显式写死的 encoding='gbk'，也不能保证本地外部工具输出的编码。无法按 UTF-8 解码的外层字节仍替换为 �，避免读取线程崩溃，但不等于文字被正确恢复。如果关键结果文字乱码，须检查实际输出编码并复核，不可据此宣称研究通过。

## 安装和启动

1. 等当前研究结束或在轮间暂停后，确认是否要结束该次运行。保持原版本的关闭行为：Ctrl+C 会等待工作进程收尾，不提供立即取消。
2. 备份项目，把补丁文件解压到含 run.py、web_server.py 的项目根目录，覆盖 utils/python_utils.py。若尚未升级 v8.3.2，先使用完整的 v8.3.2 升级包。
3. 在原来已激活 Python 环境的 Anaconda Prompt / CMD 里，进入项目根目录，执行：

```bat
start_physmaster_utf8.cmd
```

也可不使用脚本，手动执行：

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

环境变量必须在启动服务器之前设置，刷新浏览器不能更新已启动进程的编码。修改工具文件也不会替换旧工作进程已经导入的函数。补充条件重新研究会启动新任务；旧输出保留，不是断点恢复。

## 当前任务怎么办

读线程错误可能造成部分输出丢失。先查看该节点后续工具结果、退出回执、Critic 与实际生成文件，不能仅凭任务还在继续就认定这个节点正常。本补丁不会修复已经丢失的旧输出。必要时在本轮结束后要求重新执行节点 18 的计算并核对输出。

## 测试

开发环境通过 3 个新回归测试：父环境设为 GBK／关闭 UTF-8 时工具与嵌套 Python 仍使用 UTF-8；非 UTF-8 原始输出不会让外层读取器崩溃；中文目录、嵌套失败退出码仍正确。另有 14 项原研究完整性测试通过。

测试实际启动 Python 子进程，未在用户真实 Windows／Anaconda 环境重现那条原始 traceback。未增加内存优化、并行度或其他研究逻辑的改动。
