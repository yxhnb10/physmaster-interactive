import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import time

import yaml
from openai import OpenAI
from utils.runtime_timing import measure_operation


def _load_llm_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """Read the 'llm' section from config.yaml. Requires base_url,
    api_key, and model to be present."""
    default_path = Path(__file__).resolve().parents[1] / "config.yaml"
    path = Path(config_path) if config_path else default_path
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    llm_config = config.get("llm", {})
    if not isinstance(llm_config, dict):
        raise ValueError("`llm` section in config.yaml must be a mapping.")

    missing = [key for key in ("base_url", "api_key", "model") if not llm_config.get(key)]
    if missing:
        raise ValueError(f"Missing llm config fields: {', '.join(missing)}")

    return llm_config


class LLMClient:
    """Thin wrapper around the OpenAI-compatible chat API. Handles both
    plain completions and multi-turn tool-call loops."""

    def __init__(self, config_path=None):
        llm_config = _load_llm_config(config_path)
        self.model = str(llm_config["model"])
        self.model_overrides = llm_config.get("model_overrides", {}) or {}
        self.client = OpenAI(
            api_key=str(llm_config["api_key"]),
            base_url=str(llm_config["base_url"]),
            timeout=float(llm_config.get("timeout", 120)),
            max_retries=3,
        )

    def _resolve_model(self, role: Optional[str], explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        if role and role in self.model_overrides:
            return self.model_overrides[role]
        return self.model

    def call_without_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        model_name: Optional[str] = None,
        role: Optional[str] = None,
    ) -> str:
        """Single-turn chat completion with no tool definitions."""
        actual_model = self._resolve_model(role, model_name)
        t0 = time.time()
        print(
            f"[LLM] (no tools) role={role} model={actual_model} "
            f"prompt_chars={len(system_prompt) + len(user_prompt)}",
            flush=True,
        )
        with measure_operation('llm:' + (role or 'default')):
            completion = self.client.chat.completions.create(
                model=actual_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        dt = time.time() - t0
        print(f"[LLM] (no tools) done dt={dt:.1f}s", flush=True)
        return (completion.choices[0].message.content or "").strip()

    def call_with_tools(
        self,
        system_prompt,
        user_prompt,
        tools=None,
        tool_functions=None,
        model_name=None,
        role: Optional[str] = None,
        max_tool_calls: int = 8,   # 20 -> 8
    ) -> str:
        actual_model = self._resolve_model(role, model_name)
        t0 = time.time()
        tools = tools or []
        tool_functions = tool_functions or {}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for iteration in range(max_tool_calls):
            t_llm = time.time()
            print(f"[LLM] iter={iteration} model={model_name or self.model}", flush=True)
            with measure_operation('llm:' + (role or 'default')):
                completion = self.client.chat.completions.create(
                    model=actual_model,
                    messages=messages,
                    tools=tools if tools else None,
                )
            llm_dt = time.time() - t_llm
            msg = completion.choices[0].message
            n_tools = len(msg.tool_calls or [])
            print(f"[LLM] iter={iteration} llm_dt={llm_dt:.1f}s tools={n_tools} "
                  f"finish={completion.choices[0].finish_reason} elapsed={time.time()-t0:.1f}s",
                  flush=True)

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (msg.tool_calls or [])
                ] or None,
            })

            tool_call_list = msg.tool_calls or []
            if not tool_call_list:
                if completion.choices[0].finish_reason == "stop":
                    print(f"[LLM] done total={time.time()-t0:.1f}s", flush=True)
                    return (msg.content or "").strip()
                continue

            for tc in tool_call_list:
                t_tool = time.time()
                raw_args = tc.function.arguments or "{}"
                try:
                    call_args = json.loads(raw_args)
                except Exception:
                    call_args = {"_raw": raw_args}
                try:
                    fn = tool_functions.get(tc.function.name)
                    if fn is None:
                        result = f"[tool:{tc.function.name}] not implemented"
                    else:
                        with measure_operation('tool:' + tc.function.name):
                            result = fn(**call_args) if isinstance(call_args, dict) else fn(call_args)
                except Exception:
                    result = traceback.format_exc()
                print(f"[LLM]   tool={tc.function.name} dt={time.time()-t_tool:.1f}s", flush=True)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": str(result),
                })

        print(f"[LLM] hit max_tool_calls={max_tool_calls} elapsed={time.time()-t0:.1f}s", flush=True)
        for message in reversed(messages):
            if message.get("role") == "assistant" and message.get("content"):
                return str(message["content"]).strip()
        return ""


_DEFAULT_CLIENT: "LLMClient | None" = None


def _get_default_client(config_path: str | Path | None = None) -> "LLMClient":
    """Lazy singleton. The first caller determines the config path."""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient(config_path=config_path)
    return _DEFAULT_CLIENT


def call_model_without_tools(
    system_prompt: str,
    user_prompt: str,
    model_name: Optional[str] = None,
    role: Optional[str] = None,
    config_path: str | Path | None = None,
) -> str:
    """Convenience function for a single LLM call with no tools."""
    client = _get_default_client(config_path=config_path)
    return client.call_without_tools(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_name=model_name,
        role=role,
    )


def call_model(
    system_prompt: str,
    user_prompt: str,
    tools: list | None = None,
    tool_functions: Optional[Dict[str, Callable]] = None,
    model_name: Optional[str] = None,
    role: Optional[str] = None,
    max_tool_calls: int = 8,
    config_path: str | Path | None = None,
) -> str:
    """Convenience function for an LLM call with tool support."""
    client = _get_default_client(config_path=config_path)
    return client.call_with_tools(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tools=tools,
        tool_functions=tool_functions,
        model_name=model_name,
        role=role,
        max_tool_calls=max_tool_calls,
    )
