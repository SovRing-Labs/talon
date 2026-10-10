"""Session store hygiene (2026-10-06): no credentials in saved sessions; close(persist=False) writes nothing, plugins included;
`talon --selftest` leaves the real store alone."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
REAL = Path("data/terminal-sessions")


def test_save_drops_credentials(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "SESSIONS_DIR", tmp_path)
    s = main.Session(name="x", created_at="t", last_active="t", cwd="/tmp",
                     env={"GEMINI_API_KEY": "k", "GITHUB_TOKEN": "t", "DB_PASSWORD": "p", "MY_SECRET": "s", "HOME": "/h"})
    env = json.loads(main.save_session("x", s).read_text())["env"]
    assert env == {"HOME": "/h"}


def test_integration_filter_drops_credentials():
    import integration
    assert integration.filter_env_for_save({"GEMINI_API_KEY": "k", "HOME": "/h"}) == {"HOME": "/h"}


def test_close_without_persist_writes_nothing_even_with_plugins(tmp_path, monkeypatch):
    import main, plugins
    monkeypatch.setattr(main, "SESSIONS_DIR", tmp_path)
    s = main.Session(name="np", created_at="t", last_active="t", cwd="/tmp", env={})
    s.persist_on_close = False
    plugins.PersistQueuePlugin().on_session_close(None, s)
    assert list(tmp_path.iterdir()) == []


def test_selftest_leaves_real_store_alone():
    before = {p.name: p.stat().st_mtime for p in REAL.glob("*.json")} if REAL.exists() else {}
    r = subprocess.run(["bin/talon", "--selftest"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-500:]
    after = {p.name: p.stat().st_mtime for p in REAL.glob("*.json")} if REAL.exists() else {}
    assert after == before
