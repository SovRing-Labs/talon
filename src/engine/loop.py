"""Reentrant agent turn loop for the Talon Agent Engine.

Prompt -> model completion -> tool calls -> Omnitool execution -> context update -> repeat.

Rewritten 2026-09-26 (TALON-COMPLETE, bug #63):
  - the loop keeps the provider it is given; `lane` only builds one when none was passed
  - the role's real system prompt + step budget come from engine.config (opencode.jsonc agents)
  - running out of turns is a FAILURE (was reported ok=True)
  - every message and its usage is persisted to the session store when one is attached,
    and a session can be resumed from its stored history
  - old tool outputs are compacted so long runs stay inside the model's context
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from . import config
from .providers import OpenAICompatibleClient
from .sessions import SessionStore
from .tools import OmnitoolBridge, tools_for

log = logging.getLogger("talon.engine.loop")

KEEP_FULL_TOOL_RESULTS = 6          # newest tool results kept verbatim; older ones compacted
COMPACT_TO = 600


class ReentrantAgentLoop:
    """Executes multi-turn agent conversations with tool execution."""

    def __init__(
        self,
        provider_client: Optional[OpenAICompatibleClient] = None,
        tool_bridge: Optional[OmnitoolBridge] = None,
        store: Optional[SessionStore] = None,
    ):
        self.provider = provider_client
        self.tools = tool_bridge or OmnitoolBridge()
        self.store = store

    @staticmethod
    def _compact(messages: list[dict[str, Any]]) -> None:
        tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        for i in tool_idx[:-KEEP_FULL_TOOL_RESULTS]:
            c = messages[i].get("content") or ""
            if len(c) > COMPACT_TO:
                messages[i]["content"] = c[:COMPACT_TO] + f" … [compacted, {len(c)} chars]"

    def run(
        self,
        prompt: str,
        role: str = "dev",
        lane: str = "",
        max_turns: Optional[int] = None,
        on_turn: Optional[Callable[[int, dict[str, Any]], None]] = None,
        session_id: Optional[str] = None,
        inbox: Optional[Callable[[], list[str]]] = None,
    ) -> dict[str, Any]:
        if self.provider is None:
            self.provider = OpenAICompatibleClient.from_lane(lane)
        rc = config.role_config(role)
        max_turns = max_turns or rc["steps"]
        if self.tools.allowed is None:
            self.tools.allowed = tools_for(rc["tools"])
        tool_defs = self.tools.get_tool_definitions(role=role)

        history = self.store.history(session_id) if (self.store and session_id) else []
        messages: list[dict[str, Any]] = history or [{"role": "system", "content": rc["prompt"]}]
        new_msgs = [] if history else [messages[0]]
        user = {"role": "user", "content": prompt}
        messages.append(user)
        new_msgs.append(user)
        if self.store and session_id:
            for m in new_msgs:
                self.store.append(session_id, m)

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        turn = 0
        while turn < max_turns:
            turn += 1
            if inbox and turn > 1:          # stigmergy: nudges land within one turn (§6.4)
                for text in inbox() or []:
                    injected = {"role": "user", "content": text}
                    messages.append(injected)
                    if self.store and session_id:
                        self.store.append(session_id, injected)
                    if on_turn:
                        on_turn(turn, injected)
            self._compact(messages)
            try:
                response = self.provider.complete(messages, tools=tool_defs)
            except Exception as e:
                log.error("Provider completion error on turn %d: %s", turn, e)
                return {"ok": False, "error": f"Provider completion error: {e}", "turns": turn,
                        "messages": messages, "usage": usage}

            u = response.pop("_usage", {}) or {}
            for k in usage:
                usage[k] += u.get(k) or 0
            content = response.get("content") or ""
            tool_calls = response.get("tool_calls") or []
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            if self.store and session_id:
                self.store.append(session_id, assistant_msg)
                self.store.add_usage(session_id, u)
            if on_turn:
                on_turn(turn, assistant_msg)

            if not tool_calls:
                return {"ok": True, "turns": turn, "content": content, "messages": messages, "usage": usage}

            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = None
                if args is None:
                    result = {"ok": False, "error": f"arguments were not valid JSON: {str(raw_args)[:200]}"}
                else:
                    result = self.tools.execute_tool(name, args)
                tool_msg = {"role": "tool", "tool_call_id": tc.get("id", "call_unknown"), "name": name,
                            "content": json.dumps(result, default=str)}
                messages.append(tool_msg)
                if self.store and session_id:
                    self.store.append(session_id, tool_msg)
                if on_turn:
                    on_turn(turn, tool_msg)

        return {"ok": False, "error": f"max turns ({max_turns}) reached without a final answer",
                "turns": turn, "messages": messages, "usage": usage}
