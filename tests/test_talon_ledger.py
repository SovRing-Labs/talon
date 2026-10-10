"""Talon ledger (FLEET-LOOP-RAPTOR S4): core receipts caused by a Talon session land in talon.db's ledger + a transcript notice."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engine import ledger  # noqa: E402
from engine.sessions import SessionStore  # noqa: E402
from engine.tools import OmnitoolBridge  # noqa: E402
import talon_run  # noqa: E402

REG = Path(os.environ.get("CORE_REGISTRY_BIN", "bin/core-registry"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TALON_DB", str(tmp_path / "talon.db"))
    monkeypatch.setenv("CORE_RECEIPTS", str(tmp_path / "core-receipts.jsonl"))
    store = SessionStore()
    sid = store.create(title="t", role="dev", lane="x", cwd=str(tmp_path))
    return store, sid, tmp_path / "core-receipts.jsonl"


def _rec(caller, verdict="pass", core="tw", op="gate", ms=5):
    return json.dumps({"v": 1, "ts": "2026-10-07T22:00:00Z", "core": core, "op": op, "caller": caller, "verdict": verdict,
                       "ms": ms, "input_hash": "sha256:x", "output_ref": None}) + "\n"


def test_bridge_stamps_core_caller():
    b = OmnitoolBridge(env_extra={"TALON_SESSION": "tln_abc", "TALON_ROLE": "dev"})
    assert b.env_extra["CORE_CALLER"] == "talon:tln_abc"
    assert "CORE_CALLER" not in OmnitoolBridge(env_extra={}).env_extra


def test_sync_rows_notice_idempotent(env):
    store, sid, rc = env
    rc.write_text(_rec(f"talon:{sid}") + _rec("raptor-ui") + _rec("talon:tln_unknown") + _rec(f"talon:{sid}", "fail", ms=9))
    assert ledger.sync(store) == 2
    rs = ledger.rows(store, sid)
    assert [r["verdict"] for r in rs] == ["fail", "pass"] and len({r["ref"] for r in rs}) == 2
    notes = [m for m in store.messages(sid) if m["role"] == "notice"]
    assert len(notes) == 2 and "receipt " + rs[1]["ref"] in notes[0]["content"]
    assert ledger.sync(store) == 0                                 # cursor: nothing new
    with rc.open("a") as f:
        f.write(_rec(f"talon:{sid}", ms=11)[:-5])                  # half-written line waits
    assert ledger.sync(store) == 0
    with rc.open("a") as f:
        f.write(_rec(f"talon:{sid}", ms=11)[-5:])
    assert ledger.sync(store) == 1


def test_truncated_file_restarts_without_duplicates(env):
    store, sid, rc = env
    rc.write_text(_rec(f"talon:{sid}"))
    assert ledger.sync(store) == 1
    rc.write_text("")                                              # replaced
    assert ledger.sync(store) == 0
    rc.write_text(_rec(f"talon:{sid}"))                            # same receipt line again -> same ref -> ignored
    assert ledger.sync(store) == 0 and len(ledger.rows(store, sid)) == 1


def test_cli(env, capsys):
    store, sid, rc = env
    rc.write_text(_rec(f"talon:{sid}", core="doctor.wall", op="wall"))
    assert talon_run.main(["ledger", sid]) == 0
    out = capsys.readouterr().out
    assert "doctor.wall.wall" in out and "1 ledger row(s) · 1 new" in out


@pytest.mark.skipif(not REG.exists(), reason="FLEET-LOOP-REG core-registry not present")
def test_end_to_end_real_registry(env):
    store, sid, rc = env
    e = dict(os.environ, CORE_CALLER=ledger.caller_for(sid), CORE_RECEIPTS=str(rc))
    r = subprocess.run([str(REG), "gate", "tw"], env=e, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert ledger.sync(store) == 1
    row = ledger.rows(store, sid)[0]
    assert (row["core"], row["op"], row["verdict"]) == ("tw", "gate", "pass")
