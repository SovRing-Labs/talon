"""OpenAI-compatible model provider client for the Talon Agent Engine.

Resolves a lane (lane-catalog.db id or model id) to (base_url, model, api_key) and runs
/v1/chat/completions. Rewritten 2026-09-26 (TALON-COMPLETE, bugs #62/#64):
  - catalog provider -> its real endpoint (zen -> opencode.ai/zen/v1, not OpenRouter)
  - the lane prefix is stripped: `openrouter/nvidia/x:free` is sent as `nvidia/x:free`
  - keys come from engine.config.resolve_key (env -> talon keys -> opencode auth -> jsonc)
  - `ollama/<m>` / `local/<m>` reach the local server; `http://host/v1::model` is a direct URL
  - an unknown lane is an error, never a silent fall-through to openrouter.ai
Also: a User-Agent (the zen edge returns 403 without one), retry with backoff on 429/5xx,
usage returned as `_usage`, and hermes-style `<tool_call>{...}</tool_call>` text parsed into
tool_calls for vLLM/llama.cpp servers that do not emit native tool calls.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from . import config

log = logging.getLogger("talon.engine.providers")

AI_STACK_ROOT = Path(os.environ.get("AI_STACK_ROOT", "."))
LANE_CATALOG_DB = AI_STACK_ROOT / "control-center" / "lane-catalog.db"
USER_AGENT = "talon-engine/1.0 (sovereign-fleet)"


class LaneError(ValueError):
    """The lane cannot be resolved to an endpoint (unknown lane, no key)."""


def _gpu_url() -> str:
    return os.environ.get("WORKER_GPU_URL") or config.provider_base_url("vllm-remote") or "http://localhost:8000/v1"


# catalog provider id -> base URL (callables are evaluated late)
PROVIDER_BASE_URLS: dict[str, Any] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openrouter-alt": "https://openrouter.ai/api/v1",
    "openrouter-free": "https://openrouter.ai/api/v1",
    "zen-free": "https://opencode.ai/zen/v1",
    "zen-paid": "https://opencode.ai/zen/v1",
    "opencode": "https://opencode.ai/zen/v1",
    "groq": "https://api.groq.com/openai/v1",
    "groq-direct": "https://api.groq.com/openai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "mistral": "https://api.mistral.ai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "vllm-remote": _gpu_url,
    "local-gpu": _gpu_url,
    "local": lambda: os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/v1"),
    "ollama": lambda: os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/v1"),
}
# first path segment of a lane/model id that names the lane, not the upstream model
LANE_PREFIXES = {"openrouter", "openrouter-free", "openrouter-alt", "opencode", "vllm-remote", "local-gpu",
                 "groq-direct", "groq", "mistral", "google", "ollama", "local", "cerebras"}
KEYLESS = {"local", "ollama"}
# Provider policy, verified live 2026-09-26: zen's FREE tier answers 403 FreeTierError "can only be
# used from within OpenCode". Talon does not impersonate opencode; these lanes are refused up front.
OPENCODE_ONLY = {"zen-free"}


def _base(provider_id: str) -> Optional[str]:
    b = PROVIDER_BASE_URLS.get(provider_id)
    return b() if callable(b) else b


def upstream_model(model_id: str) -> str:
    head, _, rest = model_id.partition("/")
    return rest if rest and head in LANE_PREFIXES else model_id


def resolve_lane(lane_or_model: str) -> tuple[str, str, str]:
    """(base_url, upstream_model, api_key). Raises LaneError when it cannot be resolved."""
    lane = (lane_or_model or "").strip()
    if not lane:
        raise LaneError("no lane given (use -m <lane id from lane-catalog.db>, ollama/<model>, or http://host/v1::model)")

    if lane.startswith(("http://", "https://")):
        if "::" in lane:
            base, model = lane.split("::", 1)
        else:  # legacy form: last path segment is the model (model ids with '/' need '::')
            base, _, model = lane.rpartition("/")
        key = os.environ.get("TALON_API_KEY") or os.environ.get("OPENAI_API_KEY") or "local"
        return base.rstrip("/"), model, key

    head = lane.split("/", 1)[0]
    if head in KEYLESS and "/" in lane:
        return _base(head), lane.split("/", 1)[1], "local"

    if LANE_CATALOG_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{LANE_CATALOG_DB}?mode=ro", uri=True, timeout=5.0)
            row = conn.execute(
                "SELECT l.model_id, k.key_env_var, k.provider_id FROM lanes l "
                "JOIN api_keys k ON l.api_key_id = k.id WHERE l.id = ? OR l.model_id = ? "
                "ORDER BY (l.status = 'live') DESC LIMIT 1", (lane, lane)).fetchone()
            conn.close()
        except sqlite3.Error as e:
            raise LaneError(f"lane-catalog.db unreadable: {e}") from e
        if row:
            model_id, env_var, provider_id = row
            if provider_id in OPENCODE_ONLY:
                raise LaneError(f"lane {lane}: provider '{provider_id}' is OpenCode-only (zen free tier refuses "
                                f"other clients); use an openrouter/openrouter-free, groq, local or GPU lane")
            base = _base(provider_id)
            if not base:
                raise LaneError(f"lane {lane}: provider '{provider_id}' has no known endpoint")
            key = "local" if provider_id in KEYLESS else config.resolve_key(provider_id, env_var)
            if not key:
                raise LaneError(f"lane {lane}: no API key for provider '{provider_id}' "
                                f"(env {env_var}, ~/.config/talon/keys.json, or opencode auth.json)")
            return base, upstream_model(model_id), key

    # not in the catalog: a provider-prefixed id we know how to reach
    base = _base(head)
    if base and "/" in lane:
        key = config.resolve_key(head)
        if key:
            return base, upstream_model(lane), key
    raise LaneError(f"unknown lane '{lane}' (not in lane-catalog.db; no endpoint/key for '{head}')")


_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    calls = []
    for i, m in enumerate(_TOOL_TAG.finditer(content or "")):
        try:
            obj = json.loads(m.group(1))
        except Exception:
            continue
        args = obj.get("arguments", obj.get("parameters", {}))
        calls.append({"id": f"call_text_{i}", "type": "function",
                      "function": {"name": obj.get("name", ""),
                                   "arguments": args if isinstance(args, str) else json.dumps(args)}})
    return calls


class OpenAICompatibleClient:
    """Client for OpenAI-compatible /v1/chat/completions endpoints."""

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "", lane: str = ""):
        if not (base_url and model):
            raise LaneError("OpenAICompatibleClient needs base_url and model (use from_lane)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.lane = lane or model

    @classmethod
    def from_lane(cls, lane_or_model: str) -> "OpenAICompatibleClient":
        base_url, model_name, api_key = resolve_lane(lane_or_model)
        return cls(base_url=base_url, model=model_name, api_key=api_key, lane=lane_or_model)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        stream: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: int = 180,
        retries: int = 4,
    ) -> dict[str, Any]:
        """Send one chat completion. Returns the assistant message (+ `_usage`)."""
        if os.environ.get("TALON_MOCK") == "1":
            return self._mock_completion(messages, tools)

        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                   "HTTP-Referer": "https://sovereign-fleet.local", "X-Title": "Talon-Agent-Engine"}
        if self.api_key and self.api_key != "local":
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = json.dumps(payload).encode("utf-8")
        endpoint = f"{self.base_url}/chat/completions"

        last_err: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("error"):
                    err = data["error"]
                    code = err.get("code") if isinstance(err, dict) else None
                    last_err = RuntimeError(f"Model API error ({code}) from {self.lane}: {str(err)[:300]}")
                    # OpenRouter reports upstream overload as HTTP 200 + {"error": {"code": 503}}
                    if code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                        wait = min(30, 2 ** attempt * 3)
                        log.warning("in-body %s from %s, retry %d/%d in %ss", code, self.lane, attempt + 1, retries - 1, wait)
                        time.sleep(wait)
                        continue
                    raise last_err
                msg = dict((data.get("choices") or [{}])[0].get("message") or {"role": "assistant", "content": ""})
                if not msg.get("tool_calls") and "<tool_call>" in (msg.get("content") or ""):
                    parsed = _parse_text_tool_calls(msg["content"])
                    if parsed:
                        msg["tool_calls"] = parsed
                        msg["content"] = _TOOL_TAG.sub("", msg["content"]).strip()
                msg["_usage"] = data.get("usage") or {}
                return msg
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")[:400]
                last_err = RuntimeError(f"Model API error ({e.code}) from {self.lane}: {err_body}")
                if e.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                    wait = min(30, 2 ** attempt * 3)
                    log.warning("HTTP %d from %s, retry %d/%d in %ss", e.code, self.lane, attempt + 1, retries - 1, wait)
                    time.sleep(wait)
                    continue
                raise last_err from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = RuntimeError(f"endpoint unreachable for {self.lane} ({endpoint}): {e}")
                if attempt + 1 < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise last_err from e
        raise last_err or RuntimeError("completion failed")

    def _mock_completion(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]
    ) -> dict[str, Any]:
        """Deterministic mock completion for hermetic tests (TALON_MOCK=1 only)."""
        last = messages[-1]
        if last.get("role") == "tool":
            return {"role": "assistant", "content": f"Task completed.\nOutput:\n{last.get('content', '')}",
                    "_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        text = last.get("content", "")
        if "echo " in text or "run bash" in text.lower():
            cmd = "echo " + text.split("echo ", 1)[1].strip().strip("'\"") if "echo " in text else "echo hi"
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "call_mock_1", "type": "function",
                                    "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}],
                    "_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        return {"role": "assistant", "content": f"Acknowledged: {text}",
                "_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
