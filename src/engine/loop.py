"""Reentrant agent turn loop for the Talon Agent Engine.

Prompt -> model completion -> tool calls -> Omnitool execution -> context update -> repeat.

Rewritten 2026-09-26 (bug #63):
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
import os
import re
from typing import Any, Callable, Optional

from . import config, ledger
from .providers import Cancelled, OpenAICompatibleClient
from .sessions import SessionStore
from .tools import OmnitoolBridge, tools_for

log = logging.getLogger("talon.engine.loop")

KEEP_FULL_TOOL_RESULTS = 6          # newest tool results kept verbatim; older ones compacted
COMPACT_TO = 600

# Conversation compaction (TALON-VERTEX-OPS-0, J 10-04): the model writes a summary of everything so
# far; the stored messages stay for viewers, but only system prompt + summary + what follows are sent.
# Run on demand (/compact in the console, POST /api/sessions/<id>/compact) or automatically before a
# turn once the session's context reaches its `autocompact` threshold.
COMPACT_PROMPT = (
    "Summarise the conversation below so the work can continue from your summary alone; the original "
    "messages will no longer be visible. Keep: the task and the user's instructions (verbatim where wording "
    "matters), decisions made and why, every file / path / command touched and its outcome, errors met and "
    "how they were resolved, the current state, and the exact next steps. Drop chit-chat and raw tool output. "
    "Write it as a dense handover, not a narrative.")
# Loop guard (J 10-04, tln_2c109: 10+ turns rewriting one debug script, every result "new: []").
# The same tool call, or the same output once digits are masked and lines de-duplicated, seen
# LOOP_GUARD times among the last 6 tool results pauses the run with a notice (status stopped).
LOOP_GUARD = int(os.environ.get("TALON_LOOP_GUARD") or 3)    # 0 = off
_DIGITS = re.compile(r"\d+")


def _shape(text: str) -> str:
    return "\n".join(sorted({_DIGITS.sub("#", ln.strip()) for ln in (text or "").splitlines() if ln.strip()}))


def _looping(recent: list[tuple[str, str]]) -> Optional[str]:
    if not LOOP_GUARD:
        return None
    window = recent[-6:]
    for i, label in ((0, "the same tool call"), (1, "the same result")):
        keys = [k[i] for k in window]
        for k in set(keys):
            if keys.count(k) >= LOOP_GUARD:
                return f"{label} {keys.count(k)} times in the last {len(window)} tool results"
    return None


TRANSCRIPT_BUDGET = 600_000          # chars (~170k tokens): keeps the compaction call under Gemini's 200k price tier


def _transcript(messages: list[dict[str, Any]], per_tool: int = 1500, budget: int = TRANSCRIPT_BUDGET) -> str:
    parts = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            parts.append(f"USER: {m.get('content') or ''}")
        elif role == "assistant":
            calls = "".join(f"\n  [called {(tc.get('function') or {}).get('name')}({str((tc.get('function') or {}).get('arguments'))[:400]})]"
                            for tc in m.get("tool_calls") or [])
            parts.append(f"ASSISTANT: {m.get('content') or ''}{calls}")
        elif role == "tool":
            c = m.get("content") or ""
            parts.append(f"TOOL {m.get('name') or ''}: {c[:per_tool]}{' …' if len(c) > per_tool else ''}")
    text = "\n\n".join(parts)
    return text if len(text) <= budget else "[earliest part omitted]\n" + text[-budget:]


def compact(provider: Any, messages: list[dict[str, Any]], *, store: Optional[SessionStore] = None,
            session_id: Optional[str] = None, focus: str = "",
            note: Optional[Callable[[str, str], None]] = None) -> list[dict[str, Any]]:
    """Replace the conversation with a model-written summary. Returns the new message list."""
    system = next((m for m in messages if m.get("role") == "system"), None)
    body = [m for m in messages if m.get("role") not in ("system", "notice")]
    if not body:
        return messages
    before = int(len(json.dumps(messages, default=str)) / 3.5)
    req = [{"role": "system", "content": "You compact agent conversations into handover summaries."},
           {"role": "user", "content": COMPACT_PROMPT + (f"\n\nFocus especially on: {focus}" if focus else "")
            + "\n\n<conversation>\n" + _transcript(body) + "\n</conversation>"}]
    resp = provider.complete(req)
    summary = (resp.get("content") or "").strip()
    if not summary:
        raise RuntimeError("compaction returned an empty summary; conversation left as it was")
    msg = {"role": "user", "content": "[Conversation compacted — summary of everything before this point]\n\n" + summary}
    new = ([system] if system else []) + [msg]
    after = int(len(json.dumps(new, default=str)) / 3.5)
    if store and session_id:
        cut = store.conn.execute("SELECT coalesce(max(seq),0) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
        store.set(session_id, compact_seq=cut, ctx_tokens=after)
        store.append(session_id, msg)
        store.add_usage(session_id, resp.get("_usage") or {}, turn=False)
    if note:
        note("info", f"compacted {len(body)} messages (~{before:,} tokens) into a ~{after:,}-token summary"
                     + (f" (focus: {focus})" if focus else ""))
    return new


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
        on_delta: Optional[Callable[[int, str, str], None]] = None,
    ) -> dict[str, Any]:
        if self.provider is None:
            self.provider = OpenAICompatibleClient.from_lane(lane)
        rc = config.role_config(role)
        max_turns = max_turns or rc["steps"]
        if self.tools.allowed is None:
            self.tools.allowed = tools_for(rc["tools"])
        tool_defs = self.tools.get_tool_definitions(role=role)

        errored: list[str] = []

        def note(level: str, text: str) -> None:
            """Operator-visible notice (rate limit, retry, compaction, error): stored, never sent to the model."""
            if level == "error":
                errored.append(text)
            if self.store and session_id:
                self.store.append(session_id, {"role": "notice", "name": level, "content": text})
            else:
                log.warning("%s: %s", level, text)

        self.provider.on_notice = note

        def maybe_compact(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            s = self.store.get(session_id) if (self.store and session_id) else None
            if not (s and s.get("autocompact") and (s.get("ctx_tokens") or 0) >= s["autocompact"] and len(msgs) > 2):
                return msgs
            note("info", f"context {s['ctx_tokens']:,} tokens ≥ auto-compact {s['autocompact']:,}: compacting")
            try:
                return compact(self.provider, msgs, store=self.store, session_id=session_id, note=note)
            except Exception as e:
                note("warn", f"auto-compact failed, continuing uncompacted: {e}")
                return msgs

        history = self.store.history(session_id) if (self.store and session_id) else []
        if history:
            history = maybe_compact(history)      # before the new prompt, so it stays verbatim after the summary
        messages: list[dict[str, Any]] = history or [{"role": "system", "content": rc["prompt"]}]
        new_msgs = [] if history else [messages[0]]
        user = {"role": "user", "content": prompt}
        messages.append(user)
        new_msgs.append(user)
        if self.store and session_id:
            for m in new_msgs:
                self.store.append(session_id, m)

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
        recent: list[tuple[str, str]] = []     # (call signature, output shape) per tool result, for the loop guard
        turn = 0
        while turn < max_turns:
            turn += 1
            if turn > 1:                           # mid-run: the summary carries the task and next steps
                messages = maybe_compact(messages)
            if inbox and turn > 1:          # stigmergy: nudges land within one turn (§6.4)
                for text in inbox() or []:
                    injected = {"role": "user", "content": text}
                    messages.append(injected)
                    if self.store and session_id:
                        self.store.append(session_id, injected)
                    if on_turn:
                        on_turn(turn, injected)
            self._compact(messages)
            stream = (lambda kind, text, _t=turn: on_delta(_t, kind, text)) if on_delta else None
            if on_delta:
                on_delta(turn, "turn", "")
            try:
                response = (self.provider.complete(messages, tools=tool_defs, on_delta=stream) if stream
                            else self.provider.complete(messages, tools=tool_defs))
            except Cancelled as e:
                note("info", f"stopped on turn {turn}: the in-flight model call was abandoned")
                return {"ok": False, "stopped": True, "error": str(e), "turns": turn, "messages": messages, "usage": usage}
            except Exception as e:
                log.error("Provider completion error on turn %d: %s", turn, e)
                if not errored:
                    note("error", f"run stopped on turn {turn}: {e}")
                return {"ok": False, "error": f"Provider completion error: {e}", "turns": turn,
                        "messages": messages, "usage": usage}

            u = response.pop("_usage", {}) or {}
            ctx = int(u.get("prompt_tokens") or 0) or None    # what the model holds this turn
            for k in usage:
                usage[k] += u.get(k) or 0
            content = response.get("content") or ""
            tool_calls = response.get("tool_calls") or []
            reasoning = response.pop("reasoning", None) or ""
            # A final answer cut at the output cap (finish_reason "length") used to be stored as if
            # complete (J 10-02: "stuck halfway"). Ask for the rest, up to 3 times, and keep ONE
            # assistant message; the continuation prompt is transient and never stored.
            more = 0
            while response.pop("_finish", None) == "length" and not tool_calls and more < 3:
                more += 1
                note("info", f"reply hit the output cap; asking for the rest ({more}/3)")
                try:
                    response = self.provider.complete(on_delta=stream, messages=messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": "Your reply was cut off by the output limit. Continue exactly "
                                                    "where it stopped; do not repeat or summarise what you wrote."}])
                except Exception as e:
                    log.warning("continuation after length cut failed: %s", e)
                    break
                u2 = response.pop("_usage", {}) or {}
                ctx = int(u2.get("prompt_tokens") or 0) or ctx
                for k in usage:
                    usage[k] += u2.get(k) or 0
                for k, v in u2.items():
                    if isinstance(v, (int, float)):
                        u[k] = (u.get(k) or 0) + v
                content += response.get("content") or ""
                tool_calls = response.get("tool_calls") or []
                reasoning += response.pop("reasoning", None) or ""
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            if self.store and session_id:
                # thinking is stored for viewers but never sent back to the model
                self.store.append(session_id, dict(assistant_msg, reasoning=reasoning) if reasoning else assistant_msg)
                self.store.add_usage(session_id, u, ctx=ctx)
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
                out = result.get("stdout") if isinstance(result, dict) and "stdout" in result else tool_msg["content"]
                recent.append((f"{name}:{raw_args if isinstance(raw_args, str) else json.dumps(raw_args, sort_keys=True)}",
                               f"{name}:{_shape(str(out))}"))
                messages.append(tool_msg)
                if self.store and session_id:
                    self.store.append(session_id, tool_msg)
                    ledger.sync(self.store)   # core receipts this tool call caused -> ledger row + notice
                if on_turn:
                    on_turn(turn, tool_msg)
            why = _looping(recent)
            if why:
                note("warn", f"loop guard: {why} — run paused. Send a new instruction to redirect it, or /compact first "
                             f"(TALON_LOOP_GUARD={LOOP_GUARD}).")
                return {"ok": False, "stopped": True, "error": f"loop guard: {why}", "turns": turn,
                        "messages": messages, "usage": usage}

        return {"ok": False, "error": f"max turns ({max_turns}) reached without a final answer",
                "turns": turn, "messages": messages, "usage": usage}
