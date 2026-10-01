import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import os
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

BRIEFS_DIR = Path("./spawn-briefs/active")


class BriefDeckPlugin(TerminalPlugin):
    name = "brief-deck"
    version = "0.1.0"
    load_priority = 30
    depends_on: list[str] = ["fleet-commander"]

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        briefs = self._list_briefs()
        ctx.publish("commander.brief.active", "")
        ctx.publish("commander.brief.history", json.dumps(briefs))

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        pass

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_list_briefs", self._cmd_list_briefs)
        registry.register("commander_launch_brief", self._cmd_launch_brief)
        registry.register("commander_brief_status", self._cmd_brief_status)

    def _list_briefs(self) -> list[dict]:
        briefs = []
        if BRIEFS_DIR.exists():
            for f in sorted(BRIEFS_DIR.iterdir()):
                if f.suffix in (".md", ".yaml"):
                    briefs.append({"name": f.stem, "path": str(f)})
        return briefs

    def _cmd_list_briefs(self, args: list[str]) -> None:
        briefs = self._list_briefs()
        for b in briefs:
            print(f"{b['name']} -> {b['path']}")

    def _cmd_launch_brief(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_launch_brief <brief_name>")
            return
        brief_name = args[0]
        brief_path = BRIEFS_DIR / f"{brief_name}.md"
        if not brief_path.exists():
            brief_path = BRIEFS_DIR / f"{brief_name}.yaml"
        if brief_path.exists():
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish("commander.brief.active", brief_name)
            print(f"Launching brief: {brief_name}")
        else:
            print(f"Brief not found: {brief_name}")

    def _cmd_brief_status(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        active = ""
        if ctx:
            state = ctx.get_fleet_state()
            active = state.get("commander.brief.active", "")
        print(f"Active brief: {active}")
