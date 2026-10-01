# PhysMaster v8.3.1：手动设置 Critic 阈值与运行计时

以本对话提供的 v8.2 为基础，版本号按本扩展项目命名。
保留总结下载、七项能力开关、完成后重新研究、历史恢复与轮间条件更新。
保留上游 PhysMaster 的 MIT 许可证、版权与署名；不是上游官方版本。
本版没有加入恢复旧进程或旧 MCTS 搜索树的断点续研功能。

## 安装

1. 等研究结束，在服务器终端按 Ctrl+C 并等待退出。备份现有项目。
2. 解压 PhysMaster-v8.3.1-upgrade.zip，将全部文件按目录结构覆盖到原项目。
   必须一起复制 utils/critic_policy.py、core/supervisor.py、core/retrieval_critic.py、
   prompts/critic_prompt.txt、web_server.py 和 web/index.html；不能只替换网页。
   包中也附带 v8.2 的能力开关相关文件，兼容此前 v8.1 安装。
3. 保留已有 config.yaml、outputs、知识库数据和环境变量。不要用 config.example.yaml
   覆盖已配置的 config.yaml，它只是字段示例。
4. 在原 Python 环境中启动：

```powershell
python web_server.py --cfg_file config.yaml
```

5. 打开 http://127.0.0.1:8765，按 Ctrl+F5 刷新。
无需增加 Python 依赖。完整源码包 PhysMaster-v8.3.1.zip 可用于新安装。

本次计时增强版使用 PhysMaster-v8.3.1-timing-upgrade.zip（覆盖升级）与
PhysMaster-v8.3.1-timing.zip（完整源码）。继续沿用 v8.3.1 版本号，见
[README-v8.3.1-timing.md](README-v8.3.1-timing.md)。必须完整覆盖升级包，不能只替换网页。

## 网页设置

“开始研究”区域增加“Critic 评审设置”。字段取值范围为 0～1。

| 字段 | 默认值 | 实际规则 |
| --- | --- | --- |
| 研究通过阈值 | 0.85 | 评分达到此值才有资格标记 complete |
| 研究重做阈值 | 0.60 | 有效评分低于此值，转为 to_redraft |
| 检索相关性阈值 | 0.50 | 成功评审后，保留评分不低于此值的搜索结果 |
| 启用检索评审 | 来自配置，未配置时关闭 | 控制 WebSearch/arXiv 检索结果相关性评审 |

重做阈值必须小于通过阈值。通过阈值越高通常越严格，可能增加修订次数和 API 用量；
仍受已有任务轮数预算约束，不保证一定能达到该分数。

- score < 重做阈值：to_redraft。
- 重做阈值 <= score < 通过阈值：不能 complete；保留更严格的模型重做判断。
- score >= 通过阈值：只有模型也判断 complete 时才完成。
- 高分但模型判断 to_revise/to_redraft：保留模型判断，不自动放行。
- 评分缺失、无效、非有限或超出 0～1：记为无效评分，转为 to_revise，不能完成。

设置同时用于 Critic 提示词和程序检查，避免模型低分却输出 complete。
每个节点评审 JSON 记录 critic_policy、model_decision、threshold_adjusted、score_valid、
policy_reason 等信息，可在“每轮成果”的 Critic 评审中查看。
评分是模型评价，不是实验置信度，也不是物理正确性的保证。

## 设置如何生效

新建任务默认读取 config.yaml；网页选择写入该任务的 web_config.yaml 与 task.json，
不会改动基础 config.yaml。任务启动后固定，运行及生成总结期间不能修改。
完成后可以调整阈值，并用“补充条件并重新研究”创建下一轮任务。
续研未指定时继承上轮；部分覆盖时保留其它参数。每秒轮询不会重置尚未提交的修改。
服务器重启后恢复已完成任务的设置；旧 v8/v8.1/v8.2 任务从原 web_config.yaml 恢复。
“恢复配置默认值”同时恢复能力开关与 Critic 设置。

## 配置文件

如希望以后所有任务默认使用自己的值，合并到已有 config.yaml：

```yaml
critic:
  accept_threshold: 0.90
  redraft_threshold: 0.65
tools:
  retrieval_critic:
    enabled: true
    threshold: 0.60
```

不要用上述片段替换整个配置文件；tools 内其它搜索配置、API 环境变量名应保留。
命令行 run.py 也使用相同的阈值和检查。

## 检索评审与兼容变化

研究 Critic 与 RetrievalCritic 分开设置。检索评审仅在对应搜索启用且返回结果时运行。
成功评审后全部结果低于阈值，会返回空列表；修复了旧版“全部低分仍返回全部结果”的行为。
模型调用或分数解析失败时，沿用原有 fail-open 降级，返回原始结果并记录日志；
因此失败降级的检索结果不等同于已通过阈值评审。

移除了旧 CLI 在 revise 节点评分 >=0.75 且多次修改后强制完成的逻辑。
它可能绕过用户阈值和模型的未完成判断。网页和 CLI 现在使用一致的评分门槛。
默认的 0.85/0.60 从原来提示词中的参考分界变为明确的程序检查，可能改变旧任务的搜索行为。

## API

GET /api/capabilities 新增 critic_defaults 字段，不含 API 密钥。

POST /api/tasks 或 POST /api/tasks/<UUID>/continue 可以附带：

```json
{"critic_settings":{"accept_threshold":0.9,"redraft_threshold":0.65,
 "retrieval_threshold":0.6,"retrieval_enabled":true}}
```

字段允许部分覆盖。未知字段、非数字、NaN/Infinity、超范围值、不合法分界和非布尔开关
会返回 400，不启动工作进程。任务查询新增 critic_settings。

## 验证

31 项自动测试通过。覆盖阈值边界、低分不能完成、高分不自动放行、无效评分、
实际 Critic 调用使用新提示词与程序门槛、检索全部低分返回空列表、错误降级、
任务配置与续研继承、历史恢复，以及已有 v8.2 功能。
前端 JavaScript 在 DOM 环境连接本地 HTTP 服务与模拟研究进程，验证了默认值、
非法分界拒绝、运行/总结期间锁定、续研修改、轮询保留输入、历史切换和恢复默认值。
Python 编译与 git diff --check 通过。未调用真实模型/搜索 API；仍需真实 Windows 和
实际模型 API 的端到端验证。测试不验证物理研究结论。
