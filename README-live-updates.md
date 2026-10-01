> 本包包含网页功能，安装和启动请优先阅读 README-chat.md。以下为终端追加条件功能说明。

# PhysMaster 运行中追加条件补丁

本补丁基于你新上传的“PhysMaster-main - 联网加速 - 副本.zip”制作；不要用之前基于原版的补丁覆盖此联网版。它是覆盖补丁，不包含原项目的依赖、配置、密钥、历史输出或其他资源。

## 安装

先备份原项目的 run.py、core/supervisor.py、core/theoretician.py。
将本目录下的 run.py、send_update.py、core/、utils/、tests/ 合并到原项目根目录；覆盖同名文件，保留原目录中的其他文件。
在原 config.yaml 的现有 pipeline 节点下加入（不要创建第二个 pipeline）：

```yaml
pipeline:
  # 原有配置继续保留
  live_updates_enabled: true
```

没有启用时不创建收件箱，运行入口兼容原行为。求解和评审提示词现在会包含完整契约。

## 使用

终端 A：进入原项目根目录，正常启动：

```bash
python run.py --cfg_file config.yaml
```

看到 `[LiveUpdate] Inbox ready for task: outputs/query` 后，终端 B 也进入原项目根目录：

```bash
python send_update.py --task-dir outputs/query --text "离开火箭时相对地面静止，质量保持190 kg；先建立一维竖直下降模型。"
python send_update.py --task-dir outputs/query --text "追加气动加热估算，并对迎风面积进行敏感性分析。"
python send_update.py --task-dir outputs/query --text "纠正上一条：迎风面积取0.7平方米；之前其他要求保留。"
```

`outputs/query` 必须换成终端 A 打印的真实 task_dir。如果 output_path 被修改，也使用实际路径。长条件可以保存为 UTF-8 文本：

```bash
python send_update.py --task-dir outputs/query --file additional_conditions.txt
```

`Queued` 只表示已提交。终端 A 的 `[LiveUpdate] Applied revision ...` 和对应 `acks/` JSON 文件才表示已接收并应用到契约。

## 处理规则

1. 不再改写原 query.txt。每次更新保存在当前运行专属的收件箱中。
2. 当前并行求解、Critic、Promoter 完成后读取新条件；因此等待时间可能是一整轮，不保证秒级生效。
3. 把初始 query 与全部已接受的追加条件交给 Clarifier。保留不冲突要求，冲突时后提交的条件优先。完整原文也写入 live_updates，防止结构化时漏掉某条。
4. 旧契约和最佳轨迹归档在 revisions/revision_N/。旧节点输出文件保留，节点编号不重用；新的搜索不自动继承旧分数、完成标记和树记忆。
5. 重建全部子任务、刷新先验检索、新建搜索树；每次接收一批更新获得配置 max_rounds 的新预算。新一轮会重新计算，可能增加费用。
6. Supervisor、Theoretician、Critic 和最终 Summarizer 都收到最新契约。Theoretician 使用派发时的契约快照，避免同一批节点读到不同版本。
7. 更新解析失败会报错停止，不会确认该更新已应用。原始更新文件保留。

## 限制与后续方向

- 这是“运行中提交、轮间生效”，不打断已发出的模型调用或 Python 计算。
- 任务完成后不继续等待聊天，也不支持断点恢复。收件箱关闭后提交会被拒绝；若在结束边界竞态下看到 Queued 却没有回执，该条件尚未应用，需要用于新任务。只以 Applied/回执判断成功。
- 不要在同一个 task_dir 同时运行两个任务；原项目的输出和 contract.json 本就共享该目录。
- 历史目录供人工检查。新搜索不会自动复用旧结果；这能避免把更改参数前的结果当成当前已验证结论。
- “最大过载”等约束需要在物理代码中落实并检验；本补丁保证条件传递，无法保证模型生成的物理解答必然遵守全部约束。
- 若后续需要聊天界面，可用前端/API 调用 submit_update。真正的即时中断需进一步改 utils/llm_client.py 的工具调用循环、工作进程取消和长计算执行器；仅增加 input() 或监听线程不够。

## 验证

```bash
python -m unittest discover -s tests -p test_live_updates.py -v
```

已完成 4 项离线测试：原子提交/顺序/回执/不同运行隔离；空消息拒绝；契约共享引用、重新规划、预算、旧轨迹归档及失败不确认；执行过程中到达的新条件阻止旧契约提前完成。修改后的 Python 文件通过编译检查。未调用真实 LLM API，未运行完整 PhysMaster 科研任务；测试不证明物理输出质量。

## 修改位置

- utils/live_updates.py：原子文件写入、按运行隔离的条件队列和回执。
- send_update.py：另一个终端提交自然语言条件。
- run.py：配置开关，构造 Clarifier 重规划回调，关闭收件箱，最终汇总共享最新契约。
- core/supervisor.py：轮间检查条件，重新规划，归档旧版本，清除旧树的评分和完成状态，Critic 接收当前契约。
- core/theoretician.py：真正把完整任务契约加入求解提示词；原源码中读了 contract.json 但没有在 solve 中使用。

## 联网加速版专属适配

- 保留已有网页搜索、CODATA、RetrievalCritic、检索预算及模型分工调用；未修改这些工具的实现。原配置仍由你本地保留。
- 每个新契约版本重置 Supervisor 的检索计数、query/URL 去重记录，否则原先结果被丢弃后可能无法重新获取依据。检索预算按版本重新分配，因此更新可能增加搜索调用费用。
- Critic/Promoter 的探索与收敛阶段按当前契约版本的轮数判断，不因全局累计轮数错误地一开始进入收敛阶段。
- 启用追加条件时关闭原代码的两条捷径：修改节点 reward>=0.75 且修改次数>=2 时强制接受；失败累计>=4 时强制推进到下个子任务。关闭功能时保留原来的行为。
- 只提供需要覆盖的文件；联网工具、依赖和配置无需重新替换。
