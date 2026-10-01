"""Plugin system for the terminal launcher.

Provides the TerminalPlugin base class, PluginHost for discovery/loading,
PluginContext for per-invocation state, and CommandRegistry for named
command registration. Built-in plugins are included for fleet awareness,
git branch display, and persistent session Queuing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable, Dict, List

SWARM_BUS = Path("bin/swarm-bus")


def _swarm_bus_put(key: str, value: str) -> None:
    import subprocess
    try:
        subprocess.run(
            [str(SWARM_BUS), "put", "TERM-LAUNCHER", key, value],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PluginContext — per-invocation context
# ---------------------------------------------------------------------------


class PluginContext:
    def __init__(self, launcher: Optional["TerminalLauncher"] = None, session: Optional["Session"] = None):
        self.launcher = launcher
        self.session = session

    def publish(self, key: str, value: str) -> None:
        _swarm_bus_put(key, value)

    def get_fleet_state(self) -> dict:
        state: dict = {}
        try:
            result = subprocess.run(
                [str(SWARM_BUS), "dump"],
                capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if ":" in line:
                        parts = line.split(":", 1)
                        state[parts[0]] = parts[1]
        except Exception:
            pass
        return state


# ---------------------------------------------------------------------------
# CommandRegistry — register / list / execute named commands
# ---------------------------------------------------------------------------


class CommandRegistry:
    def __init__(self):
        self._commands: Dict[str, Callable[[list[str]], None]] = {}

    def register(self, name: str, callback: Callable[[list[str]], None]) -> None:
        self._commands[name] = callback

    def get(self, name: str) -> Optional[Callable[[list[str]], None]]:
        return self._commands.get(name)

    def list_commands(self) -> List[str]:
        return list(self._commands.keys())


# ---------------------------------------------------------------------------
# TerminalPlugin base class
# ---------------------------------------------------------------------------


class TerminalPlugin:
    name: str = "base"
    version: str = "0.1.0"

    def on_session_create(self, ctx: PluginContext, session: "Session") -> None:
        pass

    def on_session_switch(self, ctx: PluginContext, old: Optional["Session"], new: "Session") -> None:
        pass

    def on_session_close(self, ctx: PluginContext, session: "Session") -> None:
        pass

    def on_output(self, ctx: PluginContext, session: "Session", data: bytes) -> bytes:
        return data

    def on_input(self, ctx: PluginContext, session: "Session", data: bytes) -> bytes:
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        pass


# ---------------------------------------------------------------------------
# Session dataclass — used by plugins
# ---------------------------------------------------------------------------


SAFE_ENV_VARS = {"PATH", "HOME", "TERM", "LANG", "EDITOR", "SHELL", "USER", "LOGNAME", "PAGER"}

_ENV_PREFIXES_TO_STRIP = ("AWS_", "SECRET_", "TOKEN_", "PASSWORD_", "API_KEY", "PRIVATE_")


def _filter_env(env: dict[str, str]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    for k, v in env.items():
        if k in SAFE_ENV_VARS:
            filtered[k] = v
        else:
            stripped = any(k.startswith(p) for p in _ENV_PREFIXES_TO_STRIP)
            if not stripped:
                filtered[k] = v
    return filtered


@dataclass
class Session:
    name: str
    created_at: str
    last_active: str
    cwd: str
    env: dict[str, str]
    pty_state: Optional[dict] = None
    shell: str = "/bin/bash"
    worktree: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    output_buffer: list[bytes] = field(default_factory=list)
    is_background: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "cwd": self.cwd,
            "env": _filter_env(dict(self.env)),
            "shell": self.shell,
            "worktree": self.worktree,
            "tags": list(self.tags),
        }


# ---------------------------------------------------------------------------
# PluginHost — plugin discovery, load/unload, command registry
# ---------------------------------------------------------------------------


def _default_plugins_dir() -> Path:
    project_plugins = Path(__file__).resolve().parent.parent / "plugins"
    if project_plugins.exists():
        return project_plugins
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")))
    plugins_dir = config_dir / "terminal-launcher" / "plugins"
    return plugins_dir


class PluginHost:
    def __init__(self, plugins_dir: Optional[Path] = None):
        if plugins_dir is None:
            self.plugins_dir = _default_plugins_dir()
        else:
            self.plugins_dir = plugins_dir
        self._plugins: Dict[str, TerminalPlugin] = {}
        self._command_registry = CommandRegistry()
        self._ctx: Optional[PluginContext] = None

    def set_context(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        for plugin in self._plugins.values():
            if plugin is not None:
                plugin._ctx = ctx

    def discover(self) -> List[str]:
        discovered: List[str] = []
        if not self.plugins_dir.exists():
            return discovered
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                discovered.append(item.name)
            elif item.suffix == ".py" and item.stem != "__init__":
                discovered.append(item.stem)
            elif item.is_dir():
                for subitem in item.glob("*.py"):
                    if subitem.stem != "__init__":
                        discovered.append(subitem.stem)
        return sorted(list(set(discovered)))

    def load_plugin(self, name: str) -> Optional[TerminalPlugin]:
        if name in self._plugins:
            return self._plugins[name]
        try:
            target_path = None
            if (self.plugins_dir / name / "__init__.py").exists():
                target_path = self.plugins_dir / name / "__init__.py"
            elif (self.plugins_dir / f"{name}.py").exists():
                target_path = self.plugins_dir / f"{name}.py"
            else:
                matches = list(self.plugins_dir.glob(f"*/{name}.py"))
                if matches:
                    target_path = matches[0]
                else:
                    user_dir = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "terminal-launcher" / "plugins"
                    if (user_dir / f"{name}.py").exists():
                        target_path = user_dir / f"{name}.py"
                    else:
                        user_matches = list(user_dir.glob(f"*/{name}.py"))
                        if user_matches:
                            target_path = user_matches[0]

            if not target_path or not target_path.exists():
                return None

            src_dir = str(Path(__file__).resolve().parent)
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)

            spec = importlib.util.spec_from_file_location(name.replace("-", "_"), target_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            plugin = None
            if hasattr(module, "plugin") and module.plugin is not None:
                plugin = module.plugin
            if plugin is None:
                for obj in module.__dict__.values():
                    if isinstance(obj, type) and issubclass(obj, TerminalPlugin) and obj is not TerminalPlugin:
                        plugin = obj()
                        break
            if plugin is None and hasattr(module, "TerminalPlugin"):
                plugin_class = getattr(module, "TerminalPlugin")
                if isinstance(plugin_class, type) and issubclass(plugin_class, TerminalPlugin):
                    plugin = plugin_class()
                elif isinstance(plugin_class, TerminalPlugin):
                    plugin = plugin_class

            if plugin is not None:
                if self._ctx is not None:
                    plugin._ctx = self._ctx
                if hasattr(plugin, "register_commands"):
                    plugin.register_commands(self._command_registry)
                self._plugins[name] = plugin
                return plugin
            elif hasattr(module, "plugin"):
                self._plugins[name] = None
                return None
            return None
        except Exception:
            return None

    def unload_plugin(self, name: str) -> bool:
        if name in self._plugins:
            del self._plugins[name]
            return True
        return False

    def get_plugin(self, name: str) -> Optional[TerminalPlugin]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        return list(self._plugins.keys())

    def get_command_registry(self) -> CommandRegistry:
        return self._command_registry


# ---------------------------------------------------------------------------
# Built-in plugins
# ---------------------------------------------------------------------------


class FleetAwarenessPlugin(TerminalPlugin):
    name = "fleet-awareness"
    version = "0.1.0"

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("term-launcher.session", session.name)
        ctx.publish("term-launcher.session_count", str(len(ctx.launcher.sessions)))
        self._update_fleet_state(ctx)

    def on_session_switch(self, ctx: PluginContext, old: Optional[Session], new: Session) -> None:
        ctx.publish("term-launcher.session", new.name)
        self._update_fleet_state(ctx)

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("term-launcher.session_count", str(len(ctx.launcher.sessions)))
        self._update_fleet_state(ctx)

    def _update_fleet_state(self, ctx: PluginContext) -> None:
        sessions_data = []
        for name, sess in ctx.launcher.sessions.items():
            sessions_data.append({
                "name": name,
                "cwd": sess.cwd,
                "active": not sess.is_background,
                "last_active": sess.last_active,
            })
        ctx.publish("term-launcher.fleet", json.dumps({"sessions": sessions_data}))


class GitBranchPlugin(TerminalPlugin):
    name = "git-branch"
    version = "0.1.0"

    def on_session_switch(self, ctx: PluginContext, old: Optional[Session], new: Session) -> None:
        branch = self._get_git_branch(new.cwd)
        if branch:
            ctx.publish(f"term-launcher.session.{new.name}.git_branch", branch)

    def _get_git_branch(self, cwd: str) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=cwd,
                capture_output=True, timeout=5, text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None


class PersistQueuePlugin(TerminalPlugin):
    name = "persist-queue"
    version = "0.1.0"
    _save_interval = 30

    def __init__(self):
        super().__init__()
        self._save_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        self._ensure_save_thread(ctx)

    def on_session_close(self, ctx: PluginContext, session: Session) -> None:
        try:
            from main import save_session
        except ImportError:
            try:
                from terminal_launcher.src.main import save_session
            except ImportError:
                return
        save_session(session.name, session)

    def _ensure_save_thread(self, ctx: PluginContext) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._save_thread = threading.Thread(target=self._save_loop, args=(ctx,), daemon=True)
            self._save_thread.start()

    def _save_loop(self, ctx: PluginContext) -> None:
        import time as _time
        while self._running:
            _time.sleep(self._save_interval)
            ctx.launcher.persist_all()

    def set_running(self, running: bool) -> None:
        self._running = running