"""Talon as a stigmergy citizen (STIGMERGY-LOOP, 2026-09-27).

  arrive / debrief   board verbs (bin/log-event → control-center/events.jsonl) + field marks
  Inbox              per-turn delivery of messages addressed to this session or its task:
                     Weaver field inbox (weaver/marks.py send / `talon nudge`) and dispatcherd's
                     content-watch nudge key `task.<id>.nudge` on the blackboard (read-only here).
                     Delivered into the conversation as a user message prefixed [OPS-INJECT].
Everything is fail-open: coordination must never break a run.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

AI = Path(os.environ.get("AI_STACK_ROOT", "."))
BLACKBOARD_DB = Path(os.environ.get("BLACKBOARD_DB") or AI / "control-center" / "blackboard.db")
_MARKS = None


def marks():
    """Weaver's field module (the one writer API for marks + inbox)."""
    global _MARKS
    if _MARKS is None:
        spec = importlib.util.spec_from_file_location("weaver_marks", str(AI / "weaver" / "marks.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MARKS = m
    return _MARKS


def board_verb(verb: str, task: Optional[str], session: str, role: str, note: str = "") -> None:
    try:
        subprocess.run([str(AI / "bin" / "log-event"), "--verb", verb, "--task", task or session,
                        "--agent", f"talon:{role}", "--session", session, "--role", role, "--note", note[:200]],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def arrive(session: str, task: Optional[str], role: str, lane: str) -> None:
    board_verb("ARRIVE", task, session, role, f"talon {lane}")
    try:
        marks().record("arrive", lane, "ok", source="talon", session=session, task=task, role=role)
    except Exception:
        pass


def debrief(session: str, task: Optional[str], role: str, ok: bool, summary: str) -> None:
    board_verb("DEBRIEF", task, session, role, ("done: " if ok else "failed: ") + (summary or "")[:160])
    try:
        marks().record("debrief", session, "ok" if ok else "fail", source="talon", session=session, task=task, role=role)
    except Exception:
        pass


class Inbox:
    """Callable returning new injected messages for this session/task (checked each turn)."""

    def __init__(self, session: str, task: Optional[str]):
        self.session, self.task = session, task
        self._nudge_seen = self._nudge_ts()  # only nudges posted AFTER the run started

    def _nudge_ts(self) -> float:
        row = self._nudge_row()
        return row[1] if row else 0

    def _nudge_row(self):
        if not self.task or not BLACKBOARD_DB.exists():
            return None
        try:
            c = sqlite3.connect(f"file:{BLACKBOARD_DB}?mode=ro", uri=True, timeout=1)
            row = c.execute("SELECT value, updated_at FROM blackboard WHERE key=?", (f"task.{self.task}.nudge",)).fetchone()
            c.close()
            return row
        except Exception:
            return None

    def __call__(self) -> list[str]:
        out: list[str] = []
        try:
            c = marks().connect()
            for m in marks().take(c, self.session, self.task or ""):
                out.append(f"[OPS-INJECT from {m.get('sender') or 'fleet'}] {m['message']}")
            c.close()
        except Exception:
            pass
        row = self._nudge_row()
        if row and row[1] > self._nudge_seen:
            self._nudge_seen = row[1]
            out.append(f"[OPS-INJECT from dispatcherd] {row[0]}")
        return out
