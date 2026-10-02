# PhysMaster Feishu Bot

Bring PhysMaster's physics-solving capabilities to Feishu (Lark). Users send physics problems in direct messages or group chats, and the bot runs the full pipeline asynchronously and pushes results back.

Uses Feishu's officially recommended **long-connection (WebSocket)** mode — no public IP, no domain, no ngrok required.

**[中文文档](README_CN.md)**

## Quick Start

### 1. Create a Feishu App

1. Go to the [Feishu Open Platform](https://open.feishu.cn/app) and create a **custom enterprise app**
2. Note down the **App ID** and **App Secret**

### 2. Configure Permissions

In the app's "Permissions" page, request the following scopes:

| Permission | Purpose |
|---|---|
| `im:message` | Read and send messages in chats |
| `im:message:send_as_bot` | Send messages as the bot |
| `im:message.group_at_msg` | Receive messages when @mentioned in groups |
| `im:message.p2p_msg` | Receive direct messages from users |

### 3. Configure Event Subscription (Long Connection)

1. Go to "Events & Callbacks" → "Event Configuration"
2. Set subscription mode to **"Receive events via long connection"**
3. Add event: `im.message.receive_v1` (Receive messages)

> In long-connection mode, there is no need to configure a Request URL, Verification Token, or Encrypt Key.

### 4. Configure the Bot

```bash
cd feishu
cp config_example.yaml config.yaml
```

Edit `config.yaml` — only App ID and App Secret are needed:

```yaml
feishu:
  app_id: "cli_xxxx"          # your App ID
  app_secret: "xxxx"          # your App Secret

server:
  max_workers: 2              # concurrent solve tasks

physmaster:
  config_path: "config.yaml"  # path to the main PhysMaster config.yaml
```

### 5. Install Dependencies

```bash
pip install lark-oapi pyyaml
```

### 6. Start

```bash
cd feishu
python bot.py
```

When you see `connected to wss://...` in the logs, the connection is established. No port configuration or public address exposure needed.

### 7. Enable the Bot

Publish an app version in the Feishu Open Platform under "App Release", or enable it directly in your test organization.

## Usage

- **Direct message**: Send physics problem text directly to the bot
- **Group chat**: @Bot followed by the physics problem text

The bot immediately replies "solving...", then pushes progress updates during the pipeline, and sends the final summary when done (typically 5–30 minutes).

## Architecture

```
User message ──→ Feishu server ──WebSocket──→ lark-oapi SDK
                                                    │
                                              bot.py (event handler)
                                                    │
                                      ┌─────────────┴──────────────┐
                                      │  Reply "solving..."         │
                                      │  Submit to ThreadPoolExecutor│
                                      └─────────────┬──────────────┘
                                                    │ (background thread)
                                              worker.py
                                                    │
                                      Clarifier → MCTS → Summarizer
                                                    │
                                              Push result to Feishu
```

## Files

| File | Description |
|---|---|
| `bot.py` | Long-connection event handler and message sending |
| `worker.py` | PhysMaster pipeline wrapper |
| `config_example.yaml` | Configuration template |
| `config.yaml` | Actual config (not committed to git) |

## Notes

- `config.yaml` contains secrets — do not commit to version control (excluded in `.gitignore`)
- `max_workers` controls concurrent solve tasks; each task is resource-heavy, recommended value is 1–2
- Feishu has a message length limit; long summaries are auto-truncated, full results are available in `summary.md` on the server
- Event handlers must return within 3 seconds (current design: immediate reply + background thread, well within budget)
- Each app supports up to 50 long connections (only 1 is needed)
