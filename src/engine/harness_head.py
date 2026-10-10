"""Harness-as-head adapters — 2026-10-02.

Vision (an internal vision note, 2026-10-01): every agent plugged into Talon is one of our heads. A
harness that runs its own agent loop (Claude Code, later opencode / codex) is spawned as a subprocess; Talon owns
everything around it: the session (SessionStore, same rows as engine heads), field arrive/debrief marks, the Inbox
(nudges land mid-run on the harness's stdin), the exit contract dispatcherd reads, and the usage ledger.
Tool calls: the Claude head runs in the fleet's own tree, whose PreToolUse hook routes file and
shell work to the sovereign-kernel tools (omnitool) — the head's hands are ours.

Claude: `claude -p --input-format stream-json --output-format stream-json` keeps stdin open, so injected messages are
queued as the next user turn (they show as `queued_turn_count`). stdin closes after a result with nothing pending.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

AI = Path(os.environ.get("AI_STACK_ROOT", "."))
CLAUDE_LANES = {"claude-desktop-cli", "claude-cli"}
SESSION_TAG = "HARNESS claude-session "
IDLE_CLOSE_S = int(os.environ.get("TALON_HEAD_IDLE_CLOSE_S", "1800"))
LIMIT_SIGNATURES = ("usage limit", "hit your limit", "limit reached", "rate_limit", "overloaded")


def _claude_worker():
    """bin/claude-worker (deny list, binary path) — one source for the Claude harness flags."""
    loader = importlib.machinery.SourceFileLoader("claude_worker", str(AI / "bin" / "claude-worker"))
    spec = importlib.util.spec_from_loader("claude_worker", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def handles(lane: str) -> bool:
    head = (lane or "").split("/", 1)[0]
    return head in CLAUDE_LANES or head.startswith("claude-cli-")


def claude_model(lane: str, role: str) -> str:
    """claude-desktop-cli[/opus|/sonnet|/haiku]; default Sonnet, Opus for oracle/architect (J 2026-10-02)."""
    if "/" in lane:
        return lane.split("/", 1)[1]
    return "opus" if role in ("oracle", "architect") else "sonnet"


def _prior_claude_session(store, sid: str) -> Optional[str]:
    for m in store.messages(sid):
        c = m.get("content") or ""
        if m.get("role") == "system" and c.startswith(SESSION_TAG):
            return c[len(SESSION_TAG):].strip()
    return None


def run_claude_head(*, prompt: str, role: str, lane: str, cwd: str, store, sid: str,
                    inbox: Optional[Callable[[], list[str]]] = None,
                    on_event: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    cw = _claude_worker()
    prior = _prior_claude_session(store, sid)
    csid = prior or str(uuid.uuid4())
    if not prior:
        store.append(sid, {"role": "system", "content": SESSION_TAG + csid})
    store.append(sid, {"role": "user", "content": prompt})
    argv = [str(cw.CLAUDE_BIN), "-p", "--model", claude_model(lane, role),
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--dangerously-skip-permissions", "--disallowed-tools", cw.DISALLOWED_TOOLS, "--add-dir", str(AI)]
    argv += ["--resume", csid] if prior else ["--session-id", csid]
    env = dict(os.environ, TALON_SESSION=sid, TALON_ROLE=role)
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    say = on_event or (lambda s: None)
    lock = threading.Lock()
    pending = {"n": 0, "closed": False}

    def send(text: str) -> None:
        with lock:
            if pending["closed"] or proc.stdin is None:
                return
            proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
            proc.stdin.flush()
            pending["n"] += 1

    def close_stdin() -> None:
        with lock:
            if not pending["closed"] and proc.stdin:
                pending["closed"] = True
                proc.stdin.close()

    bg = {"turn": False, "last_out": time.time()}   # a turn that started background work waits for its completion turn
    # WATCH-SIGNAL (2026-10-02): the head ends on its own exit claim. CF-2d said DONE, but a send/result count off by
    # one kept stdin open and it answered 32 nudges for 2.5 h. A DONE/BLOCKED <task> result, or a usage-limit
    # result, closes stdin; nudges arriving after that are recorded, not sent.
    task_id = os.environ.get("TALON_TASK_ID", "")
    claim_re = re.compile(rf"(^|[\s>*`])(DONE|BLOCKED)\s+{re.escape(task_id)}(\s|$|[^\w-])", re.M) if task_id else None
    ended = {"v": False}
    send(prompt)
    stop = threading.Event()

    def pump_inbox() -> None:                 # stigmergy: nudges reach the running head (field.Inbox)
        while not stop.wait(5):
            if time.time() - bg["last_out"] > IDLE_CLOSE_S:   # a background wait that never reports must not hang
                close_stdin()
            for text in (inbox() if inbox else []) or []:
                store.append(sid, {"role": "user", "content": text})
                if ended["v"]:
                    continue                  # the head already made its exit claim: record, do not re-open a turn
                say(f"⇐ {text[:110]}")
                send(text)

    threading.Thread(target=pump_inbox, daemon=True).start()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    finals: list[str] = []
    thinking = {"last": 0}
    ok, limited, turns = False, False, 0
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        bg["last_out"] = time.time()
        try:
            ev = json.loads(line)
        except Exception:
            if any(s in line.lower() for s in LIMIT_SIGNATURES):
                limited = True
            say(line.rstrip()[:160])
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "thinking_tokens":
            est = int(ev.get("estimated_tokens") or 0)
            if est - thinking["last"] >= 500:      # a real progress signal: content-watch must not read silent
                thinking["last"] = est              # thinking as a stall (DAY0-G5 attempt 1, 2026-10-02)
                say(f"· thinking ~{est} tokens")
            continue
        if t == "assistant":
            thinking["last"] = 0
            msg = ev.get("message") or {}
            text = "".join(b.get("text", "") for b in msg.get("content") or [] if b.get("type") == "text")
            calls = [{"id": b.get("id"), "type": "function",
                      "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input"))}}
                     for b in msg.get("content") or [] if b.get("type") == "tool_use"]
            if text or calls:
                rec: dict[str, Any] = {"role": "assistant", "content": text}
                if calls:
                    rec["tool_calls"] = calls
                store.append(sid, rec)
                for c in calls:
                    say(f"→ {c['function']['name']} {c['function']['arguments'][:100]}")
                    a = c["function"]["arguments"] or ""
                    if c["function"]["name"] in ("Monitor",) or '"run_in_background": true' in a:
                        bg["turn"] = True
        elif t == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    c = c if isinstance(c, str) else json.dumps(c, default=str)
                    store.append(sid, {"role": "tool", "tool_call_id": b.get("tool_use_id"), "name": "tool",
                                       "content": c[:4000]})
        elif t == "result":
            turns += int(ev.get("num_turns") or 0)
            u = ev.get("usage") or {}
            step = {"prompt_tokens": int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                    + int(u.get("cache_creation_input_tokens") or 0),
                    "completion_tokens": int(u.get("output_tokens") or 0), "cost": 0.0}   # subscription: $0 marginal
            for k in usage:
                usage[k] += step[k]
            store.add_usage(sid, step)
            final = str(ev.get("result") or "")
            if final:
                finals.append(final)   # every turn's result: a DONE line may precede a notification turn
            ok = ev.get("subtype") == "success" and not ev.get("is_error")
            if ev.get("is_error") and any(s in final.lower() for s in LIMIT_SIGNATURES):
                limited = True
            with lock:
                pending["n"] = max(0, pending["n"] - 1)
                done = pending["n"] == 0 and not bg["turn"]
            if bg["turn"]:
                say("· waiting for background work to report (stdin kept open)")
                bg["turn"] = False          # the completion arrives as the next turn; it closes if it starts nothing new
            if (claim_re and claim_re.search(final)) or limited:
                ended["v"] = True
                done = True                   # a claim is the end of the run, background work included
            if done:
                close_stdin()
    stop.set()
    close_stdin()
    rc = proc.wait()
    err = None if ok else ("rate limit — Claude subscription usage limit" if limited else f"claude exited rc={rc}")
    return {"ok": ok, "turns": turns, "content": "\n".join(finals), "usage": usage, "error": err, "harness_session": csid,
            "rate_limited": limited}
