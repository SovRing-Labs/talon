import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class ResearchWebSearchPlugin(TerminalPlugin):
    name = "research-web-search"
    version = "0.1.0"
    load_priority = 20

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("researcher.search.count", "0")
        ctx.publish("researcher.search.last_query", "")
        ctx.publish("researcher.search.last_results", "[]")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_search", self._cmd_search)

    def _cmd_search(self, args: list[str]) -> None:
        if not args:
            print("Usage: researcher_search <query>")
            return
        query = " ".join(args)
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("researcher.search.last_query", query)
            ctx.publish("researcher.search.count", "0")
        print(f"Web search placeholder: {query}")
        print("(Note: actual web search requires Lightpanda MCP integration)")
