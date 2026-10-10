"""Talon's ledger (FLEET-LOOP-RAPTOR S4): every core receipt a Talon session caused, kept in the session store (talon.db).

A session's tools run with CORE_CALLER=talon:<sid> (OmnitoolBridge), so `bin/core-registry fire|gate` stamps that caller on its
receipt in control-center/core-receipts.jsonl (contract v1 §2). sync() tails that file from a cursor kept in talon.db, and for
each receipt whose caller is a known Talon session: one `ledger` row (receipt ref = sha256 of the receipt line, first 16 hex) and
one `notice` message in the session transcript, so the agent, the console and the Raptor stream all see the core's verdict.
Idempotent (UNIQUE(session_id, ref)); never raises into the agent loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'core',
  ref TEXT NOT NULL, core TEXT, op TEXT, verdict TEXT, ms INTEGER, input_hash TEXT, output_ref TEXT, receipt TEXT,
  UNIQUE(session_id, ref));
CREATE INDEX IF NOT EXISTS ix_ledger_session ON ledger(session_id, ts);
CREATE TABLE IF NOT EXISTS ledger_cursor(path TEXT PRIMARY KEY, offset INTEGER NOT NULL);
"""
PREFIX = "talon:"
TRIT = {"pass": "+1", "fail": "-1", "error": "0", "dry": "·"}


def receipts_path() -> Path:
    return Path(os.environ.get("CORE_RECEIPTS", "control-center/core-receipts.jsonl"))


def ensure(conn) -> None:
    conn.executescript(SCHEMA)


def caller_for(sid: str) -> str:
    return f"{PREFIX}{sid}"


def ref_of(line: bytes) -> str:
    return hashlib.sha256(line.strip()).hexdigest()[:16]


def sync(store, path: Optional[Path] = None) -> int:
    """Ingest new receipts into the ledger of the sessions that caused them. Returns rows added."""
    try:
        p = Path(path or receipts_path())
        c = store.conn
        ensure(c)
        if not p.exists():
            return 0
        row = c.execute("SELECT offset FROM ledger_cursor WHERE path=?", (str(p),)).fetchone()
        off = row[0] if row else 0
        size = p.stat().st_size
        if size < off:                       # file replaced / truncated: start over (UNIQUE keeps it idempotent)
            off = 0
        if size == off:
            return 0
        with p.open("rb") as f:
            f.seek(off)
            chunk = f.read(size - off)
        end = chunk.rfind(b"\n") + 1         # only whole lines; a half-written tail waits for the next sync
        added = 0
        for line in chunk[:end].splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            caller = str(rec.get("caller") or "")
            if not caller.startswith(PREFIX):
                continue
            sid = caller[len(PREFIX):]
            if not c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                continue
            ref = ref_of(line)
            cur = c.execute(
                "INSERT OR IGNORE INTO ledger(session_id,ts,kind,ref,core,op,verdict,ms,input_hash,output_ref,receipt) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (sid, time.time(), "core", ref, rec.get("core"), rec.get("op"), rec.get("verdict"), rec.get("ms"),
                 rec.get("input_hash"), rec.get("output_ref"), line.decode("utf-8", "replace")))
            if cur.rowcount:
                added += 1
                c.commit()
                store.append(sid, {"role": "notice", "name": "core",
                                   "content": f"core {rec.get('core')}.{rec.get('op')} {TRIT.get(rec.get('verdict'), '?')} "
                                              f"{rec.get('verdict')} · {rec.get('ms')} ms · receipt {ref}"})
        c.execute("INSERT INTO ledger_cursor(path, offset) VALUES(?,?) ON CONFLICT(path) DO UPDATE SET offset=excluded.offset",
                  (str(p), off + end))
        c.commit()
        return added
    except Exception:
        return 0


def rows(store, sid: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    ensure(store.conn)
    q = "SELECT session_id, ts, kind, ref, core, op, verdict, ms, input_hash, output_ref FROM ledger"
    args: tuple = ()
    if sid:
        q += " WHERE session_id=?"
        args = (sid,)
    q += " ORDER BY ts DESC, id DESC LIMIT ?"
    return [dict(r) for r in store.conn.execute(q, args + (limit,))]
