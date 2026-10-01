import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

SNAPSHOT_DIR = Path.home() / ".local" / "share" / "terminal-launcher" / "oracle-snapshots"


class OracleSessionSnapshotPlugin(TerminalPlugin):
    name = "oracle-session-snapshot"
    version = "0.1.0"
    load_priority = 60

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        snapshot = {
            "session": session.name,
            "cwd": session.cwd,
            "tags": session.tags,
            "created_at": session.created_at,
            "last_active": session.last_active,
        }
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot_file = SNAPSHOT_DIR / f"{session.name}.json"
        try:
            with open(snapshot_file, "w") as f:
                json.dump(snapshot, f, indent=2)
            ctx.publish("oracle.snapshot.list", session.name)
        except Exception:
            pass

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_snapshot", self._cmd_snapshot)

    def _cmd_snapshot(self, args: list[str]) -> None:
        if not args:
            print("Usage: oracle_snapshot <session_name>")
            return
        session_name = args[0]
        snapshot_file = SNAPSHOT_DIR / f"{session_name}.json"
        if snapshot_file.exists():
            with open(snapshot_file) as f:
                print(f.read())
        else:
            print(f"Snapshot not found: {session_name}")
