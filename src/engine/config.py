"""Role + key configuration for the Talon Agent Engine (TALON-COMPLETE, 2026-09-26).

Roles: read from opencode.jsonc's `agent` blocks (prompt, steps, tools map) so Talon runs the
same 8 roles opencode does with no second copy of the prompts. A Talon-owned override file
(~/.config/talon/roles.json, same shape) wins when present — the path off opencode.

Keys, first hit wins (values are never logged):
  1. the catalog's env var (api_keys.key_env_var), e.g. OPENROUTER_KEY
  2. ~/.config/talon/keys.json  {"<provider>": "<key>"}            (Talon-owned, 0600)
  3. ~/.local/share/opencode/auth.json  {"<name>": {"key": ...}}    (opencode's store)
  4. an inline `apiKey` in opencode.jsonc's provider block
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

HOME = Path.home()
OPENCODE_JSONC = Path(os.environ.get("TALON_OPENCODE_CONFIG", HOME / ".config/opencode/opencode.jsonc"))
OPENCODE_AUTH = Path(os.environ.get("TALON_OPENCODE_AUTH", HOME / ".local/share/opencode/auth.json"))
TALON_CONF_DIR = Path(os.environ.get("TALON_CONFIG_DIR", HOME / ".config/talon"))

# catalog provider id -> names to try in opencode's auth.json / jsonc provider blocks
PROVIDER_AUTH_NAMES = {
    "zen-free": ["opencode"], "zen-paid": ["opencode"], "opencode": ["opencode"],
    "openrouter": ["openrouter"], "openrouter-alt": ["openrouter-alt", "openrouter-free"],
    "openrouter-free": ["openrouter-free", "openrouter-alt"],
    "groq": ["groq"], "groq-direct": ["groq"], "mistral": ["mistral"], "google": ["google"],
    "cerebras": ["cerebras"], "vllm-remote": ["vllm-remote"], "local-gpu": ["vllm-remote"],
}


def parse_jsonc(text: str) -> Any:
    """JSON with // and /* */ comments and trailing commas (string-aware)."""
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


_OPENCODE_CACHE: Optional[dict] = None


def opencode_config() -> dict:
    global _OPENCODE_CACHE
    if _OPENCODE_CACHE is None:
        try:
            _OPENCODE_CACHE = parse_jsonc(OPENCODE_JSONC.read_text())
        except Exception:
            _OPENCODE_CACHE = {}
    return _OPENCODE_CACHE


def role_config(role: str) -> dict:
    """{'prompt', 'steps', 'tools'} for a role. Unknown role -> generic, all base tools."""
    override = TALON_CONF_DIR / "roles.json"
    if override.exists():
        try:
            r = json.loads(override.read_text()).get(role)
            if r:
                return {"prompt": r.get("prompt", ""), "steps": r.get("steps", 30), "tools": r.get("tools", {})}
        except Exception:
            pass
    a = (opencode_config().get("agent") or {}).get(role) or {}
    return {
        "prompt": a.get("prompt") or f"You are the {role} agent in J's sovereign ai-stack fleet.",
        "steps": int(a.get("steps") or 30),
        "tools": a.get("tools") or {},
    }


def known_roles() -> list[str]:
    return sorted((opencode_config().get("agent") or {}).keys())


def provider_base_url(name: str) -> Optional[str]:
    p = (opencode_config().get("provider") or {}).get(name) or {}
    return (p.get("options") or {}).get("baseURL")


def resolve_key(provider_id: str, env_var: Optional[str] = None) -> str:
    if env_var and os.environ.get(env_var):
        return os.environ[env_var].strip()
    names = PROVIDER_AUTH_NAMES.get(provider_id, [provider_id])
    try:
        own = json.loads((TALON_CONF_DIR / "keys.json").read_text())
        for nm in [provider_id, *names]:
            if own.get(nm):
                return str(own[nm]).strip()
    except Exception:
        pass
    try:
        auth = json.loads(OPENCODE_AUTH.read_text())
        for nm in names:
            k = (auth.get(nm) or {}).get("key")
            if k:
                return k.strip()
    except Exception:
        pass
    for nm in names:
        k = ((opencode_config().get("provider") or {}).get(nm) or {}).get("options", {}).get("apiKey")
        if k and not str(k).startswith("{env:"):
            return str(k).strip()
    return ""
