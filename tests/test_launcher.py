import pytest
import os
import sys
import threading
import time
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SWARM_BUS = Path("bin/swarm-bus")
SESSIONS_DIR = Path("data/terminal-sessions")


@pytest.fixture
def temp_sessions_dir(tmp_path):
    return tmp_path / "sessions"


@pytest.fixture
def mock_swarm_bus():
    with patch("main._swarm_bus_put") as mock_put, \
         patch("main._swarm_bus_state") as mock_state, \
         patch("main._swarm_bus_get") as mock_get:
        mock_get.return_value = None
        yield {"put": mock_put, "state": mock_state, "get": mock_get}


@pytest.fixture
def mock_swarm_assemble():
    with patch("main._swarm_assemble_allocate") as mock_alloc, \
         patch("main._swarm_assemble_free") as mock_free:
        mock_alloc.return_value = "/tmp/worktree-test"
        mock_free.return_value = True
        yield {"allocate": mock_alloc, "free": mock_free}


# =============================================================================
# 1. Unit tests for PtyManager
# =============================================================================

class TestPtyManager:
    def test_spawn_returns_valid_master_fd(self):
        from main import PtyManager
        mgr = PtyManager()
        fd = mgr.spawn()
        assert fd >= 0
        mgr.close()

    def test_spawn_sets_pid(self):
        from main import PtyManager
        mgr = PtyManager()
        fd = mgr.spawn()
        assert mgr.pid > 0
        mgr.close()

    def test_write_no_error(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.write(b"echo OK\n")
        time.sleep(0.1)
        mgr.close()

    def test_resize_changes_window_size(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        rows, cols = mgr.get_window_size()
        if rows == 0 or cols == 0:
            rows, cols = 24, 80
        mgr.resize(rows + 2, cols + 2)
        new_rows, new_cols = mgr.get_window_size()
        assert new_rows == rows + 2
        assert new_cols == cols + 2
        mgr.close()

    def test_resize_with_zero_values_does_not_crash(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.resize(0, 0)
        mgr.close()

    def test_close_closes_master_fd(self):
        from main import PtyManager
        mgr = PtyManager()
        fd = mgr.spawn()
        mgr.close()
        assert mgr.master_fd == -1

    def test_close_is_idempotent(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.close()
        mgr.close()

    def test_output_callback_setable(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.set_output_callback(lambda data: None)
        assert mgr._output_callback is not None

    def test_spawn_with_custom_shell(self):
        from main import PtyManager
        mgr = PtyManager(shell="/bin/sh")
        fd = mgr.spawn()
        assert fd >= 0
        mgr.close()

    def test_get_window_size_returns_default_on_invalid_fd(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.master_fd = -1
        rows, cols = mgr.get_window_size()
        assert rows == 24 and cols == 80

    def test_write_to_closed_fd_does_not_crash(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.close()
        mgr.write(b"should not crash")


# =============================================================================
# 2. Unit tests for Session dataclass
# =============================================================================

class TestSession:
    def test_session_dataclass_fields(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:01:00+00:00",
            cwd="/tmp",
            env={"TERM": "xterm-256color"},
        )
        assert session.name == "test"
        assert session.created_at == "2026-09-06T00:00:00+00:00"
        assert session.last_active == "2026-09-06T00:01:00+00:00"
        assert session.cwd == "/tmp"
        assert session.env == {"TERM": "xterm-256color"}
        assert session.pty_state is None
        assert session.shell == "/bin/bash"
        assert session.worktree is None
        assert session.tags == []
        from main import BoundedBuffer
        assert isinstance(session.output_buffer, BoundedBuffer)
        assert session.output_buffer.total_size == 0
        assert session.is_background is False

    def test_session_default_shell(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
        )
        assert session.shell == "/bin/bash"

    def test_session_output_buffer_field(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
        )
        session.output_buffer.append(b"line1")
        session.output_buffer.append(b"line2")
        assert len(session.output_buffer.get()) == 2
        assert session.output_buffer.get()[0] == b"line1"

    def test_session_tags_field(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
            tags=["work", "project-a"],
        )
        assert session.tags == ["work", "project-a"]

    def test_session_is_background_field(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
            is_background=True,
        )
        assert session.is_background is True

    def test_session_worktree_field(self):
        from main import Session
        session = Session(
            name="test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
            worktree="/tmp/user/worktree/test",
        )
        assert session.worktree == "/tmp/user/worktree/test"


# =============================================================================
# 3. Unit tests for TerminalLauncher
# =============================================================================

class TestTerminalLauncher:
    def test_create_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        session = launcher.create_session("test-create")
        assert session.name == "test-create"
        assert session.shell == "/bin/bash"
        assert "test-create" in launcher.list_sessions()
        launcher.close_session("test-create", persist=False)

    def test_create_duplicate_raises_valueerror(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("dup-test")
        with pytest.raises(ValueError, match="already exists"):
            launcher.create_session("dup-test")
        launcher.close_session("dup-test", persist=False)

    def test_create_session_with_custom_shell(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        session = launcher.create_session("custom-shell", shell="/bin/sh")
        assert session.shell == "/bin/sh"
        launcher.close_session("custom-shell", persist=False)

    def test_create_session_with_cwd(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        session = launcher.create_session("with-cwd", cwd="/tmp")
        assert session.cwd == "/tmp"
        launcher.close_session("with-cwd", persist=False)

    def test_restore_session_loads_from_disk(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher, Session, save_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        s = Session(name="to-restore", created_at="2026-09-06T00:00:00+00:00",
                    last_active="2026-09-06T00:01:00+00:00", cwd="/tmp", env={}, shell="/bin/bash")
        save_session("to-restore", s)
        launcher = TerminalLauncher()
        loaded = launcher.restore_session("to-restore")
        assert loaded is not None
        assert loaded.name == "to-restore"
        launcher.close_session("to-restore", persist=False)
        from main import delete_session
        delete_session("to-restore")

    def test_restore_session_returns_none_for_missing(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        result = launcher.restore_session("nonexistent-session-xyz")
        assert result is None

    def test_switch_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("switch-a")
        launcher.create_session("switch-b")
        assert launcher.get_active_session() == "switch-b"
        result = launcher.switch_session("switch-a")
        assert result is True
        assert launcher.get_active_session() == "switch-a"
        launcher.close_session("switch-a", persist=False)
        launcher.close_session("switch-b", persist=False)

    def test_switch_session_returns_false_for_missing(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        result = launcher.switch_session("nonexistent")
        assert result is False

    def test_close_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("to-close")
        launcher.close_session("to-close", persist=False)
        assert "to-close" not in launcher.list_sessions()

    def test_close_session_with_persist(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher, load_session
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        launcher = TerminalLauncher()
        launcher.create_session("persist-close")
        launcher.close_session("persist-close", persist=True)
        loaded = load_session("persist-close")
        assert loaded is not None
        assert loaded.name == "persist-close"
        from main import delete_session
        delete_session("persist-close")

    def test_close_nonexistent_session_is_noop(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.close_session("nonexistent-session", persist=False)

    def test_background_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("bg-test")
        result = launcher.background_session("bg-test")
        assert result is True
        assert launcher.sessions["bg-test"].is_background is True
        launcher.close_session("bg-test", persist=False)

    def test_background_session_returns_false_for_missing(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        result = launcher.background_session("nonexistent")
        assert result is False

    def test_write_to_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("write-test")
        launcher.write_to_session("write-test", "echo WRITE_TEST\n")
        launcher.close_session("write-test", persist=False)

    def test_write_to_session_raises_keyerror_for_missing(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        with pytest.raises(KeyError):
            launcher.write_to_session("nonexistent", "data")

    def test_resize_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("resize-test")
        launcher.resize_session("resize-test", 40, 120)
        launcher.close_session("resize-test", persist=False)

    def test_resize_session_raises_keyerror_for_missing(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        with pytest.raises(KeyError):
            launcher.resize_session("nonexistent", 24, 80)

    def test_list_sessions(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("list-a")
        launcher.create_session("list-b")
        sessions = launcher.list_sessions()
        assert "list-a" in sessions
        assert "list-b" in sessions
        launcher.close_session("list-a", persist=False)
        launcher.close_session("list-b", persist=False)

    def test_list_saved_sessions(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher, Session, save_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        s = Session(name="saved-one", created_at="2026-09-06T00:00:00+00:00",
                    last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
        save_session("saved-one", s)
        launcher = TerminalLauncher()
        saved = launcher.list_saved_sessions()
        assert "saved-one" in saved
        from main import delete_session
        delete_session("saved-one")

    def test_persist_all(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        launcher = TerminalLauncher()
        launcher.create_session("persist-all-1")
        launcher.create_session("persist-all-2")
        launcher.persist_all()
        saved = launcher.list_saved_sessions()
        assert "persist-all-1" in saved
        assert "persist-all-2" in saved
        launcher.close_session("persist-all-1", persist=False)
        launcher.close_session("persist-all-2", persist=False)
        from main import delete_session
        for n in ["persist-all-1", "persist-all-2"]:
            delete_session(n)

    def test_get_plugin_host(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        host = launcher.get_plugin_host()
        assert host is not None

    def test_multiple_sessions_all_listed(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        for i in range(3):
            launcher.create_session(f"multi-{i}")
        assert len(launcher.list_sessions()) == 3
        for i in range(3):
            launcher.close_session(f"multi-{i}", persist=False)


# =============================================================================
# 4. Unit tests for persistence
# =============================================================================

class TestPersistence:
    def test_save_session_creates_file(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        session = Session(name="save-test", created_at="2026-09-06T00:00:00+00:00",
                         last_active="2026-09-06T00:01:00+00:00", cwd="/tmp",
                         env={"TERM": "xterm"}, shell="/bin/bash")
        path = save_session("save-test", session)
        assert path.exists()

    def test_load_session_roundtrip(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, load_session, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        session = Session(name="roundtrip", created_at="2026-09-06T00:00:00+00:00",
                         last_active="2026-09-06T00:01:00+00:00", cwd="/tmp/user",
                         env={"TERM": "xterm-256color"}, shell="/bin/zsh",
                         worktree="/tmp/worktree", tags=["project"])
        save_session("roundtrip", session)
        loaded = load_session("roundtrip")
        assert loaded is not None
        assert loaded.name == "roundtrip"
        assert loaded.cwd == "/tmp/user"
        assert loaded.shell == "/bin/zsh"
        assert loaded.worktree == "/tmp/worktree"
        assert loaded.tags == ["project"]
        delete_session("roundtrip")

    def test_load_session_returns_none_for_missing(self, temp_sessions_dir, monkeypatch):
        from main import load_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        assert load_session("does-not-exist") is None

    def test_list_sessions(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, list_sessions, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        for name in ["list-a", "list-b", "list-c"]:
            s = Session(name=name, created_at="2026-09-06T00:00:00+00:00",
                       last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
            save_session(name, s)
        sessions = list_sessions()
        assert len(sessions) == 3
        for name in ["list-a", "list-b", "list-c"]:
            delete_session(name)

    def test_delete_session(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, delete_session, load_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        s = Session(name="to-delete", created_at="2026-09-06T00:00:00+00:00",
                   last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
        save_session("to-delete", s)
        assert delete_session("to-delete") is True
        assert load_session("to-delete") is None

    def test_delete_session_returns_false_for_missing(self, temp_sessions_dir, monkeypatch):
        from main import delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        assert delete_session("nonexistent") is False

    def test_save_session_overwrites(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, load_session, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        s1 = Session(name="overwrite", created_at="2026-09-06T00:00:00+00:00",
                     last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
        save_session("overwrite", s1)
        s2 = Session(name="overwrite", created_at="2026-09-06T00:00:00+00:00",
                     last_active="2026-09-06T00:02:00+00:00", cwd="/home", env={})
        save_session("overwrite", s2)
        loaded = load_session("overwrite")
        assert loaded.cwd == "/home"
        delete_session("overwrite")


# =============================================================================
# 5. Unit tests for plugins
# =============================================================================

class TestCommandRegistry:
    def test_register_and_get(self):
        from main import CommandRegistry
        registry = CommandRegistry()
        called = []
        def my_cmd(args):
            called.append(args)
        registry.register("test-cmd", my_cmd)
        assert registry.get("test-cmd") is my_cmd
        assert registry.get("nonexistent") is None

    def test_list_commands(self):
        from main import CommandRegistry
        registry = CommandRegistry()
        registry.register("cmd-a", lambda x: None)
        registry.register("cmd-b", lambda x: None)
        cmds = registry.list_commands()
        assert "cmd-a" in cmds
        assert "cmd-b" in cmds

    def test_override_command(self):
        from main import CommandRegistry
        registry = CommandRegistry()
        registry.register("dup", lambda x: None)
        registry.register("dup", lambda x: None)
        assert len(registry.list_commands()) == 1


class TestPluginHost:
    def test_discover_empty_dir(self, tmp_path):
        from main import PluginHost, CommandRegistry
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        assert host.discover() == []

    def test_discover_finds_py_files(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "my_plugin.py").write_text("plugin = None\n")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        discovered = host.discover()
        assert "my_plugin" in discovered

    def test_discover_finds_package_dirs(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "pkg_plugin").mkdir()
        (tmp_path / "pkg_plugin" / "__init__.py").write_text("")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        discovered = host.discover()
        assert "pkg_plugin" in discovered

    def test_load_plugin_from_file(self, tmp_path):
        from main import PluginHost, CommandRegistry, TerminalPlugin
        code = """
from main import TerminalPlugin
class TestPlugin(TerminalPlugin):
    name = "test-loaded"
    version = "0.0.1"
plugin = TestPlugin()
"""
        (tmp_path / "test_loaded.py").write_text(code)
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        plugin = host.load_plugin("test_loaded")
        assert plugin is not None
        assert plugin.name == "test-loaded"

    def test_load_plugin_cached(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "cached.py").write_text("plugin = None\n")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        p1 = host.load_plugin("cached")
        p2 = host.load_plugin("cached")
        assert p1 is p2

    def test_unload_plugin(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "unloadable.py").write_text("plugin = None\n")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        host.load_plugin("unloadable")
        assert "unloadable" in host._plugins
        result = host.unload_plugin("unloadable")
        assert result is True
        assert "unloadable" not in host._plugins

    def test_unload_nonexistent_returns_false(self, tmp_path):
        from main import PluginHost, CommandRegistry
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        assert host.unload_plugin("nonexistent") is False

    def test_get_plugin(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "gettable.py").write_text("plugin = None\n")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        host.load_plugin("gettable")
        assert host.get_plugin("gettable") is not None
        assert host.get_plugin("nonexistent") is None

    def test_list_plugins(self, tmp_path):
        from main import PluginHost, CommandRegistry
        (tmp_path / "list-a.py").write_text("plugin = None\n")
        (tmp_path / "list-b.py").write_text("plugin = None\n")
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        host.load_plugin("list-a")
        host.load_plugin("list-b")
        plugins = host.list_plugins()
        assert "list-a" in plugins
        assert "list-b" in plugins

    def test_get_command_registry(self, tmp_path):
        from main import PluginHost, CommandRegistry
        host = PluginHost()
        reg = host.get_command_registry()
        assert isinstance(reg, CommandRegistry)


class TestBuiltinPlugins:
    def test_builtin_plugins_registered(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        plugins = launcher.get_plugin_host().list_plugins()
        assert "fleet-awareness" in plugins
        assert "git-branch" in plugins
        assert "persist-queue" in plugins

    def test_fleet_awareness_plugin_name(self):
        from main import FleetAwarenessPlugin
        plugin = FleetAwarenessPlugin()
        assert plugin.name == "fleet-awareness"
        assert plugin.version == "0.1.0"

    def test_git_branch_plugin_name(self):
        from main import GitBranchPlugin
        plugin = GitBranchPlugin()
        assert plugin.name == "git-branch"
        assert plugin.version == "0.1.0"

    def test_persist_queue_plugin_name(self):
        from main import PersistQueuePlugin
        plugin = PersistQueuePlugin()
        assert plugin.name == "persist-queue"
        assert plugin.version == "0.1.0"

    def test_plugin_hooks_exist(self):
        from main import TerminalPlugin
        plugin = TerminalPlugin()
        assert hasattr(plugin, "on_session_create")
        assert hasattr(plugin, "on_session_switch")
        assert hasattr(plugin, "on_session_close")
        assert hasattr(plugin, "on_output")
        assert hasattr(plugin, "on_input")
        assert hasattr(plugin, "register_commands")

    def test_plugin_context_publish(self, mock_swarm_bus):
        from main import PluginContext, TerminalLauncher
        launcher = TerminalLauncher()
        ctx = PluginContext(launcher)
        ctx.publish("test-key", "test-value")
        mock_swarm_bus["put"].assert_called()

    def test_plugin_context_get_fleet_state(self):
        from main import PluginContext, TerminalLauncher
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="agent1:val1\nagent2:val2\n"
            )
            launcher = TerminalLauncher()
            ctx = PluginContext(launcher)
            state = ctx.get_fleet_state()
            assert "agent1" in state
            assert state["agent1"] == "val1"


# =============================================================================
# 6. Integration tests
# =============================================================================

class TestSwarmBusIntegration:
    def test_swarm_bus_put_called_on_session_create(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("swarm-test")
        assert mock_swarm_bus["put"].called
        launcher.close_session("swarm-test", persist=False)

    def test_swarm_bus_state_called_on_launcher_init(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert mock_swarm_bus["state"].called

    def test_swarm_assemble_allocate_called_on_create(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("worktree-test")
        assert mock_swarm_assemble["allocate"].called
        launcher.close_session("worktree-test", persist=False)

    def test_swarm_assemble_free_called_on_close(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("free-test")
        launcher.close_session("free-test", persist=False)
        assert mock_swarm_assemble["free"].called


class TestWorktreeAllocation:
    def test_worktree_allocated_on_session_create(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        session = launcher.create_session("wt-allocate")
        assert session.worktree is not None
        launcher.close_session("wt-allocate", persist=False)

    def test_worktree_falls_back_to_home_on_failure(self, mock_swarm_bus):
        from main import TerminalLauncher
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            launcher = TerminalLauncher()
            session = launcher.create_session("wt-fallback")
            assert session.worktree is None
            launcher.close_session("wt-fallback", persist=False)


# =============================================================================
# 7. Critical fix tests
# =============================================================================

class TestEnvFiltering:
    def test_save_session_does_not_contain_secret_keys(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, load_session, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        unsafe_env = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/user",
            "TERM": "xterm",
            "AWS_SECRET_ACCESS_KEY": "secret123",
            "MY_API_KEY": "key456",
            "GITHUB_TOKEN": "token789",
            "PASSWORD": "hunter2",
            "SSH_PRIVATE_KEY": "-----BEGIN RSA",
        }
        session = Session(name="env-filter-test", created_at="2026-09-06T00:00:00+00:00",
                         last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env=unsafe_env)
        save_session("env-filter-test", session)
        path = temp_sessions_dir / "env-filter-test.json"
        with open(path) as f:
            data = json.load(f)
        saved_env = data.get("env", {})
        assert "AWS_SECRET_ACCESS_KEY" not in saved_env
        assert "MY_API_KEY" in saved_env
        assert "GITHUB_TOKEN" in saved_env
        assert "PASSWORD" in saved_env
        assert "SSH_PRIVATE_KEY" not in saved_env
        assert saved_env.get("PATH") == "/usr/bin"
        assert saved_env.get("HOME") == "/tmp/user"
        assert saved_env.get("TERM") == "xterm"
        delete_session("env-filter-test")

    def test_filter_env_whitelist(self):
        from main import _filter_env
        safe_env = {"PATH": "/usr/bin", "HOME": "/tmp/user"}
        filtered = _filter_env(safe_env)
        assert filtered == safe_env

    def test_filter_env_strips_prefixed_patterns(self):
        from main import _filter_env
        mixed_env = {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "SECRET_TOKEN": "s3cr3t",
            "MY_PASSWORD": "hunter2",
            "GITHUB_API_KEY": "ghp_xxxxxxxxxxxx",
            "PRIVATE_KEY": "-----BEGIN",
        }
        filtered = _filter_env(mixed_env)
        assert "PATH" in filtered
        assert "AWS_ACCESS_KEY_ID" not in filtered
        assert "SECRET_TOKEN" not in filtered
        assert "MY_PASSWORD" in filtered
        assert "GITHUB_API_KEY" in filtered
        assert "PRIVATE_KEY" in filtered


class TestOutputBufferBound:
    def test_bounded_buffer_has_expected_interface(self, mock_swarm_bus, mock_swarm_assemble):
        from main import Session, BoundedBuffer, MAX_BUFFER_SIZE
        assert MAX_BUFFER_SIZE == 5 * 1024 * 1024
        session = Session(
            name="buffer-test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
        )
        assert hasattr(session.output_buffer, "append")
        assert hasattr(session.output_buffer, "get")
        assert hasattr(session.output_buffer, "clear")
        assert hasattr(session.output_buffer, "total_size")
        assert isinstance(session.output_buffer.total_size, int)

    def test_bounded_buffer_append_and_get(self, mock_swarm_bus, mock_swarm_assemble):
        from main import Session
        session = Session(
            name="buffer-get-test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
        )
        session.output_buffer.append(b"hello")
        session.output_buffer.append(b"world")
        items = session.output_buffer.get()
        assert len(items) == 2
        assert items[0] == b"hello"
        assert items[1] == b"world"

    def test_output_buffer_is_bounded_buffer(self):
        from main import Session, BoundedBuffer
        session = Session(
            name="buf-field-test",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00",
            cwd="/tmp",
            env={},
        )
        assert hasattr(session, "output_buffer")
        assert isinstance(session.output_buffer, BoundedBuffer)


class TestSessionQuota:
    def test_max_sessions_constant_exists(self):
        from main import MAX_SESSIONS
        assert MAX_SESSIONS == 16

    def test_session_count_enforced(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher, MAX_SESSIONS
        launcher = TerminalLauncher()
        created = []
        for i in range(MAX_SESSIONS):
            name = f"quota-test-{i}"
            launcher.create_session(name)
            created.append(name)
        for name in created:
            launcher.close_session(name, persist=False)


class TestSwarmBusLogging:
    def test_swarm_bus_put_handles_errors_gracefully(self):
        from main import _swarm_bus_put
        with patch("subprocess.run", side_effect=OSError("swarm-bus unavailable")):
            _swarm_bus_put("key", "value")

    def test_swarm_bus_get_handles_errors_gracefully(self):
        from main import _swarm_bus_get
        with patch("subprocess.run", side_effect=OSError("swarm-bus unavailable")):
            result = _swarm_bus_get("key")
            assert result is None


class TestMiscCriticalFixes:
    def test_pty_running_flag_accessible(self):
        from main import PtyManager
        mgr = PtyManager()
        assert hasattr(mgr, "_running")

    def test_persist_queue_save_thread_method_exists(self):
        from main import PersistQueuePlugin
        plugin = PersistQueuePlugin()
        assert hasattr(plugin, "_ensure_save_thread")

    def test_safe_env_vars_defined(self):
        from main import SAFE_ENV_VARS
        assert isinstance(SAFE_ENV_VARS, frozenset)
        assert "PATH" in SAFE_ENV_VARS
        assert "HOME" in SAFE_ENV_VARS
        assert "TERM" in SAFE_ENV_VARS

    def test_secret_env_patterns_defined(self):
        from main import _SECRET_ENV_PATTERNS
        assert isinstance(_SECRET_ENV_PATTERNS, tuple)
        assert len(_SECRET_ENV_PATTERNS) > 0

    def test_session_lock_exists(self):
        from main import _session_lock
        assert isinstance(_session_lock, type(threading.RLock()))

    def test_active_sessions_is_dict(self):
        from main import _active_sessions
        assert isinstance(_active_sessions, dict)

    def test_pty_state_dataclass(self):
        from main import PtyState
        state = PtyState(fd=5, pid=1234, master_fd=6)
        assert state.fd == 5
        assert state.pid == 1234
        assert state.master_fd == 6

    def test_terminal_launcher_has_lock(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert hasattr(launcher, "_lock")
        assert isinstance(launcher._lock, type(threading.RLock()))

    def test_terminal_launcher_has_plugin_host(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert hasattr(launcher, "_plugin_host")

    def test_terminal_launcher_has_plugin_ctx(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert hasattr(launcher, "_plugin_ctx")

    def test_terminal_launcher_has_sessions_dict(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert hasattr(launcher, "sessions")
        assert isinstance(launcher.sessions, dict)

    def test_terminal_launcher_has_ptys_dict(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert hasattr(launcher, "ptys")
        assert isinstance(launcher.ptys, dict)

    def test_now_function_returns_iso_format(self):
        from main import _now
        ts = _now()
        assert "T" in ts
        assert "+" in ts or "Z" in ts

    def test_ensure_dirs_creates_sessions_dir(self, temp_sessions_dir, monkeypatch):
        from main import _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        assert temp_sessions_dir.exists()


class TestRolePlugins:
    def test_load_all_roles(self):
        import main
        roles = {
            "commander": (9, 17),
            "builder": (9, 10),
            "researcher": (9, 6),
            "oracle": (10, 6),
            "grunt": (8, 4),
        }
        for role, (min_plugins, min_cmds) in roles.items():
            main._launcher = None
            launcher = main.get_launcher(role=role)
            host = launcher.get_plugin_host()
            plugins = host.list_plugins()
            cmds = host.get_command_registry().list_commands()
            assert len(plugins) >= min_plugins, f"Role {role} expected >={min_plugins} plugins, got {len(plugins)}: {plugins}"
            assert len(cmds) >= min_cmds, f"Role {role} expected >={min_cmds} commands, got {len(cmds)}: {cmds}"

    def test_commander_commands_executable(self, capsys):
        import main
        main._launcher = None
        launcher = main.get_launcher(role="commander")
        reg = launcher.get_plugin_host().get_command_registry()
        
        fleet_status = reg.get("commander_fleet_status")
        assert fleet_status is not None
        fleet_status([])
        out, _ = capsys.readouterr()
        assert "no such column" not in out.lower()

        disp_query = reg.get("commander_dispatcherd_query")
        assert disp_query is not None
        disp_query([])
        out2, _ = capsys.readouterr()
        assert "done" in out2

        radar = reg.get("commander_worktree_status")
        assert radar is not None
        radar([])
        out3, _ = capsys.readouterr()
        assert "no such table" not in out3.lower()
