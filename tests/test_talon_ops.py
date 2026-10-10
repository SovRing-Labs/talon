"""TALON-VERTEX-OPS-0 (J 2026-10-04): paid spend, context, compaction, server notices."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine import providers  # noqa: E402
from engine.loop import ReentrantAgentLoop, compact  # noqa: E402
from engine.providers import OpenAICompatibleClient, normalize_usage  # noqa: E402
from engine.sessions import SessionStore  # noqa: E402


@pytest.fixture
def prices(tmp_path, monkeypatch):
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"google-vertex": {"models": {
        "gemini-3.8-flash": {"cost": {"input": 0.75, "output": 3.75, "cache_read": 0.075}},
        "gemini-3.1-pro-preview": {"cost": {"input": 2, "output": 12, "cache_read": 0.2,
                                            "tiers": [{"input": 4, "output": 18, "cache_read": 0.4,
                                                       "tier": {"type": "context", "size": 200000}}]}}}}}))
    monkeypatch.setattr(providers, "MODELS_DEV", f)
    providers._PRICES.clear()
    yield
    providers._PRICES.clear()


def test_cost_counts_vertex_thinking_as_output(prices):
    # shape returned live by Vertex 2026-10-04: reasoning sits outside completion_tokens
    u = normalize_usage("vertexii/gemini-3.8-flash", {"prompt_tokens": 1000, "completion_tokens": 10,
                        "completion_tokens_details": {"reasoning_tokens": 30}, "total_tokens": 1040})
    assert u["completion_tokens"] == 40 and u["reasoning_tokens"] == 30
    assert u["cost"] == pytest.approx((1000 * 0.75 + 40 * 3.75) / 1e6)


def test_cost_uses_long_context_tier_and_cache(prices):
    u = normalize_usage("vertexii/gemini-3.1-pro-preview", {"prompt_tokens": 300_000, "completion_tokens": 100,
                        "prompt_tokens_details": {"cached_tokens": 100_000}, "total_tokens": 300_100})
    assert u["cost"] == pytest.approx((200_000 * 4 + 100_000 * 0.4 + 100 * 18) / 1e6)


def test_provider_cost_is_kept_and_unpriced_lane_is_zero(prices):
    assert normalize_usage("openrouter/x", {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.5})["cost"] == 0.5
    assert not normalize_usage("groq/llama", {"prompt_tokens": 5, "completion_tokens": 5}).get("cost")


def test_rate_limit_retry_and_final_error_are_noticed(monkeypatch):
    body = b'[{"error":{"code":429,"message":"Resource exhausted.","status":"RESOURCE_EXHAUSTED"}}]'

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(body))
    monkeypatch.setattr(providers.urllib.request, "urlopen", boom)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    c = OpenAICompatibleClient(base_url="http://x/v1", model="m", lane="groq/m")
    seen = []
    c.on_notice = lambda lvl, txt: seen.append((lvl, txt))
    with pytest.raises(RuntimeError):
        c.complete([{"role": "user", "content": "hi"}], max_tokens=10, retries=3)
    assert [l for l, _ in seen] == ["warn", "warn", "error"]
    assert "rate limited" in seen[0][1] and "RESOURCE_EXHAUSTED Resource exhausted." in seen[0][1]
    assert "retry 1/2" in seen[0][1]


class Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, tools=None, **kw):
        self.calls.append([dict(m) for m in messages])
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            if getattr(self, "on_notice", None):
                self.on_notice("error", f"rate limited (429): {r}")
            raise r
        return {"role": "assistant", "content": r, "_finish": "stop",
                "_usage": {"prompt_tokens": 1234, "completion_tokens": 7, "cost": 0.01}}


def _store(tmp_path):
    return SessionStore(tmp_path / "t.db")


def test_context_and_spend_are_recorded(tmp_path):
    st = _store(tmp_path)
    sid = st.create(title="t", role="dev", lane="vertexii/gemini-3.8-flash", cwd="/tmp")
    ReentrantAgentLoop(provider_client=Fake(["done"]), store=st).run(prompt="q", lane="x", session_id=sid)
    s = st.get(sid)
    assert s["ctx_tokens"] == 1234 and s["cost"] == pytest.approx(0.01)
    assert st.spend(0)["cost"] == pytest.approx(0.01) and st.spend(0)["lanes"][0]["lane"] == "vertexii/gemini-3.8-flash"


def test_provider_error_is_a_stored_notice_not_a_model_message(tmp_path):
    st = _store(tmp_path)
    sid = st.create(title="t", role="dev", lane="x", cwd="/tmp")
    res = ReentrantAgentLoop(provider_client=Fake([RuntimeError("quota")]), store=st).run(
        prompt="q", lane="x", session_id=sid)
    assert not res["ok"]
    notices = [m for m in st.messages(sid) if m["role"] == "notice"]
    assert len(notices) == 1 and notices[0]["name"] == "error"       # provider's notice, not duplicated
    assert all(m["role"] != "notice" for m in st.history(sid))


def test_compact_summary_replaces_history_sent(tmp_path):
    st = _store(tmp_path)
    sid = st.create(title="t", role="dev", lane="x", cwd="/tmp")
    ReentrantAgentLoop(provider_client=Fake(["first answer"]), store=st).run(prompt="task one", lane="x", session_id=sid)
    p = Fake(["SUMMARY: task one answered"])
    seen = []
    new = compact(p, st.history(sid), store=st, session_id=sid, focus="paths", note=lambda l, t: seen.append(t))
    assert "task one" in p.calls[0][-1]["content"] and "Focus especially on: paths" in p.calls[0][-1]["content"]
    assert [m["role"] for m in new] == ["system", "user"] and "SUMMARY" in new[1]["content"]
    hist = st.history(sid)
    assert [m["role"] for m in hist] == ["system", "user"] and "SUMMARY" in hist[1]["content"]
    assert any(m["content"] == "first answer" for m in st.messages(sid))   # viewers keep the full record
    assert seen and "compacted" in seen[0]
    # the next turn continues from the summary
    p2 = Fake(["second"])
    ReentrantAgentLoop(provider_client=p2, store=st).run(prompt="task two", lane="x", session_id=sid)
    assert [m["role"] for m in p2.calls[0]] == ["system", "user", "user"]


def test_autocompact_fires_before_a_turn_at_threshold(tmp_path):
    st = _store(tmp_path)
    sid = st.create(title="t", role="dev", lane="x", cwd="/tmp")
    ReentrantAgentLoop(provider_client=Fake(["a1"]), store=st).run(prompt="q1", lane="x", session_id=sid)
    st.set(sid, autocompact=1000)                       # ctx_tokens is 1234 from the last turn
    p = Fake(["SUMMARY", "a2"])
    ReentrantAgentLoop(provider_client=p, store=st).run(prompt="q2", lane="x", session_id=sid)
    assert len(p.calls) == 2 and "<conversation>" in p.calls[0][-1]["content"]
    assert "q2" not in p.calls[0][-1]["content"]                     # the new prompt is not summarised away
    assert [(m["role"], m["content"]) for m in p.calls[1]][-1] == ("user", "q2")
    assert [m["role"] for m in p.calls[1]] == ["system", "user", "user"]
    assert [m["role"] for m in st.history(sid)][:3] == ["system", "user", "user"]   # resume keeps q2 too


def test_cancel_abandons_an_in_flight_call(monkeypatch):
    import threading
    import time as _t
    from engine.providers import Cancelled
    hang = threading.Event()
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda req, timeout=0: hang.wait(30))
    c = OpenAICompatibleClient(base_url="http://x/v1", model="m", lane="groq/m")
    threading.Timer(0.3, c.cancel.set).start()
    t0 = _t.time()
    with pytest.raises(Cancelled):
        c.complete([{"role": "user", "content": "hi"}], max_tokens=10)
    assert _t.time() - t0 < 2
    hang.set()


class FakeTools:
    allowed = {"bash"}

    def get_tool_definitions(self, role=None):
        return [{"type": "function", "function": {"name": "bash", "parameters": {}}}]

    def execute_tool(self, name, args):
        return {"ok": True, "exit_code": 0, "stdout": f"new: []\n" * (3 + len(args.get("command", "")) % 5)}


class Looper:
    """Rewrites its debug command every turn; the output never changes shape (tln_2c109, 2026-10-04)."""
    def __init__(self):
        self.n = 0

    def complete(self, messages, tools=None, **kw):
        self.n += 1
        return {"role": "assistant", "content": "", "_finish": "tool_calls", "_usage": {},
                "tool_calls": [{"id": f"c{self.n}", "type": "function",
                                "function": {"name": "bash", "arguments": json.dumps({"command": f"python3 dbg{self.n}.py"})}}]}


def test_loop_guard_pauses_a_run_that_repeats_itself(tmp_path):
    st = _store(tmp_path)
    sid = st.create(title="t", role="dev", lane="x", cwd="/tmp")
    p = Looper()
    res = ReentrantAgentLoop(provider_client=p, tool_bridge=FakeTools(), store=st).run(
        prompt="debug", lane="x", session_id=sid, max_turns=40)
    assert res.get("stopped") and "loop guard" in res["error"] and p.n == 3
    assert any(m["role"] == "notice" and "loop guard" in m["content"] for m in st.messages(sid))
