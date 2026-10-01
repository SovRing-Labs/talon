import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import time
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

CACHE_TTL = 300


class SessionTimerPlugin(TerminalPlugin):
    name = "session-timer"
    version = "0.1.0"
    load_priority = 20
    _start_time: float | None = None
    _laps: list[float] = []

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        self._start_time = time.time()
        self._laps = []
        ctx.publish(f"term-launcher.session.{session.name}.timer-running", "true")
        ctx.publish(f"term-launcher.session.{session.name}.elapsed", "0")

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        if self._start_time is not None:
            elapsed = time.time() - self._start_time
            ctx.publish(f"term-launcher.session.{new.name}.elapsed", str(int(elapsed)))

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        if self._start_time is not None:
            elapsed = time.time() - self._start_time
            ctx.publish(f"term-launcher.session.{session.name}.elapsed", str(int(elapsed)))
            ctx.publish(f"term-launcher.session.{session.name}.timer-running", "false")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("grunt_session_time", self._cmd_session_time)

    def _cmd_session_time(self, args: list[str]) -> None:
        if self._start_time is None:
            print("No active session timer")
            return
        elapsed = time.time() - self._start_time
        mins, secs = divmod(int(elapsed), 60)
        hours, mins = divmod(mins, 60)
        print(f"Elapsed: {hours:02d}:{mins:02d}:{secs:02d}")
