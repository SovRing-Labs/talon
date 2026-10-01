import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import os
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class AutoCdPlugin(TerminalPlugin):
    name = "auto-cd"
    version = "0.1.0"
    load_priority = 20

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish(f"term-launcher.session.{session.name}.cwd", session.cwd)
        repo = self._detect_repo(session.cwd)
        if repo:
            ctx.publish(f"term-launcher.session.{session.name}.repo", repo)

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        ctx.publish(f"term-launcher.session.{new.name}.cwd", new.cwd)

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("grunt_auto_cd", self._cmd_auto_cd)

    def _detect_repo(self, cwd: str) -> str | None:
        p = Path(cwd)
        for parent in [p] + list(p.parents):
            if (parent / ".git").exists():
                return str(parent)
        return None

    def _cmd_auto_cd(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx or not ctx.session:
            return
        repo_key = f"term-launcher.session.{ctx.session.name}.repo"
        state = ctx.get_fleet_state()
        repo = state.get(repo_key, "")
        print(f"Repo: {repo}" if repo else "No repo detected")
