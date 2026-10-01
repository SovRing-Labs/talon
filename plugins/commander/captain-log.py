import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import os
from datetime import datetime, timezone
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

CAPTAIN_LOG_DIR = Path(os.path.expanduser("~/.local/share/terminal-launcher/captain-log"))
CAPTAIN_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _log_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return CAPTAIN_LOG_DIR / f"{today}.jsonl"


class CaptainLogPlugin(TerminalPlugin):
    name = "captain-log"
    version = "0.1.0"
    load_priority = 5

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "session_create",
            "session": session.name,
            "cwd": session.cwd,
        }
        self._append(entry)
        ctx.publish("commander.audit.last_entry", json.dumps(entry))
        count = self._today_count()
        ctx.publish("commander.audit.entries_today", str(count))

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "session_switch",
            "from": old.name if old else "",
            "to": new.name,
        }
        self._append(entry)
        ctx.publish("commander.session.focus", new.name)

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "session_close",
            "session": session.name,
        }
        self._append(entry)

    def on_output(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_audit_write", self._cmd_audit_write)
        registry.register("commander_audit_query", self._cmd_audit_query)
        registry.register("commander_audit_export", self._cmd_audit_export)

    def _append(self, entry: dict) -> None:
        try:
            with open(_log_path(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _today_count(self) -> int:
        count = 0
        try:
            with open(_log_path()) as f:
                count = sum(1 for _ in f)
        except Exception:
            pass
        return count

    def _cmd_audit_write(self, args: list[str]) -> None:
        entry_text = " ".join(args)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "manual",
            "entry": entry_text,
        }
        self._append(entry)

    def _cmd_audit_query(self, args: list[str]) -> None:
        limit = int(args[0]) if args else 10
        try:
            with open(_log_path()) as f:
                lines = f.readlines()
            for line in lines[-limit:]:
                print(line.rstrip())
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_audit_export(self, args: list[str]) -> None:
        fmt = args[0] if args else "jsonl"
        try:
            with open(_log_path()) as f:
                data = f.read()
            print(data)
        except Exception as e:
            print(f"Error: {e}")
