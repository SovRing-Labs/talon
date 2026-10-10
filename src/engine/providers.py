"""OpenAI-compatible model provider client for the Talon Agent Engine.

Resolves a lane (lane-catalog.db id or model id) to (base_url, model, api_key) and runs
/v1/chat/completions. Rewritten 2026-09-26 (bugs #62/#64):
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
import threading
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


# Vertex AI (Gemini) via its OpenAI-compatible endpoint, authenticated with Google
# application-default credentials (the same ADC opencode's @ai-sdk/google-vertex uses).
# Lane form: `<opencode provider>/<model>`, e.g. `vertexii/gemini-2.5-flash`; project and
# location come from that provider's opencode.jsonc options (env VERTEX_PROJECT /
# VERTEX_LOCATION override). Added 2026-10-02: the author's Vertex models were reachable from opencode
# only. Vertex's Anthropic models (vertex-claude) use a different API and are not served here.
VERTEX_PROVIDERS = {"vertexii", "vertex", "google-vertex"}
_ADC: dict[str, Any] = {}


def vertex_settings(provider: str) -> tuple[str, str]:
    opts = ((config.opencode_config().get("provider") or {}).get(provider) or {}).get("options") or {}
    project = os.environ.get("VERTEX_PROJECT") or opts.get("project") or ""
    location = os.environ.get("VERTEX_LOCATION") or opts.get("location") or "global"
    return project, location


def vertex_models(provider: str = "vertexii") -> list[str]:
    block = (config.opencode_config().get("provider") or {}).get(provider) or {}
    return list((block.get("models") or {}).keys())


def adc_token() -> str:
    """A fresh ADC access token (cached creds, refreshed when expired). '' when ADC is absent."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        return ""
    creds = _ADC.get("creds")
    try:
        if creds is None:
            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            _ADC["creds"] = creds
        if not creds.valid:
            creds.refresh(google.auth.transport.requests.Request())
        return creds.token or ""
    except Exception as e:  # no ADC / revoked: the lane reports it instead of a bare 401
        log.warning("vertex: ADC unavailable: %s", e)
        return ""


def _vertex_lane(provider: str, model: str) -> tuple[str, str, str]:
    project, location = vertex_settings(provider)
    if not project:
        raise LaneError(f"lane {provider}/{model}: no Vertex project (opencode.jsonc provider.{provider}.options.project or VERTEX_PROJECT)")
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    token = adc_token()
    if not token:
        raise LaneError(f"lane {provider}/{model}: no Google application-default credentials (gcloud auth application-default login)")
    base = f"https://{host}/v1/projects/{project}/locations/{location}/endpoints/openapi"
    return base, model if "/" in model else f"google/{model}", token


_LIMITS: dict[str, tuple[Optional[int], Optional[int]]] = {}


def lane_limits(lane: str) -> tuple[Optional[int], Optional[int]]:
    """(context, max_output) for a lane, from where bin/model-limits recorded them (2026-10-02):
    lane-catalog.db models.ctx_len/max_output for catalog lanes, opencode.jsonc limit{} for
    vertex lanes. (None, None) when unknown — callers keep their default."""
    if lane in _LIMITS:
        return _LIMITS[lane]
    out: tuple[Optional[int], Optional[int]] = (None, None)
    head, _, model = lane.partition("/")
    if head in VERTEX_PROVIDERS:
        spec = (((config.opencode_config().get("provider") or {}).get(head) or {}).get("models") or {}).get(model) or {}
        lim = spec.get("limit") or {}
        out = (lim.get("context") or 1048576, lim.get("output") or 65536)
    elif LANE_CATALOG_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{LANE_CATALOG_DB}?mode=ro", uri=True, timeout=5.0)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(models)")}
            if "max_output" in cols:
                row = conn.execute("SELECT m.ctx_len, m.max_output FROM lanes l JOIN models m ON m.id = l.model_id "
                                   "WHERE l.id = ? OR l.model_id = ? LIMIT 1", (lane, lane)).fetchone()
                if row:
                    out = (row[0], row[1])
            conn.close()
        except sqlite3.Error:
            pass
    _LIMITS[lane] = out
    return out


# Paid spend (TALON-VERTEX-OPS-0, J 10-04: Vertex sessions recorded cost 0.0 at 22.9M tokens).
# Vertex returns no cost, so it is computed from models.dev prices — the file opencode itself keeps
# fresh (~/.cache/opencode/models.json). $/1M tokens; Gemini Pro bills a higher tier once the
# prompt exceeds 200k. Lanes whose provider reports usage.cost (OpenRouter) keep that figure.
MODELS_DEV = Path(os.environ.get("TALON_MODELS_DEV") or Path.home() / ".cache/opencode/models.json")
PRICE_PROVIDER = {"vertexii": "google-vertex", "vertex": "google-vertex", "google-vertex": "google-vertex"}
_PRICES: dict[str, Optional[dict]] = {}


def lane_price(lane: str) -> Optional[dict]:
    """models.dev cost block for a lane ({input, output, cache_read, tiers?}) or None when unpriced."""
    if lane in _PRICES:
        return _PRICES[lane]
    head, _, model = lane.partition("/")
    out = None
    if head in PRICE_PROVIDER and MODELS_DEV.exists():
        try:
            prov = json.loads(MODELS_DEV.read_text()).get(PRICE_PROVIDER[head]) or {}
            out = ((prov.get("models") or {}).get(model) or {}).get("cost") or None
        except (OSError, ValueError):
            out = None
    _PRICES[lane] = out
    return out


def normalize_usage(lane: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Provider usage → Talon usage: thinking counted as output (Vertex bills it but reports it
    outside completion_tokens), cost filled in from lane_price when the provider sent none."""
    u = dict(usage or {})
    p, c = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    r = int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
    if r and int(u.get("total_tokens") or 0) >= p + c + r:   # reasoning reported on top of completion
        u["completion_tokens"] = c = c + r
    u["reasoning_tokens"] = r
    if not u.get("cost"):
        price = lane_price(lane)
        if price and (p or c):
            rate = price
            for t in price.get("tiers") or []:
                if (t.get("tier") or {}).get("type") == "context" and p > int((t.get("tier") or {}).get("size") or 0):
                    rate = t
            cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
            u["cost"] = ((p - cached) * float(rate.get("input") or 0) + cached * float(rate.get("cache_read") or rate.get("input") or 0)
                         + c * float(rate.get("output") or 0)) / 1e6
    return u


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
    if head in VERTEX_PROVIDERS and "/" in lane:
        return _vertex_lane(head, lane.split("/", 1)[1])
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


def _read_stream(resp, on_delta) -> dict[str, Any]:
    """Consume an OpenAI-style SSE completion, emitting deltas; return the equivalent
    non-streaming response so the caller's parsing is unchanged. Keeps Gemini's per-call
    extra_content (thought_signature), which must be echoed back on later turns."""
    content: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish, usage, error = None, None, None
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if chunk.get("error"):
            error = chunk["error"]
            break
        if chunk.get("usage"):
            usage = chunk["usage"]
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or {}
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            thought = ((d.get("extra_content") or {}).get("google") or {}).get("thought")
            r = d.get("reasoning") or d.get("reasoning_content")
            if r:
                reasoning.append(r)
                on_delta("reasoning", r)
            if d.get("content"):
                if thought:
                    reasoning.append(d["content"])
                    on_delta("reasoning", d["content"])
                else:
                    content.append(d["content"])
                    on_delta("text", d["content"])
            for tc in d.get("tool_calls") or []:
                i = tc.get("index", len(calls))
                cur = calls.setdefault(i, {"type": "function", "index": i, "id": None,
                                           "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    cur["id"] = tc["id"]
                if tc.get("extra_content"):
                    cur["extra_content"] = tc["extra_content"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    cur["function"]["name"] += fn["name"]
                    on_delta("tool", cur["function"]["name"])
                if fn.get("arguments"):
                    cur["function"]["arguments"] += fn["arguments"]
    if error is not None:
        return {"error": error}
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if calls:
        msg["tool_calls"] = [dict(c, id=c["id"] or f"call_{i}") for i, c in sorted(calls.items())]
    if reasoning:
        msg["reasoning"] = "".join(reasoning)
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage or {}}


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


def _kind(code: Any) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "server error"
    return {429: "rate limited", 401: "auth failed", 403: "forbidden", 400: "bad request", 404: "not found",
            503: "overloaded", 529: "overloaded"}.get(code, "server error" if code >= 500 else "request refused")


def _brief(err: Any) -> str:
    """The provider's own message, one line (Vertex/Google errors nest it as error.message)."""
    if isinstance(err, str):
        try:
            err = json.loads(err)
        except ValueError:
            return " ".join(err.split())[:240]
    if isinstance(err, list) and err:
        err = err[0]
    if isinstance(err, dict):
        inner = err.get("error", err)
        if isinstance(inner, dict):
            status = inner.get("status") or ""
            return " ".join(f"{status} {inner.get('message') or ''}".split())[:240] or str(inner)[:240]
    return " ".join(str(err).split())[:240]


class Cancelled(RuntimeError):
    """The run was stopped while a model call was in flight; the call is abandoned, not awaited."""


class OpenAICompatibleClient:
    """Client for OpenAI-compatible /v1/chat/completions endpoints."""

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "", lane: str = ""):
        if not (base_url and model):
            raise LaneError("OpenAICompatibleClient needs base_url and model (use from_lane)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.lane = lane or model
        # on_notice(level, text): server-side events the operator must see — rate limits, overload,
        # retries, unreachable endpoint (J 10-04). Set by the loop; None = journal only.
        self.on_notice = None
        # Set from another thread (talon serve's Stop) to abandon the in-flight call at once. Before
        # 2026-10-04 Stop only landed between turns; a stalled Vertex read held it ~12 min (J: "kill it").
        self.cancel = threading.Event()

    def _pause(self, seconds: float) -> None:
        while seconds > 0:
            if self.cancel.is_set():
                raise Cancelled(f"stopped while waiting to retry {self.lane}")
            time.sleep(min(0.25, seconds))
            seconds -= 0.25

    def _call(self, req: Any, timeout: int, on_delta: Any) -> dict[str, Any]:
        """One HTTP round trip on a worker thread; returns early with Cancelled when cancel is set."""
        if self.cancel.is_set():
            raise Cancelled(f"stopped before calling {self.lane}")
        box: dict[str, Any] = {}
        live = (lambda k, t: None if self.cancel.is_set() else on_delta(k, t)) if on_delta else None

        def work() -> None:
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    box["data"] = _read_stream(resp, live) if live else json.loads(resp.read().decode("utf-8"))
            except BaseException as e:  # re-raised on the caller's thread
                box["err"] = e
        th = threading.Thread(target=work, name="talon-model-call", daemon=True)
        th.start()
        while th.is_alive():
            if self.cancel.wait(0.25):
                raise Cancelled(f"stopped: in-flight call to {self.lane} abandoned")
        if "err" in box:
            raise box["err"]
        return box["data"]

    def _notice(self, level: str, text: str) -> None:
        if self.on_notice:
            try:
                self.on_notice(level, text)
            except Exception:
                pass

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
        max_tokens: Optional[int] = None,
        timeout: int = 180,
        retries: int = 4,
        on_delta: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Send one chat completion. Returns the assistant message (+ `_usage`, `_finish`).

        on_delta(kind, text) streams the reply as it is generated (TALON-SSE-1, J 10-02: "the
        thoughts/tools and message live stream is not live"): kind is "text", "reasoning",
        "tool" (a tool call's name as soon as it is known) or "reset" (a retry restarts the reply).
        Without on_delta the request is a plain non-streaming completion, as before."""
        if os.environ.get("TALON_MOCK") == "1":
            return self._mock_completion(messages, tools)
        if max_tokens is None:
            # max_tokens is a ceiling, not spend: only generated tokens are billed. Gemini's hidden
            # thinking counts against it, so the old blanket 4096 cut long Vertex answers mid-sentence
            # (J 10-02). Vertex lanes get the model's own output limit (opencode.jsonc limit.output,
            # else Gemini's 65536). The loop also continues any reply still cut at the cap.
            max_tokens = int(os.environ.get("TALON_MAX_TOKENS") or self._default_max_tokens())
            max_tokens = self._fit(max_tokens, messages, tools)

        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if on_delta:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            if self.lane.split("/", 1)[0] in VERTEX_PROVIDERS:
                # Gemini returns thought summaries only when asked; they arrive as content chunks
                # marked extra_content.google.thought = true.
                payload["extra_body"] = {"google": {"thinking_config": {"include_thoughts": True}}}
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                   "HTTP-Referer": "https://sovereign-fleet.local", "X-Title": "Talon-Agent-Engine"}
        if self.lane.split("/", 1)[0] in VERTEX_PROVIDERS:
            self.api_key = adc_token() or self.api_key  # ADC tokens last ~1 h; a session can outlive one
        if self.api_key and self.api_key != "local":
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = json.dumps(payload).encode("utf-8")
        endpoint = f"{self.base_url}/chat/completions"

        last_err: Exception | None = None
        for attempt in range(retries):
            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            try:
                if on_delta and attempt:
                    on_delta("reset", "")
                data = self._call(req, timeout, on_delta)
                if data.get("error"):
                    err = data["error"]
                    code = err.get("code") if isinstance(err, dict) else None
                    last_err = RuntimeError(f"Model API error ({code}) from {self.lane}: {str(err)[:300]}")
                    # OpenRouter reports upstream overload as HTTP 200 + {"error": {"code": 503}}
                    if code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                        wait = min(30, 2 ** attempt * 3)
                        log.warning("in-body %s from %s, retry %d/%d in %ss", code, self.lane, attempt + 1, retries - 1, wait)
                        self._notice("warn", f"{_kind(code)} ({code}) from {self.lane}: {_brief(err)} — retry {attempt + 1}/{retries - 1} in {wait}s")
                        self._pause(wait)
                        continue
                    self._notice("error", f"{_kind(code)} ({code}) from {self.lane}: {_brief(err)}")
                    raise last_err
                msg = dict((data.get("choices") or [{}])[0].get("message") or {"role": "assistant", "content": ""})
                if not msg.get("tool_calls") and "<tool_call>" in (msg.get("content") or ""):
                    parsed = _parse_text_tool_calls(msg["content"])
                    if parsed:
                        msg["tool_calls"] = parsed
                        msg["content"] = _TOOL_TAG.sub("", msg["content"]).strip()
                msg["_usage"] = normalize_usage(self.lane, data.get("usage") or {})
                msg["_finish"] = (data.get("choices") or [{}])[0].get("finish_reason")
                return msg
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")[:400]
                last_err = RuntimeError(f"Model API error ({e.code}) from {self.lane}: {err_body}")
                if e.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                    wait = min(30, 2 ** attempt * 3)
                    log.warning("HTTP %d from %s, retry %d/%d in %ss", e.code, self.lane, attempt + 1, retries - 1, wait)
                    self._notice("warn", f"{_kind(e.code)} (HTTP {e.code}) from {self.lane}: {_brief(err_body)} — retry {attempt + 1}/{retries - 1} in {wait}s")
                    self._pause(wait)
                    continue
                self._notice("error", f"{_kind(e.code)} (HTTP {e.code}) from {self.lane}: {_brief(err_body)}")
                raise last_err from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = RuntimeError(f"endpoint unreachable for {self.lane} ({endpoint}): {e}")
                if attempt + 1 < retries:
                    self._notice("warn", f"endpoint unreachable for {self.lane}: {e} — retry {attempt + 1}/{retries - 1} in {2 ** attempt}s")
                    self._pause(2 ** attempt)
                    continue
                self._notice("error", f"endpoint unreachable for {self.lane}: {e}")
                raise last_err from e
        raise last_err or RuntimeError("completion failed")

    def _default_max_tokens(self) -> int:
        return lane_limits(self.lane)[1] or 4096

    def _fit(self, max_tokens: int, messages: list[dict[str, Any]], tools: Optional[list]) -> int:
        """Keep prompt + reply inside the model's context: a request whose max_tokens plus prompt
        exceeds the window is rejected outright by most providers. ~3.5 chars per token estimate."""
        ctx = lane_limits(self.lane)[0]
        if not ctx:
            return max_tokens
        est = int(len(json.dumps(messages, default=str)) / 3.5) + (int(len(json.dumps(tools)) / 3.5) if tools else 0)
        return max(1024, min(max_tokens, ctx - est - 512))

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
