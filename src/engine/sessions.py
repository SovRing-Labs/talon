"""Talon session store (2026-09-26) — replaces opencode's session DB for Talon runs.

~/.local/share/talon/talon.db (TALON_DB overrides). One row per session, one per message, usage
summed per session. Sessions are resumable (`talon run --session <id>`), listable, and served to
the front end by `talon serve`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, title TEXT, role TEXT, lane TEXT, cwd TEXT,
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','done','failed','stopped')),
  error TEXT, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), seq INTEGER NOT NULL,
  role TEXT NOT NULL, content TEXT, tool_calls TEXT, tool_call_id TEXT, name TEXT, created_at REAL NOT NULL,
  UNIQUE(session_id, seq));
CREATE INDEX IF NOT EXISTS ix_sessions_updated ON sessions(updated_at DESC);
"""


def db_path() -> Path:
    return Path(os.environ.get("TALON_DB") or Path.home() / ".local/share/talon/talon.db")


# Called as listener(sid, row) after every stored message (row = what messages() returns).
# talon serve registers one to push messages to live viewers the moment they are written
# (TALON-SSE-1). Listener errors never break a run.
LISTENERS: list = []


class SessionStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(messages)")}
        if "reasoning" not in cols:  # thinking text per assistant turn (TALON-SSE-1, 2026-10-02)
            self.conn.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")
            self.conn.commit()
        # TALON-VERTEX-OPS-0 (2026-10-04): ctx_tokens = the last call's prompt size (what the model
        # holds now); compact_seq = messages at or below it were summarised and are no longer sent;
        # autocompact = compact before a turn once ctx_tokens reaches it (0 = off).
        scols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)")}
        for col in ("ctx_tokens", "compact_seq", "autocompact"):
            if col not in scols:
                self.conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        if "cost" not in cols:  # spend per message, so "today" counts today's turns only
            self.conn.execute("ALTER TABLE messages ADD COLUMN cost REAL NOT NULL DEFAULT 0")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create(self, *, title: str, role: str, lane: str, cwd: str) -> str:
        sid = f"tln_{uuid.uuid4().hex[:20]}"
        t = time.time()
        self.conn.execute("INSERT INTO sessions(id,title,role,lane,cwd,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                          (sid, title, role, lane, cwd, t, t))
        self.conn.commit()
        return sid

    def append(self, sid: str, msg: dict[str, Any]) -> int:
        seq = self.conn.execute("SELECT coalesce(max(seq),0)+1 FROM messages WHERE session_id=?", (sid,)).fetchone()[0]
        tc = msg.get("tool_calls")
        now = time.time()
        self.conn.execute(
            "INSERT INTO messages(session_id,seq,role,content,tool_calls,tool_call_id,name,created_at,reasoning) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, seq, msg.get("role"), msg.get("content"), json.dumps(tc) if tc else None,
             msg.get("tool_call_id"), msg.get("name"), now, msg.get("reasoning")))
        self.conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))
        self.conn.commit()
        row = {"session_id": sid, "seq": seq, "role": msg.get("role"), "content": msg.get("content"),
               "tool_calls": tc or None, "tool_call_id": msg.get("tool_call_id"), "name": msg.get("name"),
               "created_at": now, "reasoning": msg.get("reasoning")}
        for listener in LISTENERS:
            try:
                listener(sid, row)
            except Exception:
                pass
        return seq

    def add_usage(self, sid: str, usage: dict[str, Any], ctx: Optional[int] = None, turn: bool = True) -> None:
        if not usage:
            return
        self.conn.execute(
            "UPDATE sessions SET tokens_in=tokens_in+?, tokens_out=tokens_out+?, cost=cost+?, turns=turns+?,"
            " ctx_tokens=coalesce(?, ctx_tokens) WHERE id=?",
            (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
             float(usage.get("cost") or 0.0), 1 if turn else 0, ctx, sid))
        if usage.get("cost"):
            self.conn.execute("UPDATE messages SET cost=cost+? WHERE session_id=? AND seq=(SELECT max(seq) FROM messages "
                              "WHERE session_id=?)", (float(usage["cost"]), sid, sid))
        self.conn.commit()

    def set(self, sid: str, **cols: Any) -> None:
        """Update operator settings / compaction state (whitelisted columns only)."""
        ok = {k: v for k, v in cols.items() if k in ("autocompact", "compact_seq", "ctx_tokens")}
        if ok:
            self.conn.execute(f"UPDATE sessions SET {', '.join(f'{k}=?' for k in ok)} WHERE id=?", (*ok.values(), sid))
            self.conn.commit()

    def spend(self, since: float = 0) -> dict[str, Any]:
        """Paid spend across sessions updated since `since`: total + per lane."""
        rows = self.conn.execute("SELECT s.lane, sum(m.cost) c, count(DISTINCT s.id) n FROM messages m JOIN sessions s "
                                 "ON s.id=m.session_id WHERE m.created_at>=? AND m.cost>0 GROUP BY s.lane ORDER BY c DESC",
                                 (since,)).fetchall()
        lanes = [{"lane": r["lane"], "cost": r["c"], "sessions": r["n"]} for r in rows]
        return {"cost": sum(x["cost"] for x in lanes), "lanes": lanes}

    def finish(self, sid: str, status: str, error: Optional[str] = None) -> None:
        self.conn.execute("UPDATE sessions SET status=?, error=?, updated_at=? WHERE id=?",
                          (status, error, time.time(), sid))
        self.conn.commit()

    def get(self, sid: str) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None

    def messages(self, sid: str, after: int = 0) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM messages WHERE session_id=? AND seq>? ORDER BY seq", (sid, after)):
            m = dict(r)
            m["tool_calls"] = json.loads(m["tool_calls"]) if m["tool_calls"] else None
            out.append(m)
        return out

    def history(self, sid: str) -> list[dict[str, Any]]:
        """Messages in OpenAI chat format, for resuming the conversation. Notices are for the
        operator only; after a compaction only the system prompt and what follows the cut are sent."""
        hist = []
        cut = int((self.get(sid) or {}).get("compact_seq") or 0)
        for m in self.messages(sid):
            if m["role"] == "notice" or (cut and m["seq"] <= cut and m["role"] != "system"):
                continue
            msg: dict[str, Any] = {"role": m["role"], "content": m["content"] or ""}
            if m["tool_calls"]:
                msg["tool_calls"] = m["tool_calls"]
            if m["role"] == "tool":
                msg["tool_call_id"] = m["tool_call_id"]
                msg["name"] = m["name"]
            hist.append(msg)
        return hist

    def list(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT s.*, (SELECT count(*) FROM messages m WHERE m.session_id=s.id) AS n_messages "
            "FROM sessions s ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def find_by_title(self, title: str, since: float = 0) -> Optional[dict]:
        r = self.conn.execute("SELECT * FROM sessions WHERE title=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                              (title, since)).fetchone()
        return dict(r) if r else None
