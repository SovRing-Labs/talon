import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import dashboard_tui


SAMPLE_METRICS = """
  FLEET METRICS - dispatch.db telemetry
  Tasks:        820 total  |  632 done  |  20 staged  |  1 active  |  0 abandoned
  Completion:   77.1%  |  Gaveup rate: 32.4%
  Pending (active):       3
  opencode/nemotron-3.5-lightning-free    79/260   30.4%
  openrouter-free/minimax/minimax-m2.7:free  72/146   49.3%
"""


class TestParseMetrics:
    def test_extracts_totals(self):
        parsed = dashboard_tui.parse_metrics(SAMPLE_METRICS)
        assert parsed["total"] == 820
        assert parsed["done"] == 632
        assert parsed["staged"] == 20
        assert parsed["active"] == 1

    def test_extracts_completion_and_pending(self):
        parsed = dashboard_tui.parse_metrics(SAMPLE_METRICS)
        assert parsed["completion"] == 77.1
        assert parsed["pending"] == 3

    def test_extracts_model_rows(self):
        parsed = dashboard_tui.parse_metrics(SAMPLE_METRICS)
        assert len(parsed["models"]) == 2
        assert parsed["models"][0][:2] == ("opencode/nemotron-3.5-lightning-free", "79")

    def test_empty_text(self):
        parsed = dashboard_tui.parse_metrics("")
        assert parsed["total"] is None
        assert parsed["models"] == []


class TestReadTasks:
    def _make_db(self, tmp_path):
        db_path = tmp_path / "tasks.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, role TEXT, "
            "updated_at TEXT, created_at TEXT, brief_path TEXT, class TEXT, "
            "attempt INTEGER)"
        )
        conn.executemany(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("T-1", "first", "done", "builder", "2026-01-01", "2026-01-01", "", "", 1),
                ("T-2", "second", "staged", "builder", "2026-01-03", "2026-01-01", "", "", 1),
                ("T-3", "third", "dispatched", "oracle", "2026-01-02", "2026-01-01", "", "", 2),
                ("T-4", "fourth", "blocked", "grunt", "2026-01-04", "2026-01-01", "", "", 1),
            ],
        )
        conn.commit()
        conn.close()
        return db_path

    def test_counts(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = dashboard_tui.read_tasks(db_path=db_path)
        assert result["error"] is None
        assert result["counts"]["done"] == 1
        assert result["counts"]["staged"] == 1
        assert result["counts"]["dispatched"] == 1
        assert result["counts"]["blocked"] == 1

    def test_rows_prioritize_live(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = dashboard_tui.read_tasks(db_path=db_path, limit=10)
        statuses = [row[2] for row in result["rows"]]
        assert "staged" in statuses
        assert "dispatched" in statuses
        assert statuses.index("dispatched") < statuses.index("staged")
        assert len([r for r in result["rows"] if r[2] == "done"]) == 0

    def test_missing_db(self, tmp_path):
        result = dashboard_tui.read_tasks(db_path=tmp_path / "nope.db")
        assert result["error"] is not None
        assert result["counts"] == {}

    def test_limit_respected(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = dashboard_tui.read_tasks(db_path=db_path, limit=1)
        assert len(result["rows"]) == 1


class TestReadSessions:
    def _make_sessions(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "alpha.json").write_text(
            json.dumps({
                "name": "alpha",
                "shell": "/bin/bash",
                "cwd": "/tmp",
                "last_active": "2026-09-21T00:00:00+00:00",
                "tags": ["test"],
            })
        )
        (sessions_dir / "beta.json").write_text(
            json.dumps({
                "name": "beta",
                "shell": "/bin/zsh",
                "cwd": "/srv",
                "last_active": "2026-09-22T00:00:00+00:00",
            })
        )
        (sessions_dir / "broken.json").write_text("{not json")
        return sessions_dir

    def test_lists_sessions_sorted_by_last_active(self, tmp_path):
        sessions = dashboard_tui.read_sessions(sessions_dir=self._make_sessions(tmp_path))
        assert [s["name"] for s in sessions] == ["beta", "alpha"]
        assert sessions[0]["shell"] == "/bin/zsh"
        assert sessions[1]["cwd"] == "/tmp"

    def test_missing_dir_returns_empty(self, tmp_path):
        assert dashboard_tui.read_sessions(sessions_dir=tmp_path / "nope") == []

    def test_broken_file_skipped(self, tmp_path):
        sessions = dashboard_tui.read_sessions(sessions_dir=self._make_sessions(tmp_path))
        assert "broken" not in [s["name"] for s in sessions]


class TestTailLines:
    def test_last_n_lines(self, tmp_path):
        path = tmp_path / "events.jsonl"
        path.write_text("\n".join(f"line{i}" for i in range(60)))
        lines = dashboard_tui.tail_lines(path, count=5)
        assert lines == ["line55", "line56", "line57", "line58", "line59"]

    def test_missing_file(self, tmp_path):
        lines = dashboard_tui.tail_lines(tmp_path / "none.jsonl", count=5)
        assert lines and "no events file" in lines[0]

    def test_small_file(self, tmp_path):
        path = tmp_path / "small.jsonl"
        path.write_text("a\nb\n")
        assert dashboard_tui.tail_lines(path, count=10) == ["a", "b"]


class TestGetAndon:
    def test_default_clear(self, tmp_path):
        assert dashboard_tui.get_andon(andon_file=tmp_path / "missing.json") == "clear"

    def test_tripped(self, tmp_path):
        path = tmp_path / "talon-andon.json"
        path.write_text(json.dumps({"state": "tripped"}))
        assert dashboard_tui.get_andon(andon_file=path) == "tripped"

    def test_bad_json_defaults_clear(self, tmp_path):
        path = tmp_path / "talon-andon.json"
        path.write_text("junk")
        assert dashboard_tui.get_andon(andon_file=path) == "clear"


class TestTextReport:
    def test_render(self):
        data = {
            "ts": 0,
            "andon": "clear",
            "metrics": {"completion": 50.0},
            "tasks": {"counts": {"done": 2, "staged": 1}, "rows": [], "error": None},
            "sessions": [{"name": "alpha", "shell": "/bin/bash", "cwd": "/tmp", "worktree": None}],
            "events": ["event-a"],
        }
        report = dashboard_tui.text_report(data)
        assert "TALON DASHBOARD" in report
        assert "alpha" in report
        assert "event-a" in report

    def test_fmt_time(self):
        assert dashboard_tui.fmt_time("") == "?"
        assert dashboard_tui.fmt_time("2026-01-01T00:00:00+00:00").startswith("2026-01-01")

    def test_fmt_task_time_handles_epoch_and_text(self):
        import datetime
        assert dashboard_tui.fmt_task_time(None) == "?"
        epoch_formatted = dashboard_tui.fmt_task_time(1789920859)
        expected = datetime.datetime.fromtimestamp(1789920859).strftime("%m-%d %H:%M")
        assert epoch_formatted == expected
        assert dashboard_tui.fmt_task_time("2026-01-01T00:00:00+00:00").startswith("2026-01-01")

    def test_report_shows_provider_pin(self):
        data = {
            "ts": 0,
            "andon": "clear",
            "metrics": {},
            "tasks": {"counts": {}, "rows": [], "error": None},
            "sessions": [{"name": "alpha", "shell": "/bin/bash", "cwd": "/tmp",
                          "worktree": None, "provider": "openrouter",
                          "model": "openrouter/cohere/north-mini-code:free"}],
            "events": ["event-a"],
        }
        report = dashboard_tui.text_report(data)
        assert "openrouter/openrouter/cohere/north-mini-code:free" in report


class TestReadCatalog:
    def _make_catalog(self, tmp_path):
        db_path = tmp_path / "lane-catalog.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE providers (id TEXT, name TEXT, type TEXT, cap INTEGER, "
            "rate_min INTEGER, is_enabled INTEGER)"
        )
        conn.execute(
            "CREATE TABLE models (id TEXT, provider_id TEXT, name TEXT, best TEXT, "
            "caps TEXT, cost INTEGER, quality INTEGER, speed INTEGER, ctx_len INTEGER, "
            "is_tombstone INTEGER, is_enabled INTEGER)"
        )
        conn.executemany("INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?)", [
            ("openrouter", "OpenRouter", "api", 4, 60, 1),
            ("groq", "Groq", "api", 2, 60, 0),
            ("opencode", "Opencode", "zen", 2, 0, 1),
        ])
        conn.executemany("INSERT INTO models VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("openrouter/cohere/north-mini-code:free", "openrouter", "north", "", '["build"]', 0, 80, 90, 32000, 0, 1),
            ("opencode/mimo-v2.5-free", "opencode", "mimo", "", "[]", 0, 70, 80, 64000, 0, 1),
            ("groq/groq-legacy", "groq", "legacy", "", "[]", 0, 50, 60, 8000, 1, 1),
            ("openrouter/old/tomb", "openrouter", "tomb", "", "[]", 0, 40, 50, 8000, 0, 0),
        ])
        conn.commit()
        conn.close()
        return db_path

    def test_providers_enabled_first(self, tmp_path):
        catalog = dashboard_tui.read_catalog(db_path=self._make_catalog(tmp_path))
        assert catalog["error"] is None
        ids = [p["id"] for p in catalog["providers"]]
        assert ids == ["opencode", "openrouter", "groq"]
        assert catalog["providers"][0]["enabled"] is True
        assert catalog["providers"][2]["enabled"] is False

    def test_models_exclude_tombstone_and_disabled(self, tmp_path):
        catalog = dashboard_tui.read_catalog(db_path=self._make_catalog(tmp_path))
        model_ids = [m["id"] for m in catalog["models"]]
        assert "groq/groq-legacy" not in model_ids
        assert "openrouter/old/tomb" not in model_ids
        assert "openrouter/cohere/north-mini-code:free" in model_ids

    def test_missing_db_reports_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard_tui, "LANE_CATALOG_DB", tmp_path / "absent.db")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        catalog = dashboard_tui.read_catalog(db_path=tmp_path / "nope.db")
        assert catalog["error"] is not None
        assert catalog["providers"] == []

    def test_corrupt_db_reports_error(self, tmp_path):
        bad_db = tmp_path / "bad.db"
        bad_db.write_bytes(b"")
        catalog = dashboard_tui.read_catalog(db_path=bad_db)
        assert catalog["error"] is not None

    def test_parse_caps_json(self, tmp_path):
        catalog = dashboard_tui.read_catalog(db_path=self._make_catalog(tmp_path))
        caps = [m["caps"] for m in catalog["models"]
                if m["id"].startswith("openrouter/cohere")][0]
        assert caps == ["build"]


class TestModelSelection:
    def test_models_for_provider_filters(self):
        catalog = {"models": [
            {"id": "openrouter/a", "provider": "openrouter"},
            {"id": "opencode/b", "provider": "opencode"},
        ]}
        ids = [m["id"] for m in dashboard_tui.models_for_provider(catalog, "openrouter")]
        assert ids == ["openrouter/a"]

    def test_models_for_provider_auto_returns_all(self):
        catalog = {"models": [
            {"id": "openrouter/a", "provider": "openrouter"},
            {"id": "opencode/b", "provider": "opencode"},
        ]}
        assert len(dashboard_tui.models_for_provider(catalog, "auto")) == 2

    def test_model_ref_prefixed(self):
        model = {"id": "openrouter/cohere/north-mini-code:free"}
        assert dashboard_tui.model_ref(model, "openrouter") == model["id"]

    def test_model_ref_unprefixed(self):
        model = {"id": "compound-mini"}
        assert dashboard_tui.model_ref(model, "groq") == "groq/compound-mini"

    def test_default_provider_prefers_enabled(self):
        catalog = {"providers": [{"id": "groq", "enabled": False},
                                 {"id": "openrouter", "enabled": True}]}
        assert dashboard_tui.default_provider(catalog) == "openrouter"
        assert dashboard_tui.default_provider({"providers": []}) == "auto"

    def test_default_provider_prefers_provider_with_models(self):
        catalog = {
            "providers": [{"id": "local-gpu", "enabled": True},
                          {"id": "openrouter", "enabled": True}],
            "models": [{"id": "openrouter/x", "provider": "openrouter"}],
        }
        assert dashboard_tui.default_provider(catalog) == "openrouter"


class TestSessionRecord:
    def test_sanitize_name(self):
        assert dashboard_tui.sanitize_name("foo/bar baz!") == "foo_bar_baz"
        assert dashboard_tui.sanitize_name("") != ""
        assert dashboard_tui.sanitize_name("..") != ""

    def test_save_manual_session_persists_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dashboard_tui, "SESSIONS_DIR", tmp_path / "sessions")
        record = dashboard_tui.save_manual_session(
            "wizard-test", role="commander", provider="openrouter",
            model="openrouter/cohere/north-mini-code:free")
        saved_path = tmp_path / "sessions" / "wizard-test.json"
        assert saved_path.exists()
        data = json.loads(saved_path.read_text())
        assert data["role"] == "commander"
        assert data["provider"] == "openrouter"
        assert data["model"] == "openrouter/cohere/north-mini-code:free"
        assert "provider:openrouter" in data["tags"]
        assert "model:openrouter/cohere/north-mini-code:free" in data["tags"]

    def test_build_session_rc_pins_provider_and_model(self):
        record = {"name": "x", "role": "commander", "provider": "openrouter",
                  "model": "openrouter/cohere/north-mini-code:free"}
        rc = dashboard_tui.build_session_rc(record)
        assert "TALON_SESSION_PROVIDER=openrouter" in rc
        assert "TALON_SESSION_MODEL=openrouter/cohere/north-mini-code:free" in rc
        assert 'opencode -m "openrouter/cohere/north-mini-code:free"' in rc

    def test_build_session_rc_without_model_skips_alias(self):
        record = {"name": "legacy", "role": "", "provider": "", "model": ""}
        rc = dashboard_tui.build_session_rc(record)
        assert "opc()" not in rc


class TestWizardNav:
    def test_move_cursor_bounds(self):
        import pytest
        if dashboard_tui.curses is None:
            pytest.skip("curses unavailable")
        move = dashboard_tui._move_cursor
        assert move(dashboard_tui.curses.KEY_DOWN, 0, 5) == 1
        assert move(dashboard_tui.curses.KEY_DOWN, 4, 5) == 4
        assert move(dashboard_tui.curses.KEY_DOWN, 5, 5) == 4
        assert move(dashboard_tui.curses.KEY_UP, 0, 5) == 0
        assert move(ord("k"), 3, 5) == 2
        assert move(ord("j"), 3, 0) == 0
        assert move(ord("g"), 3, 7) == 0
        assert move(ord("G"), 3, 7) == 6
        assert move(dashboard_tui.curses.KEY_PPAGE, 3, 25) == 0
        assert move(dashboard_tui.curses.KEY_NPAGE, 3, 25) == 13