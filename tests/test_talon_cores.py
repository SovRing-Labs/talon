"""talon cores (FLEET-LOOP-RAPTOR S4, read-only): lists the core registry and the last receipts from a contract-v1 fixture."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import talon_run  # noqa: E402

DDL = """
CREATE TABLE cores(core TEXT PRIMARY KEY, version TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL, entry TEXT NOT NULL,
  gate TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'live', added_ts TEXT NOT NULL, last_fired_ts TEXT);
CREATE TABLE ops(core TEXT NOT NULL, op TEXT NOT NULL, args_schema TEXT NOT NULL, returns TEXT NOT NULL,
  deterministic INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(core, op));
"""


def _fixture(tmp_path, monkeypatch):
    db = tmp_path / "cores.db"
    con = sqlite3.connect(db)
    con.executescript(DDL)
    con.execute("INSERT INTO cores VALUES('tw','abc','agent','cli','[]','true','live','2026-10-07',NULL)")
    con.execute("INSERT INTO cores VALUES('old','1','x','cli','[]','true','dormant','2026-10-07',NULL)")
    con.executemany("INSERT INTO ops VALUES(?,?, '[]', '\"\"', 1)", [("tw", "status"), ("tw", "kill")])
    con.commit()
    con.close()
    rc = tmp_path / "core-receipts.jsonl"
    rc.write_text("\n".join(json.dumps({"core": "tw", "op": "status", "verdict": v, "ms": i, "id": i})
                            for i, v in enumerate(["pass", "fail", "error"])) + "\n")
    monkeypatch.setenv("CORES_DB", str(db))
    monkeypatch.setenv("CORE_RECEIPTS", str(rc))


def test_cores_json(tmp_path, monkeypatch, capsys):
    _fixture(tmp_path, monkeypatch)
    assert talon_run.main(["cores", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["core"] for r in rows] == ["old", "tw"]
    assert rows[1]["ops"] == ["kill", "status"]


def test_cores_receipts_text(tmp_path, monkeypatch, capsys):
    _fixture(tmp_path, monkeypatch)
    assert talon_run.main(["cores", "--receipts", "2"]) == 0
    out = capsys.readouterr().out
    assert "1/2 cores live" in out
    assert "-1" in out and " 0 " in out and "+1" not in out  # last 2 only: fail, error


def test_cores_missing_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CORES_DB", str(tmp_path / "none.db"))
    assert talon_run.main(["cores"]) == 1
    assert "registry not built" in capsys.readouterr().err
