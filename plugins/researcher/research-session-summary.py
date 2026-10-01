import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from datetime import datetime, timezone
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

RESEARCH_DATA_DIR = Path.home() / ".local" / "share" / "terminal-launcher"


class ResearchSessionSummaryPlugin(TerminalPlugin):
    name = "research-session-summary"
    version = "0.1.0"
    load_priority = 50
    depends_on: list[str] = ["research-code-index", "research-web-search", "research-notes", "research-url-capture"]

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish(f"researcher.session.{session.name}.searches", "0")
        ctx.publish(f"researcher.session.{session.name}.notes", "0")
        ctx.publish(f"researcher.session.{session.name}.urls", "0")

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        summary = {
            "session": session.name,
            "cwd": session.cwd,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        summary_file = RESEARCH_DATA_DIR / "research-sessions.jsonl"
        try:
            with open(summary_file, "a") as f:
                f.write(json.dumps(summary) + "\n")
            ctx.publish(f"researcher.session.{session.name}.summary", json.dumps(summary))
        except Exception:
            pass

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_summary", self._cmd_summary)

    def _cmd_summary(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx or not ctx.session:
            return
        name = ctx.session.name
        state = ctx.get_fleet_state()
        searches = state.get(f"researcher.session.{name}.searches", "0")
        notes = state.get(f"researcher.session.{name}.notes", "0")
        urls = state.get(f"researcher.session.{name}.urls", "0")
        print(f"Session: {name} | Searches: {searches} | Notes: {notes} | URLs: {urls}")
