import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import sqlite3
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DISPATCH_DB = Path("control-center/dispatch.db")


class TaskRadarPlugin(TerminalPlugin):
    name = "task-radar"
    version = "0.1.0"
    load_priority = 40

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        locks = self._get_worktree_locks(ctx)
        ctx.publish("commander.worktree.locks", json.dumps(locks))

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        locks = self._get_worktree_locks(ctx)
        ctx.publish("commander.worktree.locks", json.dumps(locks))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_worktree_status", self._cmd_worktree_status)
        registry.register("commander_release_worktree", self._cmd_release_worktree)

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _get_worktree_locks(self, ctx: PluginContext) -> list[dict]:
        locks = []
        import subprocess
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=".",
                capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                current: dict[str, str] = {}
                for line in result.stdout.splitlines():
                    if line.startswith("worktree "):
                        current["worktree"] = line.split(" ", 1)[1]
                    elif line.startswith("branch "):
                        current["session"] = line.split(" ", 1)[1].replace("refs/heads/", "")
                    elif line == "":
                        if current.get("worktree"):
                            current.setdefault("session", "detached")
                            current["locked_at"] = self._now()
                            locks.append(current)
                        current = {}
                if current.get("worktree"):
                    current.setdefault("session", "detached")
                    current["locked_at"] = self._now()
                    locks.append(current)
        except Exception:
            pass
        return locks

    def _cmd_worktree_status(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        locks = self._get_worktree_locks(ctx) if ctx else self._get_worktree_locks(None)
        if not locks:
            print("No worktree locks")
        for lock in locks:
            print(f"{lock['worktree']} -> branch/session={lock['session']} at {lock['locked_at']}")

    def _cmd_release_worktree(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_release_worktree <worktree_name_or_agent>")
            return
        target = args[0]
        import subprocess
        SWARM_ASSEMBLE = Path("bin/swarm-assemble")
        if SWARM_ASSEMBLE.exists():
            try:
                res = subprocess.run([str(SWARM_ASSEMBLE), "free", target], capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    print(res.stdout.strip() or f"Freed worktree: {target}")
                    return
            except Exception as e:
                print(f"Error freeing with swarm-assemble: {e}")
        try:
            res = subprocess.run(["git", "worktree", "remove", "--force", target], cwd=".", capture_output=True, text=True, timeout=10)
            print(res.stdout.strip() or res.stderr.strip() or f"Released worktree: {target}")
        except Exception as e:
            print(f"Error: {e}")
