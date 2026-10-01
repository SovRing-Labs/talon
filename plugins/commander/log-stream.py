import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import os
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

LOGSTREAM_DIR = Path("./logs")


class LogStreamPlugin(TerminalPlugin):
    name = "log-stream"
    version = "0.1.0"
    load_priority = 50
    depends_on: list[str] = ["fleet-commander"]

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("commander.logstream.sources", "[]")
        ctx.publish("commander.logstream.errors", "[]")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_logstream_add", self._cmd_logstream_add)
        registry.register("commander_logstream_remove", self._cmd_logstream_remove)

    def _cmd_logstream_add(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_logstream_add <source_path>")
            return
        source = args[0]
        ctx = getattr(self, "_ctx", None)
        if ctx:
            state = ctx.get_fleet_state()
            sources_raw = state.get("commander.logstream.sources", "[]")
            sources = json.loads(sources_raw)
            if source not in sources:
                sources.append(source)
                ctx.publish("commander.logstream.sources", json.dumps(sources))
            print(f"Added log source: {source}")

    def _cmd_logstream_remove(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_logstream_remove <source_path>")
            return
        source = args[0]
        ctx = getattr(self, "_ctx", None)
        if ctx:
            state = ctx.get_fleet_state()
            sources_raw = state.get("commander.logstream.sources", "[]")
            sources = json.loads(sources_raw)
            if source in sources:
                sources.remove(source)
                ctx.publish("commander.logstream.sources", json.dumps(sources))
            print(f"Removed log source: {source}")
