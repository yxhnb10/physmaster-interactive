# PhysMaster 飞书机器人

将 PhysMaster 的物理求解能力接入飞书，用户在群聊/私聊中发送物理题，Bot 异步执行求解并推送结果。

采用飞书官方推荐的**长连接（WebSocket）**模式接收事件，无需公网 IP、无需域名、无需 ngrok。

## 快速开始

### 1. 创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，创建一个**企业自建应用**
2. 记录 **App ID** 和 **App Secret**

### 2. 配置权限

在应用的「权限管理」页面，申请以下权限：

| 权限 | 说明 |
|------|------|
| `im:message` | 获取与发送单聊、群组消息 |
| `im:message:send_as_bot` | 以应用身份发送消息 |
| `im:message.group_at_msg` | 接收群聊中 @ 机器人的消息 |
| `im:message.p2p_msg` | 接收用户发给机器人的单聊消息 |

### 3. 配置事件订阅（长连接模式）

1. 进入「事件与回调」→「事件配置」
2. 订阅方式选择「**使用长连接接收事件**」
3. 添加事件：`im.message.receive_v1`（接收消息）

> 长连接模式下无需配置请求地址（Request URL），也不需要 Verification Token 和 Encrypt Key。

### 4. 配置 Bot

```bash
cd feishu
cp config_example.yaml config.yaml
```

编辑 `config.yaml`，只需填入 App ID 和 App Secret：

```yaml
feishu:
  app_id: "cli_xxxx"          # 替换为你的 App ID
  app_secret: "xxxx"          # 替换为你的 App Secret

server:
  max_workers: 2              # 并发求解任务数

physmaster:
  config_path: "config.yaml"  # 指向主项目 config.yaml
```

### 5. 安装依赖

```bash
pip install lark-oapi pyyaml
```

### 6. 启动

```bash
cd feishu
python bot.py
```

看到 `connected to wss://...` 日志即表示连接成功。无需配置端口或暴露公网地址。

### 7. 启用机器人

在飞书开放平台「应用发布」中发布应用版本，或在测试企业中直接启用。

## 使用方式

- **私聊**：直接给 Bot 发送物理题文字
- **群聊**：@Bot 后跟物理题文字

Bot 会立即回复「正在求解…」，求解完成后（通常 5-30 分钟）主动推送 summary 结果。

## 架构

```
用户发消息 ──→ 飞书服务器 ──WebSocket──→ lark-oapi SDK
                                              │
                                        bot.py (事件处理)
                                              │
                                ┌─────────────┴──────────────┐
                                │  立即回复 "正在求解…"         │
                                │  提交到 ThreadPoolExecutor   │
                                └─────────────┬──────────────┘
                                              │ (后台线程)
                                        worker.py
                                              │
                                Clarifier → MCTS → Summarizer
                                              │
                                        结果推送回飞书
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `bot.py` | 长连接事件处理、消息发送 |
| `worker.py` | 调用 PhysMaster pipeline 的封装 |
| `config_example.yaml` | 配置模板 |
| `config.yaml` | 实际配置（不提交到 git） |

## 注意事项

- `config.yaml` 包含密钥，请勿提交到版本控制（已在 `.gitignore` 中排除）
- `max_workers` 控制并发求解数，每个任务消耗较多内存和 API 资源，建议设为 1-2
- 飞书消息有长度限制，超长 summary 会自动截断，完整结果请查看服务器上的 `summary.md`
- 事件处理函数需在 3 秒内返回（当前架构：立即回复 + 后台线程，满足要求）
- 每个应用最多 50 个长连接（只需 1 个即可）
