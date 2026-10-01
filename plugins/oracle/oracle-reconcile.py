import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import sqlite3
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DISPATCH_DB = Path("control-center/dispatch.db")
PLAN_PATH = Path("control-center/PLAN.md")


class OracleReconcilePlugin(TerminalPlugin):
    name = "oracle-reconcile"
    version = "0.1.0"
    load_priority = 30

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_reconcile", self._cmd_reconcile)

    def _cmd_reconcile(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        drift = []

        board_tasks = set()
        if PLAN_PATH.exists():
            try:
                content = PLAN_PATH.read_text()
                for line in content.splitlines():
                    if line.strip().startswith("- [ ]") or line.strip().startswith("- [x]"):
                        board_tasks.add(line.strip())
            except Exception as e:
                drift.append(f"PLAN.md read error: {e}")

        db_tasks = set()
        if DISPATCH_DB.exists():
            try:
                conn = sqlite3.connect(DISPATCH_DB)
                cursor = conn.execute("SELECT id, status FROM tasks")
                for row in cursor.fetchall():
                    db_tasks.add(f"{row[0]}:{row[1]}")
                conn.close()
            except Exception as e:
                drift.append(f"dispatch.db read error: {e}")

        if board_tasks != db_tasks:
            drift.append(f"Board has {len(board_tasks)} tasks, DB has {len(db_tasks)} tasks")

        if drift:
            print("DRIFT DETECTED:")
            for d in drift:
                print(f"  {d}")
        else:
            print("Reconcile OK: no drift detected")

        if ctx:
            ctx.publish("oracle.reconcile.drift", json.dumps(drift))
            ctx.publish("oracle.reconcile.ts", self._now())

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
