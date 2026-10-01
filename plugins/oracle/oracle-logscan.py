import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import sqlite3
import re
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DISPATCH_DB = Path("control-center/dispatch.db")


class OracleLogscanPlugin(TerminalPlugin):
    name = "oracle-logscan"
    version = "0.1.0"
    load_priority = 20

    def on_output(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        text = data.decode(errors="replace")
        errors = self._extract_errors(text)
        if errors:
            ctx.publish("oracle.lanes.alert", json.dumps(errors))
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_lanes", self._cmd_lanes)

    def _extract_errors(self, text: str) -> list[dict]:
        errors = []
        for line in text.splitlines():
            if re.search(r"\b(ERROR|FATAL|CRITICAL|Exception|Traceback)\b", line):
                errors.append({"line": line.strip(), "ts": self._now()})
        return errors

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _cmd_lanes(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx:
            return
        state = ctx.get_fleet_state()
        alerts = []
        for k, v in state.items():
            if k.startswith("lane."):
                alerts.append(f"{k}: {v[:80]}")
        if not alerts:
            print("No lane alerts")
        else:
            for a in alerts:
                print(a)
