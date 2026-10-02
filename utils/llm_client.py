import json
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import time

import yaml
from openai import OpenAI
from utils.runtime_timing import measure_operation


class LLMResponseError(RuntimeError):
    """A response that cannot be used as a completed answer."""

    def __init__(self, reason, model, finish_reason, round_index, partial_response=""):
        self.diagnostic = dict(reason=reason, model=model,
                               finish_reason=finish_reason, round=round_index)
        self.partial_response = partial_response
        super().__init__(f"LLM final response failed: {reason}; model={model}; "
                         f"finish={finish_reason}; round={round_index}")


def _final_text(choice, model, round_index):
    content = (choice.message.content or "").strip()
    if choice.message.tool_calls:
        reason = "unexpected_tool_calls"
    elif choice.finish_reason != "stop":
        reason = "truncated_response" if choice.finish_reason == "length" else "incomplete_response"
    elif not content:
        reason = "empty_response"
    else:
        return content
    raise LLMResponseError(reason, model, choice.finish_reason, round_index, content)


FINAL_RESPONSE_PROMPT = (
    "The model interaction budget ends with this response. Tools are disabled. "
    "Return a non-empty final answer in the exact format required by the system prompt. "
    "Use only results already obtained; do not claim new execution or invent files, "
    "parameter evidence, tests, or results. If JSON is required, return a complete JSON "
    "object and explicitly describe missing deliverables and unverified results. "
    "Report primary parameters and file/symbol evidence only when supported by the "
    "task contract and actual tool results. Do not return a plan or request another tool."
)


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
        return _final_text(completion.choices[0], actual_model, 1)

    def call_with_tools(
        self,
        system_prompt,
        user_prompt,
        tools=None,
        tool_functions=None,
        model_name=None,
        role: Optional[str] = None,
        max_tool_calls: int = 8,
    ) -> str:
        # Legacy name: this limits model rounds, not the number of tool calls.
        # Reserve the last round for delivery without adding to the API budget.
        if isinstance(max_tool_calls, bool) or not isinstance(max_tool_calls, int) or max_tool_calls < 1:
            raise ValueError("max_tool_calls must be a positive integer model-round budget")
        actual_model = self._resolve_model(role, model_name)
        t0 = time.time()
        tools = tools or []
        tool_functions = tool_functions or {}
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for iteration in range(max_tool_calls):
            final_round = iteration == max_tool_calls - 1
            if final_round:
                messages.append({"role": "user", "content": FINAL_RESPONSE_PROMPT})
            t_llm = time.time()
            print(f"[LLM] iter={iteration} model={model_name or self.model}", flush=True)
            with measure_operation('llm:' + (role or 'default')):
                completion = self.client.chat.completions.create(
                    model=actual_model,
                    messages=messages,
                    tools=tools if tools and not final_round else None,
                )
            llm_dt = time.time() - t_llm
            msg = completion.choices[0].message
            n_tools = len(msg.tool_calls or [])
            print(f"[LLM] iter={iteration} llm_dt={llm_dt:.1f}s tools={n_tools} "
                  f"finish={completion.choices[0].finish_reason} elapsed={time.time()-t0:.1f}s",
                  flush=True)

            # Never execute tools requested in the reserved delivery round, or
            # treat truncated/empty content as a successful final answer.
            if final_round or not msg.tool_calls:
                response = _final_text(completion.choices[0], actual_model, iteration + 1)
                print(f"[LLM] done total={time.time()-t0:.1f}s", flush=True)
                return response
            if completion.choices[0].finish_reason == "length":
                raise LLMResponseError("truncated_tool_request", actual_model,
                                       "length", iteration + 1, msg.content or "")

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
