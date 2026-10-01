import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import subprocess
import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DONE_GATE_BIN = Path("bin/done-gate")


class OracleDoneGateProxyPlugin(TerminalPlugin):
    name = "oracle-done-gate-proxy"
    version = "0.1.0"
    load_priority = 70

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_done_gate", self._cmd_done_gate)

    def _cmd_done_gate(self, args: list[str]) -> None:
        if not args:
            print("Usage: oracle_done_gate <task_id>")
            return
        task_id = args[0]
        if not DONE_GATE_BIN.exists():
            print(f"done-gate binary not found: {DONE_GATE_BIN}")
            return
        try:
            result = subprocess.run(
                [str(DONE_GATE_BIN), task_id],
                capture_output=True, timeout=30, text=True
            )
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish(f"oracle.done_gate.{task_id}", result.stdout.strip())
                from datetime import datetime, timezone
                ctx.publish(f"oracle.done_gate.{task_id}.ts", datetime.now(timezone.utc).isoformat())
            print(result.stdout or result.stderr)
        except Exception as e:
            print(f"Error: {e}")
