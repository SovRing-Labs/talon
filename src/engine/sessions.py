"""Talon session store (TALON-COMPLETE, 2026-09-26) — replaces opencode's session DB for Talon runs.

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


class SessionStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

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
        self.conn.execute(
            "INSERT INTO messages(session_id,seq,role,content,tool_calls,tool_call_id,name,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, seq, msg.get("role"), msg.get("content"), json.dumps(tc) if tc else None,
             msg.get("tool_call_id"), msg.get("name"), time.time()))
        self.conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), sid))
        self.conn.commit()
        return seq

    def add_usage(self, sid: str, usage: dict[str, Any]) -> None:
        if not usage:
            return
        self.conn.execute(
            "UPDATE sessions SET tokens_in=tokens_in+?, tokens_out=tokens_out+?, cost=cost+?, turns=turns+1 WHERE id=?",
            (int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
             float(usage.get("cost") or 0.0), sid))
        self.conn.commit()

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
        """Messages in OpenAI chat format, for resuming the conversation."""
        hist = []
        for m in self.messages(sid):
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
