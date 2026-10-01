import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import subprocess
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class GitWorkflowPlugin(TerminalPlugin):
    name = "git-workflow"
    version = "0.1.0"
    load_priority = 20

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        branch = self._git_branch(new.cwd)
        dirty = self._git_dirty(new.cwd)
        ahead_behind = self._git_ahead_behind(new.cwd)
        ctx.publish("builder.git.branch", branch or "")
        ctx.publish("builder.git.dirty", str(dirty))
        ctx.publish("builder.git.ahead_behind", json.dumps(ahead_behind) if ahead_behind else "{}")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_git_status", self._cmd_git_status)
        registry.register("builder_git_commit", self._cmd_git_commit)
        registry.register("builder_git_push", self._cmd_git_push)

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

    def _git_dirty(self, cwd: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd, capture_output=True, timeout=5, text=True
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def _git_ahead_behind(self, cwd: str) -> dict | None:
        try:
            result = subprocess.run(
                ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
                cwd=cwd, capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                return {"ahead": int(parts[0]), "behind": int(parts[1])}
        except Exception:
            pass
        return None

    def _cmd_git_status(self, args: list[str]) -> None:
        cwd = args[0] if args else "."
        branch = self._git_branch(cwd) or "unknown"
        dirty = self._git_dirty(cwd)
        print(f"Branch: {branch} | Dirty: {dirty}")

    def _cmd_git_commit(self, args: list[str]) -> None:
        message = " ".join(args)
        if not message:
            print("Usage: builder_git_commit <message>")
            return
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True, text=True
            )
            print(result.stdout or result.stderr)

            def _cmd_git_push(self, args: list[str]) -> None:
                remote = args[0] if args else "origin"
                branch = args[1] if len(args) > 1 else ""
                cmd = ["git", "push", remote]
                if branch:
                    cmd.append(branch)
                result = subprocess.run(cmd, capture_output=True, text=True)
                print(result.stdout or result.stderr)
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_git_push(self, args: list[str]) -> None:
        remote = args[0] if args else "origin"
        branch = args[1] if len(args) > 1 else ""
        cmd = ["git", "push", remote]
        if branch:
            cmd.append(branch)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(result.stdout or result.stderr)
        except Exception as e:
            print(f"Error: {e}")
