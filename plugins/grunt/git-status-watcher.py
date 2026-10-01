import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import subprocess
import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class GitStatusWatcherPlugin(TerminalPlugin):
    name = "git-status-watcher"
    version = "0.1.0"
    load_priority = 20

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        branch = self._git_branch(new.cwd)
        status = self._git_status(new.cwd)
        stash_count = self._git_stash_count(new.cwd)
        ctx.publish(f"term-launcher.session.{new.name}.git_branch", branch or "")
        ctx.publish(f"term-launcher.session.{new.name}.git_status", json.dumps(status))
        ctx.publish(f"term-launcher.session.{new.name}.git_stash_count", str(stash_count))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("grunt_git_state", self._cmd_git_state)

    def _git_branch(self, cwd: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=cwd, capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _git_status(self, cwd: str) -> dict:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd, capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().splitlines() if result.stdout.strip() else []
            return {"dirty": bool(lines), "changed": len(lines)}
        except Exception:
            return {"dirty": False, "changed": 0}

    def _git_stash_count(self, cwd: str) -> int:
        try:
            result = subprocess.run(
                ["git", "stash", "list"],
                cwd=cwd, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return len(result.stdout.strip().splitlines())
        except Exception:
            pass
        return 0

    def _cmd_git_state(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx or not ctx.session:
            return
        name = ctx.session.name
        state = ctx.get_fleet_state()
        branch = state.get(f"term-launcher.session.{name}.git_branch", "unknown")
        status_key = f"term-launcher.session.{name}.git_status"
        status_raw = state.get(status_key, "{}")
        try:
            status = json.loads(status_raw)
        except Exception:
            status = {}
        stash_key = f"term-launcher.session.{name}.git_stash_count"
        stash = state.get(stash_key, "0")
        print(f"Branch: {branch} | Dirty: {status.get('dirty', False)} | Stash: {stash}")
