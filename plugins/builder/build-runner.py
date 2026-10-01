import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import subprocess
import threading
import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class BuildRunnerPlugin(TerminalPlugin):
    name = "build-runner"
    version = "0.1.0"
    load_priority = 20
    _watch_active = False
    _watch_thread: threading.Thread | None = None

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("builder.build.last_cmd", "")
        ctx.publish("builder.build.last_exit", "")
        ctx.publish("builder.build.watch_active", "false")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_build_run", self._cmd_build_run)
        registry.register("builder_build_watch", self._cmd_build_watch)

    def _cmd_build_run(self, args: list[str]) -> None:
        if not args:
            print("Usage: builder_build_run <cmd> [cwd]")
            return
        cmd = args[0]
        cwd = args[1] if len(args) > 1 else "."
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("builder.build.last_cmd", cmd)
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, timeout=300
            )
            if ctx:
                ctx.publish("builder.build.last_exit", str(result.returncode))
            print(result.stdout.decode(errors="replace") if result.stdout else "")
            if result.stderr:
                print(result.stderr.decode(errors="replace"))
        except Exception as e:
            if ctx:
                ctx.publish("builder.build.last_exit", "error")
            print(f"Error: {e}")

    def _cmd_build_watch(self, args: list[str]) -> None:
        if not args:
            print("Usage: builder_build_watch <cmd>")
            return
        cmd = args[0]
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("builder.build.watch_active", "true")
        print(f"Watch mode: {cmd}")
        print("(Note: actual file watching requires integration with the TUI)")
        if ctx:
            ctx.publish("builder.build.watch_active", "false")
