import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import re
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

MAX_LINES = 50


class QuickOutputPlugin(TerminalPlugin):
    name = "quick-output"
    version = "0.1.0"
    load_priority = 20

    def on_output(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        text = data.decode(errors="replace")
        summary = self._summarize(text)
        ctx.publish(f"term-launcher.session.{session.name}.output_summary", summary)
        ctx.publish(f"term-launcher.session.{session.name}.output_raw", text[-2000:])
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("grunt_quick_summary", self._cmd_quick_summary)

    def _summarize(self, text: str) -> str:
        lines = text.strip().splitlines()
        if not lines:
            return ""
        last_lines = lines[-MAX_LINES:]
        error_lines = [l for l in last_lines if re.search(r"\b(ERROR|WARN|FAIL)\b", l)]
        if error_lines:
            return error_lines[-1][:120]
        return last_lines[-1][:120] if last_lines else ""

    def _cmd_quick_summary(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx or not ctx.session:
            return
        state = ctx.get_fleet_state()
        summary_key = f"term-launcher.session.{ctx.session.name}.output_summary"
        print(state.get(summary_key, "(no output yet)"))
