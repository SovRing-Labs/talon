> **Status: under development — pre-alpha.** Interfaces, numbers and file layout can change without notice.

# talon — status

Last checked: 2026-10-16, Linux laptop (Intel i5-8365U, 8 threads), on a clean copy of this repo. 8 of 10 suite tests work today; 9 of 10 can run without special hardware. Each line below names its test id in `suite.json` and the evidence file it rests on.
Quick test `make test`: PASS (`All Talon release tests PASSED.`, fresh copy, empty HOME).


## Works today

- **TAL1** Full test suite: 207 passed, 13 skipped, 0 failed on a fresh clone — PASS 2026-10-16 (`207 passed, 13 skipped, 57 warnings in 48.86s`); evidence: `DOSSIER.md`, `Makefile`, `CHANGELOG.md`.
- **TAL2** Engine core: lane resolution, tool refusal, turn budget, session resume, dead model never becomes a shell command — PASS 2026-10-16 (`10 passed, 11 skipped in 9.59s`); evidence: `tests/test_talon_run.py`, `DOSSIER.md`.
- **TAL3** A dead model fails the run and the prompt is never executed as a shell command — PASS 2026-10-16 (`1 passed, 20 deselected in 9.13s`); evidence: `tests/test_talon_run.py`, `DOSSIER.md`.
- **TAL4** Terminal role shells: launcher, dashboard, render gate, PTY sessions (159 passed, 1 skipped) — PASS 2026-10-16 (`159 passed, 1 skipped, 57 warnings in 13.79s`); evidence: `tests/test_launcher.py`, `tests/test_render_gate.py`, `README.md`.
- **TAL5** Session-store hygiene and output-limit continuation (6 passed) — PASS 2026-10-16 (`6 passed in 6.03s`); evidence: `CHANGELOG.md`, `tests/test_session_store_hygiene.py`, `tests/test_loop_length_continue.py`.
- **TAL6** Vertex spend, context, auto-compaction and loop guard (10 passed) — PASS 2026-10-16 (`10 passed in 1.29s`); evidence: `tests/test_talon_ops.py`, `CHANGELOG.md`.
- **TAL7** CLI contract, standalone provider resolution, core registry view and ledger (22 passed, 1 skipped) — PASS 2026-10-16 (`22 passed, 1 skipped in 21.32s`); evidence: `tests/test_talon_cli.py`, `tests/test_talon_ledger.py`, `DOSSIER.md`.
- **TAL10** Release scan: no leaked secrets, no development-machine paths, no symlinks — PASS 2026-10-16 (`scan recorded; no /home/<user>/ path in the tree`); evidence: `release-scan.txt`, `PROVENANCE.md`.

## Not yet tested

- **TAL8** Omnitool-backed tools, write scope and the loopback-only server with cross-origin refusal — external: needs something the test laptop does not have; needs: python3, pytest, pyte, omnitool (git clone https://github.com/SovRing-Labs/omnitool; export FLEET_EXEC=<clone>/bin/fleet-exec)

## Known failures and honest negatives

- **TAL9** Recorded metrics and the early '65 passed' correction. The RSS number cannot be re-checked from the repo (no harness recorded); the test counts have since moved (see TAL1). Recorded and re-checked 2026-10-16 (PASS); evidence: `DOSSIER.md`, `release-scan.txt`

## Corrections

- No corrections touch this repo.

## How to re-check

- One file, any python3 >= 3.8: download [`sovring-suite.pyz`](https://github.com/SovRing-Labs/SovRing/releases/latest/download/sovring-suite.pyz), then `python3 sovring-suite.pyz --run talon` (headless) or `python3 sovring-suite.pyz` (local page on 127.0.0.1).
- From this checkout: `bash install.sh` (checks python, prepares a venv, runs the suite headless, then opens the page; `--no-gui` to skip the page).
- Quick test only: `PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/`.

## Defensive publication

- `TECHNICAL-DISCLOSURE.md` — on public GitHub since 2026-10-01
- `TECHNICAL-DISCLOSURE-PART-2.md` — on public GitHub since 2026-10-10
- DOI: [10.5281/zenodo.23282041](https://doi.org/10.5281/zenodo.23282041) (Zenodo, see `CITATION.cff`)
- Licence: PolyForm Small Business 1.0.0 OR PolyForm Noncommercial 1.0.0 (your choice); PolyForm Free Trial 1.0.0 for evaluation; data and shard format CC-BY-4.0
