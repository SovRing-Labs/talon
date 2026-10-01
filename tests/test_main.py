import pytest
import os
import sys
import threading
import time
import tempfile
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
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock_run


@pytest.fixture
def mock_swarm_assemble():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="/tmp/worktree-test", stderr="")
        yield mock_run


class TestPtyManager:
    def test_spawn_returns_master_fd(self):
        from main import PtyManager
        mgr = PtyManager()
        fd = mgr.spawn()
        assert fd >= 0
        mgr.close()

    def test_write_and_resize(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.write(b"echo OK\n")
        rows, cols = mgr.get_window_size()
        if rows == 0 or cols == 0:
            rows, cols = 24, 80
        assert rows > 0 and cols > 0
        mgr.resize(rows + 1, cols + 1)
        new_rows, new_cols = mgr.get_window_size()
        assert new_rows == rows + 1 and new_cols == cols + 1
        mgr.close()

    def test_close_idempotent(self):
        from main import PtyManager
        mgr = PtyManager()
        mgr.spawn()
        mgr.close()
        mgr.close()


class TestSessionPersistence:
    def test_save_and_load_session(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, load_session, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        session = Session(
            name="test-save-load",
            created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:01:00+00:00",
            cwd="/tmp",
            env={"TERM": "xterm-256color"},
            shell="/bin/bash",
        )
        path = save_session("test-save-load", session)
        assert path.exists()
        loaded = load_session("test-save-load")
        assert loaded is not None
        assert loaded.name == "test-save-load"
        assert loaded.cwd == "/tmp"
        assert loaded.shell == "/bin/bash"
        delete_session("test-save-load")
        assert load_session("test-save-load") is None

    def test_list_sessions(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, list_sessions, delete_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        for name in ["session-a", "session-b", "session-c"]:
            s = Session(name=name, created_at="2026-09-06T00:00:00+00:00",
                       last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
            save_session(name, s)
        sessions = list_sessions()
        assert len(sessions) == 3
        assert "session-a" in sessions
        for name in ["session-a", "session-b", "session-c"]:
            delete_session(name)

    def test_delete_session(self, temp_sessions_dir, monkeypatch):
        from main import Session, save_session, delete_session, load_session, _ensure_dirs
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        _ensure_dirs()
        s = Session(name="to-delete", created_at="2026-09-06T00:00:00+00:00",
                   last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={})
        save_session("to-delete", s)
        assert delete_session("to-delete") is True
        assert delete_session("nonexistent") is False


class TestTerminalLauncher:
    def test_create_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        session = launcher.create_session("test-create")
        assert session.name == "test-create"
        assert "test-create" in launcher.list_sessions()
        launcher.close_session("test-create", persist=False)

    def test_create_duplicate_raises(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("dup-test")
        with pytest.raises(ValueError, match="already exists"):
            launcher.create_session("dup-test")
        launcher.close_session("dup-test", persist=False)

    def test_write_to_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("write-test")
        launcher.write_to_session("write-test", "echo WRITE_TEST\n")
        launcher.close_session("write-test", persist=False)

    def test_resize_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("resize-test")
        launcher.resize_session("resize-test", 40, 120)
        launcher.close_session("resize-test", persist=False)

    def test_switch_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("switch-a")
        launcher.create_session("switch-b")
        assert launcher.get_active_session() == "switch-b"
        assert launcher.switch_session("switch-a") is True
        assert launcher.get_active_session() == "switch-a"
        launcher.close_session("switch-a", persist=False)
        launcher.close_session("switch-b", persist=False)

    def test_background_session(self, mock_swarm_bus, mock_swarm_assemble):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        launcher.create_session("bg-test")
        assert launcher.background_session("bg-test") is True
        assert launcher.sessions["bg-test"].is_background is True
        launcher.close_session("bg-test", persist=False)

    def test_close_with_persist(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher, load_session
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        launcher = TerminalLauncher()
        launcher.create_session("persist-test")
        launcher.close_session("persist-test", persist=True)
        loaded = load_session("persist-test")
        assert loaded is not None
        from main import delete_session
        delete_session("persist-test")

    def test_persist_all(self, temp_sessions_dir, mock_swarm_bus, mock_swarm_assemble, monkeypatch):
        from main import TerminalLauncher
        monkeypatch.setattr("main.SESSIONS_DIR", temp_sessions_dir)
        launcher = TerminalLauncher()
        launcher.create_session("persist-all-a")
        launcher.create_session("persist-all-b")
        launcher.persist_all()
        sessions = launcher.list_saved_sessions()
        assert "persist-all-a" in sessions
        assert "persist-all-b" in sessions
        launcher.close_session("persist-all-a", persist=False)
        launcher.close_session("persist-all-b", persist=False)
        from main import delete_session
        for n in ["persist-all-a", "persist-all-b"]:
            delete_session(n)


class TestPluginHost:
    def test_discover_empty_dir(self, tmp_path):
        from main import PluginHost, CommandRegistry
        host = PluginHost.__new__(PluginHost)
        host.plugins_dir = tmp_path
        host._plugins = {}
        host._command_registry = CommandRegistry()
        discovered = host.discover()
        assert discovered == []

    def test_load_builtin_plugins(self):
        from main import TerminalLauncher
        launcher = TerminalLauncher()
        assert "fleet-awareness" in launcher.get_plugin_host().list_plugins()
        assert "git-branch" in launcher.get_plugin_host().list_plugins()
        assert "persist-queue" in launcher.get_plugin_host().list_plugins()


class TestPluginContext:
    def test_publish_calls_swarm_bus_put(self, mock_swarm_bus):
        from main import PluginContext, TerminalLauncher
        launcher = TerminalLauncher()
        ctx = PluginContext(launcher)
        ctx.publish("test-key", "test-value")
        mock_swarm_bus.assert_called()

    def test_get_fleet_state(self, mock_swarm_bus):
        from main import PluginContext, TerminalLauncher
        mock_swarm_bus.return_value = MagicMock(returncode=0, stdout="agent1:state1\nagent2:state2\n")
        launcher = TerminalLauncher()
        ctx = PluginContext(launcher)
        state = ctx.get_fleet_state()
        assert "agent1" in state


class TestTerminalPlugin:
    def test_fleet_awareness_on_session_create(self, mock_swarm_bus, mock_swarm_assemble):
        from main import FleetAwarenessPlugin, PluginContext, TerminalLauncher, Session
        plugin = FleetAwarenessPlugin()
        launcher = TerminalLauncher()
        ctx = PluginContext(launcher)
        session = Session(
            name="plugin-test", created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={}
        )
        launcher.sessions["plugin-test"] = session
        plugin.on_session_create(ctx, session)
        launcher.close_session("plugin-test", persist=False)

    def test_git_branch_on_session_switch(self, mock_swarm_bus, mock_swarm_assemble):
        from main import GitBranchPlugin, PluginContext, TerminalLauncher, Session
        plugin = GitBranchPlugin()
        launcher = TerminalLauncher()
        ctx = PluginContext(launcher)
        old_session = Session(
            name="old", created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={}
        )
        new_session = Session(
            name="new", created_at="2026-09-06T00:00:00+00:00",
            last_active="2026-09-06T00:00:00+00:00", cwd="/tmp", env={}
        )
        launcher.sessions["old"] = old_session
        launcher.sessions["new"] = new_session
        plugin.on_session_switch(ctx, old_session, new_session)


class TestSelftest:
    def test_selftest_passes(self, mock_swarm_bus, mock_swarm_assemble):
        from main import selftest
        result = selftest()
        assert result is True
