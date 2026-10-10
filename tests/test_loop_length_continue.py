"""A final answer cut at the output cap (finish_reason=length) is continued, stored once (J 10-02)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine.loop import ReentrantAgentLoop  # noqa: E402


class FakeProvider:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, tools=None, **kw):
        self.calls.append([dict(m) for m in messages])
        content, finish = self.replies.pop(0)
        return {"role": "assistant", "content": content, "_finish": finish, "_usage": {"completion_tokens": 1}}


def test_length_cut_answer_is_continued_into_one_message():
    p = FakeProvider([("The answer is fo", "length"), ("rty-two.", "stop")])
    res = ReentrantAgentLoop(provider_client=p).run(prompt="q", role="dev", lane="x")
    assert res["ok"] and res["content"] == "The answer is forty-two."
    assert len(p.calls) == 2
    assert p.calls[1][-2] == {"role": "assistant", "content": "The answer is fo"}  # partial sent back
    assert [m["role"] for m in res["messages"]][-1] == "assistant"
    assert sum(1 for m in res["messages"] if m["role"] == "assistant") == 1     # stored once


def test_continuation_capped_at_three():
    p = FakeProvider([("a", "length")] * 5)
    res = ReentrantAgentLoop(provider_client=p).run(prompt="q", role="dev", lane="x")
    assert res["content"] == "aaaa" and len(p.calls) == 4
