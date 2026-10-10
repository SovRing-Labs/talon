"""Tests for the Talon Native Agent Engine (rewritten the engine rewrite 2026-09-26).

Hermetic: TALON_MOCK=1 for the model, a temp TALON_DB for sessions. Every test that runs the
CLI or the loop points TALON_DB at tmp_path so the live session store is never touched.
Live-lane behaviour is covered by control-center/gates/talon-parallel.py, not here.
"""

import json
import os
import sqlite3
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

src_path = Path(__file__).resolve().parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from engine import config  # noqa: E402
from engine.loop import ReentrantAgentLoop  # noqa: E402
from engine.providers import (LaneError, OpenAICompatibleClient, _parse_text_tool_calls,  # noqa: E402
                              resolve_lane, upstream_model)
from engine.sessions import SessionStore  # noqa: E402
from engine.tools import OmnitoolBridge, tools_for  # noqa: E402
import talon_run  # noqa: E402

LIVE_LANE = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TALON_MOCK", "1")
    monkeypatch.setenv("TALON_DB", str(tmp_path / "talon.db"))
    return tmp_path


def mock_client():
    return OpenAICompatibleClient(base_url="http://mock.invalid/v1", model="mock-model")


# ── lane resolution (bugs #62, #64) ────────────────────────────────────────────────────
def test_lane_prefix_is_stripped_and_endpoint_is_real():
    base, model, key = resolve_lane(LIVE_LANE)
    assert base == "https://openrouter.ai/api/v1"
    assert model == "nvidia/nemotron-3-super-120b-a12b:free"
    assert key and key != "local"


def test_ollama_and_direct_url_lanes():
    assert resolve_lane("ollama/qwen2.5-coder:1.5b")[:2] == ("http://127.0.0.1:11434/v1", "qwen2.5-coder:1.5b")
    assert resolve_lane("http://127.0.0.1:8000/v1::org/model-x")[:2] == ("http://127.0.0.1:8000/v1", "org/model-x")


def test_unknown_lane_is_an_error_not_a_silent_openrouter_fallback():
    with pytest.raises(LaneError):
        resolve_lane("nonsense/model")
    with pytest.raises(LaneError):
        resolve_lane("")


def test_opencode_only_free_tier_is_refused():
    with pytest.raises(LaneError, match="OpenCode-only"):
        resolve_lane("opencode/nemotron-3.5-lightning-free")


def test_upstream_model():
    assert upstream_model("openrouter-free/poolside/laguna-s-2.1:free") == "poolside/laguna-s-2.1:free"
    assert upstream_model("nvidia/x") == "nvidia/x"


def test_hermes_text_tool_calls_are_parsed():
    calls = _parse_text_tool_calls('ok <tool_call>{"name": "bash", "arguments": {"command": "ls"}}</tool_call>')
    assert calls[0]["function"]["name"] == "bash"
    assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}


# ── roles + tools ──────────────────────────────────────────────────────────────────────
def test_roles_come_from_opencode_config():
    dev = config.role_config("dev")
    assert "DEV agent" in dev["prompt"] and dev["steps"] >= 30
    assert "fleet_exec" in tools_for(dev["tools"])
    assert "fleet_exec" not in tools_for(config.role_config("researcher")["tools"])


def test_disallowed_tool_is_refused():
    b = OmnitoolBridge(allowed=["read"])
    assert b.execute_tool("bash", {"command": "true"})["ok"] is False


def test_tools_bash_write_edit_read(tmp_path):
    b = OmnitoolBridge(workspace_root=tmp_path)
    assert "talon_tool_ok" in b.execute_tool("bash", {"command": "echo talon_tool_ok"})["stdout"]
    f = tmp_path / "t.py"
    assert b.execute_tool("write", {"path": str(f), "content": "x = 1\ny = 2\n"})["ok"]
    assert b.execute_tool("edit", {"path": str(f), "old_string": "x = 1", "new_string": "x = 42"})["ok"]
    assert f.read_text() == "x = 42\ny = 2\n"
    assert "x = 42" in b.execute_tool("read", {"path": str(f)})["content"]
    assert b.execute_tool("edit", {"path": str(f), "old_string": "nope", "new_string": "z"})["ok"] is False


def test_write_scope_is_enforced_through_omnitool():
    r = OmnitoolBridge().execute_tool("write", {"path": "/etc/talon-must-not-write", "content": "x"})
    assert r["ok"] is False and "scope" in r["error"]


# ── loop (bug #63) ─────────────────────────────────────────────────────────────────────
def test_loop_keeps_the_provider_it_was_given(mock_env):
    client = mock_client()
    loop = ReentrantAgentLoop(provider_client=client)
    res = loop.run(prompt="run bash echo mock_done", role="dev", lane="nonsense/lane", max_turns=5)
    assert loop.provider is client and res["ok"] and "mock_done" in res["content"]


def test_turn_budget_exhaustion_is_a_failure(mock_env):
    res = ReentrantAgentLoop(provider_client=mock_client()).run(prompt="run bash echo x", role="dev", max_turns=1)
    assert res["ok"] is False and "max turns" in res["error"]


def test_session_persistence_and_resume(mock_env):
    st = SessionStore()
    sid = st.create(title="t", role="dev", lane="mock", cwd=str(mock_env))
    ReentrantAgentLoop(provider_client=mock_client(), store=st).run(prompt="run bash echo one", session_id=sid)
    n1 = len(st.messages(sid))
    res = ReentrantAgentLoop(provider_client=mock_client(), store=st).run(prompt="hello again", session_id=sid)
    msgs = res["messages"]
    assert n1 >= 4 and len(st.messages(sid)) == n1 + 2
    assert [m["role"] for m in msgs].count("system") == 1 and "one" in json.dumps(msgs)
    assert st.get(sid)["tokens_in"] > 0


# ── CLI (bug #61) ──────────────────────────────────────────────────────────────────────
def test_cli_help():
    with pytest.raises(SystemExit) as e:
        talon_run.run_cli(["--help"])
    assert e.value.code == 0


def test_cli_mock_run_records_a_session(mock_env, capsys):
    assert talon_run.run_cli(["--agent", "dev", "-m", LIVE_LANE, "--title", "T-CLI", "echo hi"]) == 0
    assert "hi" in capsys.readouterr().out
    st = SessionStore()
    assert st.find_by_title("T-CLI")["status"] == "done"


def test_dead_model_fails_and_never_runs_the_prompt_as_bash(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TALON_MOCK", raising=False)
    monkeypatch.setenv("TALON_DB", str(tmp_path / "talon.db"))
    marker = tmp_path / "must-not-exist"
    rc = talon_run.run_cli(["-m", "http://127.0.0.1:9/v1::dead", "-q", f"echo x > {marker}"])
    assert rc == 1 and not marker.exists()


def test_bad_lane_exit_code_2(mock_env):
    assert talon_run.run_cli(["-m", "nonsense/x", "hello"]) == 2


def test_opencode_compat_flags_are_accepted(mock_env):
    assert talon_run.run_cli(["--auto", "--attach", "http://127.0.0.1:4096", "-m", LIVE_LANE, "-q", "hello"]) == 0


# ── server ─────────────────────────────────────────────────────────────────────────────
def test_server_api_and_cross_origin_refusal(mock_env):
    from engine.server import make_server
    srv = make_server(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        assert json.load(urllib.request.urlopen(f"{base}/api/health"))["ok"] is True
        assert b"Talon Console" in urllib.request.urlopen(base + "/").read()
        req = urllib.request.Request(f"{base}/api/sessions", method="POST", data=b'{"prompt":"x","lane":"x"}',
                                     headers={"Origin": "http://evil.example", "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
        req = urllib.request.Request(f"{base}/api/sessions", method="POST",
                                     data=json.dumps({"prompt": "run bash echo via_api", "lane": LIVE_LANE}).encode(),
                                     headers={"Content-Type": "application/json"})
        sid = json.load(urllib.request.urlopen(req))["id"]
        import time
        for _ in range(50):
            s = json.load(urllib.request.urlopen(f"{base}/api/sessions/{sid}"))
            if s["session"]["status"] != "running":
                break
            time.sleep(0.1)
        assert s["session"]["status"] == "done" and "via_api" in json.dumps(s["messages"])
    finally:
        srv.shutdown()
        srv.server_close()


# ── stigmergy (2026-09-27) ─────────────────────────────────────────────
def test_inbox_message_lands_at_the_next_turn(mock_env):
    from engine import field
    st = SessionStore()
    sid = st.create(title="t", role="dev", lane="mock", cwd=str(mock_env))
    c = field.marks().connect()
    field.marks().send(c, sid, "switch to plan B")
    c.close()
    res = ReentrantAgentLoop(provider_client=mock_client(), store=st).run(
        prompt="run bash echo one", session_id=sid, inbox=field.Inbox(sid, None))
    injected = [m for m in res["messages"] if m["role"] == "user" and "OPS-INJECT" in m["content"]]
    assert injected and "switch to plan B" in injected[0]["content"]
    assert any("OPS-INJECT" in (m["content"] or "") for m in st.messages(sid))


def test_run_posts_arrive_and_debrief_and_marks_the_session(mock_env, tmp_path):
    import json as _j, sqlite3 as _s
    assert talon_run.run_cli(["-m", LIVE_LANE, "--title", "T-FIELD", "-q", "echo hi"]) == 0
    ev = [_j.loads(l) for l in open(os.environ["FLEET_EVENTS_PATH"])]
    assert [e.get("verb") for e in ev][:1] == ["ARRIVE"] and ev[-1].get("verb") == "DEBRIEF"
    rows = _s.connect(os.environ["FIELD_DB"]).execute("SELECT kind, session FROM marks ORDER BY id").fetchall()
    kinds = [r[0] for r in rows]
    assert kinds[0] == "arrive" and "command" in kinds and kinds[-1] == "debrief"
    assert len({r[1] for r in rows}) == 1 and rows[0][1].startswith("tln_")
