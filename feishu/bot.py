"""
Feishu Bot -- long-connection (WebSocket) mode

Uses the official lark-oapi SDK's WebSocket client to receive events.
No public IP, no ngrok, no FastAPI needed.  The SDK handles connection
management, reconnection, and heartbeat automatically.

Flow:
  1. SDK establishes a WebSocket connection to Feishu's gateway
  2. When a message arrives, _on_message_receive is called
  3. Bot replies "正在求解…" immediately
  4. Pipeline runs in a background ThreadPoolExecutor
  5. Result is pushed back via the lark-oapi client
"""

import json
import logging
import re
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Dict

import yaml

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from worker import solve

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("feishu-bot")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_CFG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_feishu_config() -> Dict[str, Any]:
    if not _CFG_PATH.exists():
        raise FileNotFoundError(
            f"Feishu config not found at {_CFG_PATH}. "
            "Copy config_example.yaml -> config.yaml and fill in your credentials."
        )
    with open(_CFG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_CFG = _load_feishu_config()
FEISHU_CFG = _CFG["feishu"]
SERVER_CFG = _CFG.get("server", {})
PHYSMASTER_CFG = _CFG.get("physmaster", {})

APP_ID: str = FEISHU_CFG["app_id"]
APP_SECRET: str = FEISHU_CFG["app_secret"]

MAX_WORKERS: int = SERVER_CFG.get("max_workers", 2)
PM_CONFIG_PATH: str = PHYSMASTER_CFG.get("config_path", "config.yaml")

# Feishu has a ~30 KB limit per message; keep a conservative plain-text cap
MAX_MSG_LEN = 25_000

# ---------------------------------------------------------------------------
# Lark SDK client (handles token management automatically)
# ---------------------------------------------------------------------------
_lark_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

# ---------------------------------------------------------------------------
# Feishu messaging helpers (via lark-oapi SDK, no raw requests needed)
# ---------------------------------------------------------------------------

def _reply_text(message_id: str, text: str) -> None:
    """Reply to a specific message with plain text."""
    body = ReplyMessageRequestBody.builder() \
        .content(json.dumps({"text": text})) \
        .msg_type("text") \
        .build()
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(body) \
        .build()
    resp = _lark_client.im.v1.message.reply(req)
    if not resp.success():
        logger.error("reply failed: code=%d msg=%s", resp.code, resp.msg)


def _send_text(chat_id: str, text: str) -> None:
    """Proactively send a text message to a chat."""
    body = CreateMessageRequestBody.builder() \
        .receive_id(chat_id) \
        .content(json.dumps({"text": text})) \
        .msg_type("text") \
        .build()
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(body) \
        .build()
    resp = _lark_client.im.v1.message.create(req)
    if not resp.success():
        logger.error("send failed: code=%d msg=%s", resp.code, resp.msg)


def _truncate(text: str, limit: int = MAX_MSG_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（结果过长，已截断。完整内容请查看服务器上的 summary.md）"


# ---------------------------------------------------------------------------
# Message de-duplication
# ---------------------------------------------------------------------------
_DEDUP_MAX = 512
_seen_ids: OrderedDict[str, float] = OrderedDict()
_seen_lock = Lock()


def _is_duplicate(message_id: str) -> bool:
    with _seen_lock:
        if message_id in _seen_ids:
            return True
        _seen_ids[message_id] = time.time()
        while len(_seen_ids) > _DEDUP_MAX:
            _seen_ids.popitem(last=False)
        return False


# ---------------------------------------------------------------------------
# Background solver
# ---------------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)


def _background_solve(message_id: str, chat_id: str, query_text: str) -> None:
    def _progress(msg: str):
        """Send a progress update to the chat."""
        _send_text(chat_id, msg)

    try:
        logger.info("Start solving for message %s", message_id)
        result = solve(query_text, config_path=PM_CONFIG_PATH, progress_cb=_progress)
        summary = result.get("summary", "（未生成 summary）")
        task_name = result.get("task_name", "unknown")
        header = f"[{task_name}] 求解完成：\n\n"
        _send_text(chat_id, _truncate(header + summary))
        logger.info("Result sent for message %s (task=%s)", message_id, task_name)
    except Exception:
        logger.exception("Solve failed for message %s", message_id)
        _send_text(chat_id, "求解过程出错，请查看服务端日志。")


# ---------------------------------------------------------------------------
# Event handler — called by the SDK when im.message.receive_v1 fires
# ---------------------------------------------------------------------------

def _on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """Called by the SDK when im.message.receive_v1 fires.
    Must return within 3 seconds — heavy work goes to the thread pool."""
    message = data.event.message
    message_id = message.message_id
    chat_id = message.chat_id
    msg_type = message.message_type

    # Only handle plain text
    if msg_type != "text":
        _reply_text(message_id, "目前仅支持文本消息，请直接发送物理题文字。")
        return

    # Extract text
    try:
        content = json.loads(message.content)
        query_text: str = content.get("text", "").strip()
    except (json.JSONDecodeError, AttributeError):
        query_text = ""

    if not query_text:
        return

    # Strip @mentions
    query_text = re.sub(r"@_user_\d+\s*", "", query_text).strip()
    if not query_text:
        return

    # De-duplicate (in case the SDK retries)
    if _is_duplicate(message_id):
        logger.info("Duplicate message %s, skipping", message_id)
        return

    logger.info("Received query (msg=%s): %s", message_id, query_text[:80])

    # Acknowledge immediately (well within the 3-second budget)
    _reply_text(message_id, "收到！正在求解，请稍候（通常需要 5-30 分钟）…")

    # Submit to background worker
    _executor.submit(_background_solve, message_id, chat_id, query_text)


# ---------------------------------------------------------------------------
# Build event dispatcher & WebSocket client
# ---------------------------------------------------------------------------
# Long-connection mode: verification_token and encrypt_key are not needed.
# The SDK handles authentication at connection time automatically.
_event_handler = lark.EventDispatcherHandler.builder("", "") \
    .register_p2_im_message_receive_v1(_on_message_receive) \
    .build()

_ws_client = lark.ws.Client(
    APP_ID,
    APP_SECRET,
    event_handler=_event_handler,
    log_level=lark.LogLevel.DEBUG,
)

# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Feishu bot (long-connection mode, max_workers=%d)", MAX_WORKERS)
    logger.info("Press Ctrl+C to stop.")
    try:
        _ws_client.start()  # blocks the main thread
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
        _executor.shutdown(wait=False)
        sys.exit(0)
