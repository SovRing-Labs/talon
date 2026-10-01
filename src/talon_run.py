#!/usr/bin/env python3
"""talon_run — CLI for the Talon Native Agent Engine (TALON-COMPLETE, 2026-09-26).

  talon run --agent dev -m <lane> [--title T] [--dir D] [--session S] [--brief PATH] "prompt"
  talon sessions [--json] [-n 20]
  talon session <id> [--json]
  talon lanes [--live]            # which catalog lanes resolve + have a key
  talon serve [--port 4100]       # local API + web front end

`talon run` is a drop-in for `opencode run --agent R -m LANE --title T <prompt>` (dispatcherd's
spawn shape): it also accepts --auto and --attach (ignored). Contract:
  stdout  final assistant text      stderr  progress + `TALON_SESSION <id>` on the first line
  exit 0  model finished            exit 1  model/tool/turn-budget failure    exit 2  bad args/lane
There is NO offline fallback: if the model cannot be reached the run fails (bug #61 — the old
fallback executed "echo …" prompts as raw bash and exited 0).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine.loop import ReentrantAgentLoop  # noqa: E402
from engine.providers import LaneError, OpenAICompatibleClient, resolve_lane  # noqa: E402
from engine.sessions import SessionStore  # noqa: E402
from engine.tools import OmnitoolBridge  # noqa: E402
from engine import field  # noqa: E402

DEFAULT_LANE = os.environ.get("TALON_LANE", "openrouter/nvidia/nemotron-3-super-120b-a12b:free")


def _run_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="talon run", description="Run an agent task on the Talon engine.")
    p.add_argument("positional_prompt", nargs="*", help="prompt text")
    p.add_argument("--agent", "--role", dest="role", default="dev")
    p.add_argument("-m", "--model", "--lane", dest="lane", default=DEFAULT_LANE)
    p.add_argument("--title", default="")
    p.add_argument("--dir", default=os.environ.get("AI_STACK_ROOT", "."), help="workspace root for tools")
    p.add_argument("--session", default="", help="resume this Talon session id")
    p.add_argument("--brief", default="")
    p.add_argument("--prompt", default="")
    p.add_argument("--max-turns", type=int, default=None, help="default: the role's step budget")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("-q", "--quiet", action="store_true", help="no per-turn progress on stderr")
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)       # opencode compat
    p.add_argument("--attach", default="", help=argparse.SUPPRESS)              # opencode compat
    return p


def _short(v, n=110) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def run_cli(argv: list[str]) -> int:
    args = _run_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    prompt = args.prompt or " ".join(args.positional_prompt)
    if args.brief:
        bp = Path(args.brief)
        if not bp.exists():
            print(f"Error: brief not found: {bp}", file=sys.stderr)
            return 2
        prompt = (prompt + "\n\n" if prompt else "") + bp.read_text(errors="replace")
    if not prompt.strip():
        print("Error: no prompt (use --prompt, --brief, or positional text)", file=sys.stderr)
        return 2
    try:
        provider = OpenAICompatibleClient.from_lane(args.lane)
    except LaneError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # role wind-up (same as bin/talon-exec): identity + role tools on PATH for the bash tool
    os.environ["TALON_ROLE"] = args.role
    role_bin = Path(__file__).resolve().parent.parent / "roles" / args.role / "bin"
    if role_bin.is_dir() and str(role_bin) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{role_bin}{os.pathsep}bin{os.pathsep}{os.environ.get('PATH', '')}"

    store = SessionStore()
    if args.session:
        s = store.get(args.session)
        if not s:
            print(f"Error: no Talon session {args.session}", file=sys.stderr)
            return 2
        sid = args.session
        store.conn.execute("UPDATE sessions SET status='running', lane=? WHERE id=?", (args.lane, sid))
        store.conn.commit()
    else:
        sid = store.create(title=args.title or prompt.strip().splitlines()[0][:80], role=args.role,
                           lane=args.lane, cwd=args.dir)
    print(f"TALON_SESSION {sid}", file=sys.stderr, flush=True)

    def on_turn(turn: int, msg: dict) -> None:
        if args.quiet:
            return
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                print(f"[talon {turn}] → {fn.get('name')} {_short(fn.get('arguments', ''))}", file=sys.stderr, flush=True)
            if msg.get("content") and msg.get("tool_calls"):
                print(f"[talon {turn}] {_short(msg['content'])}", file=sys.stderr, flush=True)
        elif msg.get("role") == "user":
            print(f"[talon {turn}] ⇐ {_short(msg.get('content', ''))}", file=sys.stderr, flush=True)
        elif msg.get("role") == "tool":
            print(f"[talon {turn}] ← {msg.get('name')} {_short(msg.get('content', ''))}", file=sys.stderr, flush=True)

    task = os.environ.get("TALON_TASK_ID") or None            # set by dispatcherd for dispatched work
    ident = {"TALON_SESSION": sid, "TALON_TASK_ID": task, "TALON_ROLE": args.role}
    field.arrive(sid, task, args.role, args.lane)
    loop = ReentrantAgentLoop(provider_client=provider,
                              tool_bridge=OmnitoolBridge(Path(args.dir), env_extra=ident), store=store)
    t0 = time.time()
    try:
        res = loop.run(prompt=prompt, role=args.role, lane=args.lane, max_turns=args.max_turns,
                       on_turn=on_turn, session_id=sid, inbox=field.Inbox(sid, task))
    except KeyboardInterrupt:
        store.finish(sid, "stopped", "interrupted")
        return 130
    except Exception as e:  # a bug in the engine itself: record it, fail loudly
        store.finish(sid, "failed", f"{type(e).__name__}: {e}")
        print(f"Agent execution failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    store.finish(sid, "done" if res.get("ok") else "failed", None if res.get("ok") else res.get("error"))
    field.debrief(sid, task, args.role, bool(res.get("ok")), res.get("content") or res.get("error") or "")
    u = res.get("usage") or {}
    if not args.quiet:
        print(f"[talon] {'done' if res.get('ok') else 'FAILED'} · {res.get('turns')} turn(s) · "
              f"{u.get('prompt_tokens', 0)} in / {u.get('completion_tokens', 0)} out · {time.time() - t0:.1f}s",
              file=sys.stderr, flush=True)
    if args.json:
        print(json.dumps({"session": sid, "ok": res.get("ok"), "turns": res.get("turns"), "usage": u,
                          "content": res.get("content"), "error": res.get("error")}))
    elif res.get("ok"):
        print(res.get("content", ""))
    else:
        print(f"Execution error: {res.get('error')}", file=sys.stderr)
    store.close()
    return 0 if res.get("ok") else 1


def sessions_cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="talon sessions")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    rows = SessionStore().list(a.n)
    if a.json:
        print(json.dumps(rows, default=str))
        return 0
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["updated_at"]))
        print(f"{r['id']}  {ts}  {r['status']:<8} {r['role']:<10} {r['n_messages']:>3} msg  "
              f"{r['tokens_in']:>7}/{r['tokens_out']:<6} {(r['lane'] or '')[:38]:<38} {(r['title'] or '')[:50]}")
    return 0


def session_cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="talon session")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    st = SessionStore()
    s = st.get(a.id)
    if not s:
        print(f"no session {a.id}", file=sys.stderr)
        return 1
    msgs = st.messages(a.id)
    if a.json:
        print(json.dumps({"session": s, "messages": msgs}, default=str))
        return 0
    print(f"{s['id']} · {s['status']} · {s['role']} · {s['lane']} · {s['tokens_in']}/{s['tokens_out']} tokens")
    for m in msgs:
        if m["role"] == "system":
            continue
        if m["tool_calls"]:
            for tc in m["tool_calls"]:
                print(f"  [{m['seq']}] assistant → {tc['function']['name']} {_short(tc['function']['arguments'], 160)}")
        if m["content"] and m["role"] != "tool":
            print(f"  [{m['seq']}] {m['role']}: {m['content'][:1500]}")
        if m["role"] == "tool":
            print(f"  [{m['seq']}] ← {m['name']}: {_short(m['content'] or '', 160)}")
    return 0


def lanes_cli(argv: list[str]) -> int:
    import sqlite3
    from engine.providers import LANE_CATALOG_DB
    p = argparse.ArgumentParser(prog="talon lanes")
    p.add_argument("--live", action="store_true", help="only lanes the catalog marks live")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    q = "SELECT id, status FROM lanes" + (" WHERE status='live'" if a.live else "") + " ORDER BY id"
    rows = sqlite3.connect(f"file:{LANE_CATALOG_DB}?mode=ro", uri=True).execute(q).fetchall()
    out = []
    for lane, status in rows:
        try:
            base, model, key = resolve_lane(lane)
            out.append({"lane": lane, "status": status, "ok": True, "base": base, "model": model})
        except LaneError as e:
            out.append({"lane": lane, "status": status, "ok": False, "error": str(e)})
    if a.json:
        print(json.dumps(out))
        return 0
    for r in out:
        print(f"{'OK ' if r['ok'] else 'ERR'} {r['status']:<8} {r['lane']:<60} "
              f"{(r.get('base') or '') if r['ok'] else r['error'][:70]}")
    print(f"{sum(r['ok'] for r in out)}/{len(out)} lanes resolvable")
    return 0


def nudge_cli(argv: list[str]) -> int:
    """talon nudge <session-id | task-id> "message" — lands in that run's next turn."""
    p = argparse.ArgumentParser(prog="talon nudge")
    p.add_argument("address")
    p.add_argument("message")
    p.add_argument("--from", dest="sender", default=os.environ.get("FLEET_SEAT") or "operator")
    a = p.parse_args(argv)
    c = field.marks().connect()
    mid = field.marks().send(c, a.address, a.message, a.sender)
    c.close()
    print(f"queued #{mid} for {a.address}")
    return 0


COMMANDS = {"nudge": nudge_cli, "run": run_cli, "sessions": sessions_cli, "session": session_cli, "lanes": lanes_cli}


def main(argv: list[str]) -> int:
    if argv and argv[0] == "serve":
        from engine.server import serve_cli
        return serve_cli(argv[1:])
    if argv and argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
