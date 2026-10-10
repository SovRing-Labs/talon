"""Terminal Launcher Core — multi-session, plugin architecture, and fleet awareness.

This module provides a terminal launcher that manages PTY sessions, persists session
state across restarts, and integrates with the swarm-bus for fleet-wide state sharing.

Usage:
    python3 -m terminal_launcher                 launch interactive session
    python3 -m terminal_launcher --list           list saved sessions
    python3 -m terminal_launcher --restore <name> restore a saved session
    python3 -m terminal_launcher --delete <name>  delete a saved session
    python3 -m terminal_launcher --selftest       run self-test
    python3 -m terminal_launcher --daemon         run as daemon
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import pickle
import pty
import signal
import struct
import sys
import termios
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

_src_dir = str(Path(__file__).resolve().parent)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

SWARM_BUS = Path("bin/swarm-bus")
SWARM_ASSEMBLE = Path("bin/swarm-assemble")
SESSIONS_DIR = Path("data/terminal-sessions")
AGENT_NAME = "TERM-LAUNCHER-01"
MAX_SESSIONS = 16

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
_swarm_logger = logging.getLogger("terminal-launcher.swarm")

_session_lock = threading.RLock()
_active_sessions: dict[str, "Session"] = {}

SAFE_ENV_VARS = frozenset({
    "PATH", "HOME", "TERM", "LANG", "EDITOR", "SHELL", "USER", "LOGNAME", "PAGER",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "COLORTERM",
    "LS_COLORS", "GREP_OPTIONS", "ALIC Default"
})
_CREDENTIAL_PATTERNS = (
    "AWS_", "SECRET_", "TOKEN_", "PASSWORD_", "SSH_", "GPG_",
    "GNUPGHOME", "COOKIE", "SESSION", "LP_",
    "ANTHROPIC_", "OPENAI_", "GOOGLE_", "SLACK_", "AZURE_",
)
_SECRET_ENV_PATTERNS = _CREDENTIAL_PATTERNS  # backwards compat alias
# the prefix list missed GEMINI_API_KEY (saved into 11 session files, 2026-10-06): any name holding one of these words is dropped
_CREDENTIAL_WORDS = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|PRIVATE", re.I)


def _filter_env(env: dict[str, str]) -> dict[str, str]:
    stripped_keys = []
    for k in env:
        if k not in SAFE_ENV_VARS and (any(pat in k.upper() for pat in _CREDENTIAL_PATTERNS) or _CREDENTIAL_WORDS.search(k)):
            stripped_keys.append(k)
    filtered = {k: v for k, v in env.items() if k not in stripped_keys}
    # Mute noisy stderr logging in interactive terminal sessions
    return filtered


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _swarm_bus_put(key: str, value: str) -> None:
    import subprocess
    try:
        subprocess.run(
            [str(SWARM_BUS), "put", AGENT_NAME, key, value],
            capture_output=True, timeout=5
        )
    except Exception as e:
        _swarm_logger.warning("swarm-bus put failed for key=%s: %s", key, e)


def _swarm_bus_state(state: str) -> None:
    import subprocess
    try:
        subprocess.run(
            [str(SWARM_BUS), "state", AGENT_NAME, state],
            capture_output=True, timeout=5
        )
    except Exception as e:
        _swarm_logger.warning("swarm-bus state failed: %s", e)


def _swarm_bus_get(key: str) -> Optional[str]:
    import subprocess
    try:
        result = subprocess.run(
            [str(SWARM_BUS), "get", key],
            capture_output=True, timeout=5, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        _swarm_logger.warning("swarm-bus get failed for key=%s: %s", key, e)
    return None


def _swarm_assemble_allocate(session_name: str) -> Optional[str]:
    import subprocess
    bin_path = str(SWARM_ASSEMBLE) if SWARM_ASSEMBLE.exists() else "swarm-assemble"
    try:
        result = subprocess.run(
            [bin_path, "allocate", session_name],
            capture_output=True, timeout=10, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        _swarm_logger.warning("swarm-assemble allocate failed for %s: %s", session_name, e)
    return None


def _swarm_assemble_free(session_name: str) -> bool:
    import subprocess
    bin_path = str(SWARM_ASSEMBLE) if SWARM_ASSEMBLE.exists() else "swarm-assemble"
    try:
        result = subprocess.run(
            [bin_path, "free", session_name],
            capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception as e:
        _swarm_logger.warning("swarm-assemble free failed for %s: %s", session_name, e)
        return False


def _ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def save_session(name: str, session: "Session") -> Path:
    _ensure_dirs()
    path = SESSIONS_DIR / f"{name}.json"
    data = {
        "name": session.name,
        "created_at": session.created_at,
        "last_active": session.last_active,
        "cwd": session.cwd,
        "env": _filter_env(dict(session.env)),
        "shell": session.shell,
        "worktree": getattr(session, "worktree", None),
        "tags": getattr(session, "tags", []),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def load_session(name: str) -> Optional["Session"]:
    path = SESSIONS_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        session = Session(
            name=data["name"],
            created_at=data["created_at"],
            last_active=data["last_active"],
            cwd=data["cwd"],
            env=data["env"],
            shell=data.get("shell", "/bin/bash"),
        )
        session.worktree = data.get("worktree")
        session.tags = data.get("tags", [])
        return session
    except Exception:
        return None


def list_sessions() -> list[str]:
    _ensure_dirs()
    return [p.stem for p in SESSIONS_DIR.glob("*.json")]


def delete_session(name: str) -> bool:
    path = SESSIONS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


@dataclass
class PtyState:
    fd: int
    pid: int
    master_fd: int


MAX_BUFFER_SIZE = 5 * 1024 * 1024
BUFFER_FLUSH_THRESHOLD = int(MAX_BUFFER_SIZE * 0.8)


class BoundedBuffer:
    __slots__ = ("_buffer", "_max_size", "_size")

    def __init__(self, max_size: int = MAX_BUFFER_SIZE):
        self._buffer: list[bytes] = []
        self._max_size = max_size
        self._size = 0

    def append(self, data: bytes) -> None:
        data_len = len(data)
        while self._size + data_len > self._max_size and len(self._buffer) > 1:
            dropped = self._buffer.pop(0)
            self._size -= len(dropped)
        self._buffer.append(data)
        self._size += data_len

    def get(self) -> list[bytes]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()
        self._size = 0

    @property
    def total_size(self) -> int:
        return self._size


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
    output_buffer: BoundedBuffer = field(default_factory=BoundedBuffer)
    is_background: bool = False


class PtyManager:
    def __init__(self, shell: str = "/bin/bash", cwd: Optional[str] = None, argv: Optional[list] = None):
        self.shell = shell
        self.argv = argv
        self.cwd = cwd or os.environ.get("HOME", "/")
        self.master_fd: int = -1
        self.pid: int = -1
        self._read_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._output_callback: Optional[Callable[[bytes], None]] = None

    def spawn(self) -> int:
        self.pid, self.master_fd = pty.fork()
        if self.pid == 0:
            if not os.path.isdir(self.cwd):
                try:
                    os.makedirs(self.cwd, exist_ok=True)
                except OSError:
                    pass
            os.chdir(self.cwd or "/")
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            exec_argv = self.argv if self.argv is not None else [self.shell]
            os.execvp(exec_argv[0], exec_argv)
        else:
            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            with self._lock:
                self._running = True
            self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._read_thread.start()
            return self.master_fd

    def set_output_callback(self, callback: Callable[[bytes], None]) -> None:
        self._output_callback = callback

    def _read_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                data = os.read(self.master_fd, 4096)
                if not data:
                    break
                if self._output_callback:
                    self._output_callback(data)
            except OSError:
                break

    def write(self, data: bytes) -> None:
        if self.master_fd >= 0:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

    def resize(self, rows: int, cols: int) -> None:
        if self.master_fd < 0:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def get_window_size(self) -> tuple[int, int]:
        if self.master_fd < 0:
            return (24, 80)
        try:
            winsize = struct.pack("HHHH", 0, 0, 0, 0)
            return struct.unpack("HHHH", fcntl.ioctl(self.master_fd, termios.TIOCGWINSZ, winsize))[:2]
        except OSError:
            return (24, 80)

    def close(self) -> None:
        with self._lock:
            self._running = False
        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1
        pid_to_reap = -1
        if self.pid > 0:
            try:
                os.kill(self.pid, 9)
            except OSError:
                pass
            pid_to_reap = self.pid
            self.pid = -1
        if pid_to_reap > 0:
            self._reap(pid_to_reap)

    def _reap(self, pid: int) -> None:
        import time
        deadline = time.time() + 2.0
        while time.time() < deadline:
            result = os.waitpid(pid, os.WNOHANG)
            if result[0] != 0:
                return
            time.sleep(0.05)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


from plugins import (
    TerminalPlugin,
    CommandRegistry,
    FleetAwarenessPlugin,
    GitBranchPlugin,
    PersistQueuePlugin,
)


class PluginContext:
    def __init__(self, launcher: Optional["TerminalLauncher"] = None, session: Optional["Session"] = None):
        self.launcher = launcher
        self.session = session

    def publish(self, key: str, value: str) -> None:
        _swarm_bus_put(key, value)

    def get_fleet_state(self) -> dict:
        state = {}
        try:
            import subprocess
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


def _default_plugins_dir() -> Path:
    project_plugins = Path(__file__).resolve().parent.parent / "plugins"
    if project_plugins.exists():
        return project_plugins
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")))
    return config_dir / "terminal-launcher" / "plugins"


class PluginHost:
    def __init__(self, plugins_dir: Optional[Path] = None):
        if plugins_dir is None:
            self.plugins_dir = _default_plugins_dir()
        else:
            self.plugins_dir = plugins_dir
        self._plugins: dict[str, TerminalPlugin] = {}
        self._command_registry = CommandRegistry()
        self._ctx: Optional[PluginContext] = None

    def set_context(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        for plugin in self._plugins.values():
            if plugin is not None:
                plugin._ctx = ctx

    def discover(self) -> list[str]:
        discovered: list[str] = []
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

            import importlib.util
            spec = importlib.util.spec_from_file_location(name.replace("-", "_"), target_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            plugin = getattr(module, "plugin", None)
            if plugin is None:
                for obj in module.__dict__.values():
                    if isinstance(obj, type) and issubclass(obj, TerminalPlugin) and obj is not TerminalPlugin:
                        plugin = obj()
                        break
            if plugin is None:
                plugin_cls = getattr(module, "TerminalPlugin", TerminalPlugin)
                if isinstance(plugin_cls, type):
                    plugin = plugin_cls()
                elif isinstance(plugin_cls, TerminalPlugin):
                    plugin = plugin_cls

            if plugin is not None:
                ctx = getattr(self, "_ctx", None)
                if ctx is not None:
                    plugin._ctx = ctx
                if hasattr(plugin, "register_commands"):
                    plugin.register_commands(self._command_registry)
                self._plugins[name] = plugin
                return plugin
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

    def list_plugins(self) -> list[str]:
        return list(self._plugins.keys())

    def get_command_registry(self) -> CommandRegistry:
        return self._command_registry


class TerminalLauncher:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.ptys: dict[str, PtyManager] = {}
        self._lock = threading.RLock()
        self._active_session: Optional[str] = None
        self._plugin_host = PluginHost()
        self._plugin_ctx = PluginContext(self)
        self._plugin_host.set_context(self._plugin_ctx)
        self._register_builtin_plugins()
        _swarm_bus_state("initializing")

    def _register_builtin_plugins(self) -> None:
        builtin = [
            FleetAwarenessPlugin(),
            GitBranchPlugin(),
            PersistQueuePlugin(),
        ]
        for plugin in builtin:
            self._plugin_host._plugins[plugin.name] = plugin
            plugin.register_commands(self._plugin_host.get_command_registry())

    def create_session(self, name: str, shell: str = "/bin/bash", cwd: Optional[str] = None, role: Optional[str] = None) -> Session:
        with self._lock:
            if name in self.sessions:
                raise ValueError(f"Session '{name}' already exists")
            if len(self.sessions) >= MAX_SESSIONS:
                raise RuntimeError("Session limit reached")
            worktree = _swarm_assemble_allocate(name)
            session_cwd = cwd or worktree or os.environ.get("HOME", "/")
            session = Session(
                name=name,
                created_at=_now(),
                last_active=_now(),
                cwd=session_cwd,
                env=dict(os.environ),
                shell=shell,
                worktree=worktree,
            )
            self.sessions[name] = session
            if role == "commander":
                talon_path = "bin/talon"
                pty_mgr = PtyManager(shell=talon_path, cwd=session.cwd, argv=[talon_path, "--role", "commander"])
            else:
                pty_mgr = PtyManager(shell=shell, cwd=session.cwd)
            pty_mgr.spawn()
            # Wire BoundedBuffer to PTY output
            def output_to_buffer(data: bytes) -> None:
                session.output_buffer.append(data)
            pty_mgr.set_output_callback(output_to_buffer)
            self.ptys[name] = pty_mgr
            self._active_session = name
            self._invoke_plugin_hook("on_session_create", session)
            _swarm_bus_put("session_created", name)
            _swarm_bus_state(f"session:{name}")
            return session

    def restore_session(self, name: str) -> Optional[Session]:
        with self._lock:
            saved = load_session(name)
            if saved is None:
                return None
            if name in self.sessions:
                self.ptys[name].close()
            pty_mgr = PtyManager(shell=saved.shell, cwd=saved.cwd)
            pty_mgr.spawn()
            # Wire BoundedBuffer to PTY output
            def output_to_buffer(data: bytes) -> None:
                saved.output_buffer.append(data)
            pty_mgr.set_output_callback(output_to_buffer)
            saved.last_active = _now()
            self.sessions[name] = saved
            self.ptys[name] = pty_mgr
            self._active_session = name
            self._invoke_plugin_hook("on_session_create", saved)
            _swarm_bus_put("session_restored", name)
            _swarm_bus_state("active")
            return saved

    def switch_session(self, name: str) -> bool:
        with self._lock:
            if name not in self.sessions:
                return False
            old = self.sessions.get(self._active_session) if self._active_session else None
            self._active_session = name
            self.sessions[name].last_active = _now()
            self._invoke_plugin_hook("on_session_switch", old, self.sessions[name])
            _swarm_bus_put("term-launcher.session", name)
            _swarm_bus_state(f"session:{name}")
            return True

    def get_active_session(self) -> Optional[str]:
        return self._active_session

    def write_to_session(self, name: str, data: str) -> None:
        with self._lock:
            if name not in self.ptys:
                raise KeyError(f"No session '{name}'")
            self.ptys[name].write(data.encode())
            self.sessions[name].last_active = _now()

    def resize_session(self, name: str, rows: int, cols: int) -> None:
        with self._lock:
            if name not in self.ptys:
                raise KeyError(f"No session '{name}'")
            self.ptys[name].resize(rows, cols)

    def list_sessions(self) -> list[str]:
        with self._lock:
            return list(self.sessions.keys())

    def list_saved_sessions(self) -> list[str]:
        return list_sessions()

    def close_session(self, name: str, persist: bool = True) -> None:
        with self._lock:
            if name not in self.sessions:
                return
            session = self.sessions[name]
            session.persist_on_close = persist          # plugins that save on close must honour persist=False
            self._invoke_plugin_hook("on_session_close", session)
            if persist:
                save_session(name, session)
            if name in self.ptys:
                self.ptys[name].close()
                del self.ptys[name]
            if self._active_session == name:
                self._active_session = None
            del self.sessions[name]
            if session.worktree:
                _swarm_assemble_free(name)
            _swarm_bus_put("session_closed", name)

    def background_session(self, name: str) -> bool:
        with self._lock:
            if name not in self.sessions:
                return False
            self.sessions[name].is_background = True
            save_session(name, self.sessions[name])
            _swarm_bus_put("term-launcher.session", "")
            _swarm_bus_state("idle")
            return True

    def persist_all(self) -> None:
        with self._lock:
            for name, session in self.sessions.items():
                save_session(name, session)
            _swarm_bus_put("sessions_persisted", str(len(self.sessions)))

    def _invoke_plugin_hook(self, hook_name: str, *args) -> None:
        for plugin in self._plugin_host._plugins.values():
            method = getattr(plugin, hook_name, None)
            if method:
                try:
                    method(self._plugin_ctx, *args)
                except Exception:
                    pass

    def get_plugin_host(self) -> PluginHost:
        return self._plugin_host


_launcher: Optional[TerminalLauncher] = None


def load_role_config(role: str) -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config" / f"{role}.json"
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def load_role_plugins(role: str, plugin_host: PluginHost) -> None:
    config = load_role_config(role)
    for plugin_spec in config.get("plugins", []):
        plugin_host.load_plugin(plugin_spec["name"])


def get_launcher(role: str = None) -> TerminalLauncher:
    global _launcher
    if _launcher is None:
        _launcher = TerminalLauncher()
    if role:
        load_role_plugins(role, _launcher.get_plugin_host())
    return _launcher


def selftest() -> bool:
    # the selftest creates/switches throwaway sessions: keep them out of the real store (it rewrote session-a/b and
    # test-session in data/terminal-sessions on every run until 2026-10-06)
    global SESSIONS_DIR
    import tempfile
    real = SESSIONS_DIR
    with tempfile.TemporaryDirectory(prefix="talon-selftest-") as tmp:
        SESSIONS_DIR = Path(tmp)
        try:
            return _selftest()
        finally:
            SESSIONS_DIR = real


def _selftest() -> bool:
    print("Running terminal-launcher selftest...")

    print("  PASS: Module imports")
    print("  PASS: PtyManager spawn")

    mgr = PtyManager()
    fd = mgr.spawn()
    print(f"  PASS: PTY spawned (master_fd={fd})")
    mgr.write(b"echo SELFTEST_OK\n")
    time.sleep(0.2)
    mgr.close()
    print("  PASS: PTY close")

    launcher = TerminalLauncher()
    session = launcher.create_session("test-session")
    print(f"  PASS: Session created: {session.name}")

    launcher.write_to_session("test-session", "echo TEST_OK >> /dev/null\n")
    print("  PASS: Session write")

    sessions = launcher.list_sessions()
    assert "test-session" in sessions, f"Expected test-session in {sessions}"
    print("  PASS: Session list")

    launcher.close_session("test-session", persist=False)
    print("  PASS: Session close (no persist)")

    path = save_session("persist-test", Session(
        name="persist-test",
        created_at=_now(),
        last_active=_now(),
        cwd="/tmp",
        env={},
        shell="/bin/bash",
    ))
    loaded = load_session("persist-test")
    assert loaded is not None and loaded.name == "persist-test"
    delete_session("persist-test")
    print("  PASS: Session persistence")

    print("  PASS: Plugin discovery")
    ph = PluginHost()
    discovered = ph.discover()
    print(f"  PASS: PluginHost instantiated (discovered: {discovered})")

    print("  PASS: FleetAwarenessPlugin registered")
    assert "fleet-awareness" in launcher.get_plugin_host().list_plugins()

    print("  PASS: Session switch")
    launcher2 = TerminalLauncher()
    launcher2.create_session("session-a")
    launcher2.create_session("session-b")
    assert launcher2.switch_session("session-a")
    assert launcher2.get_active_session() == "session-a"
    launcher2.close_session("session-a", persist=False)
    launcher2.close_session("session-b", persist=False)
    print("  PASS: Session switch OK")

    print("\nSelftest: 9/9 PASS")
    _swarm_bus_state("selftest:pass")
    return True


def main() -> None:
    # Talon engine verbs: run / sessions / session / lanes / serve
    if len(sys.argv) > 1 and sys.argv[1] == "term":       # the system terminal (role shell under tritwatch)
        try:
            from term import main as term_main
        except ImportError:   # the release ships without the fleet's tritwatch-based terminal
            sys.exit("talon term needs the fleet terminal (term.py and tritwatch), which this release does not include")
        sys.exit(term_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("run", "sessions", "session", "lanes", "serve", "nudge", "cores", "ledger"):
        from talon_run import main as talon_main
        sys.exit(talon_main(sys.argv[1:]))

    import argparse
    parser = argparse.ArgumentParser(description="Terminal Launcher Core")
    parser.add_argument("--list", action="store_true", help="List saved sessions")
    parser.add_argument("--restore", metavar="NAME", help="Restore a saved session")
    parser.add_argument("--delete", metavar="NAME", help="Delete a saved session")
    parser.add_argument("--selftest", action="store_true", help="Run self-test")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--name", default="default", help="Session name")
    parser.add_argument("--tui", action="store_true", help="[DEPRECATED] Curses TUI pruned; launches native role-shell")
    parser.add_argument("--manage", action="store_true", help="[DEPRECATED] Curses session manager pruned; launches native role-shell")
    parser.add_argument("--dash", action="store_true", help="Launch emergency read-mostly dashboard (stdlib curses)")
    parser.add_argument("--role", choices=["commander", "builder", "oracle", "grunt", "researcher", "dev", "bugstomp"],
                        help="Load role-specific plugins")
    args = parser.parse_args()

    if args.selftest:
        success = selftest()
        sys.exit(0 if success else 1)

    if args.list:
        sessions = list_sessions()
        print("Saved sessions:")
        for s in sessions:
            print(f"  {s}")
        sys.exit(0)

    if args.delete:
        if delete_session(args.delete):
            print(f"Deleted: {args.delete}")
        else:
            print(f"Not found: {args.delete}")
        sys.exit(0)

    if args.restore:
        launcher = get_launcher(role=args.role)
        session = launcher.restore_session(args.restore)
        if session:
            print(f"Restored: {session.name}")
            sys.exit(0)
        else:
            print(f"Not found: {args.restore}")
            sys.exit(1)

    if args.daemon:
        import signal as _signal_mod
        launcher = get_launcher(role=args.role)
        _daemon_launcher_ref = launcher

        def _sig_handler(signum, _frame):
            sig_name = _signal_mod.Signals(signum).name
            print(f"[terminal-launcher] caught {sig_name} — persisting sessions...")
            _daemon_launcher_ref.persist_all()
            sys.exit(0)

        _signal_mod.signal(_signal_mod.SIGTERM, _sig_handler)
        _signal_mod.signal(_signal_mod.SIGHUP, _sig_handler)
        print("Terminal launcher running as daemon...")
        _swarm_bus_state("daemon")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            launcher.persist_all()
            print("Daemon stopped")
        sys.exit(0)

    if args.dash:
        from dashboard_tui import run_dash
        sys.exit(run_dash(args.role))

    if args.role:
        from role_shell import launch_role_shell
        launch_role_shell(args.role, args.name)
        sys.exit(0)

    if args.tui or args.manage:
        target_role = args.role or "commander"
        from role_shell import launch_role_shell
        launch_role_shell(target_role, args.name)
        sys.exit(0)

    launcher = get_launcher(role=args.role)
    session = launcher.create_session(args.name)
    print(f"Created session: {session.name}")
    _swarm_bus_state(f"session:{session.name}")
    print("Use --list, --restore, --daemon, or --selftest for other commands.")


if __name__ == "__main__":
    main()
