# AGENTS.md — talon

For coding assistants (and people) setting this repository up. Plain steps; every command runs from the repo root.

**Status: pre-alpha, under development.** Interfaces, numbers and file layout can change without notice. Read `STATUS.md` first: what works today (each line names its test and evidence file), what is not yet tested, the known failures and the corrections.

## What it is
Standalone agent runtime and role-shell terminal: talon run/serve, multi-provider lanes, tool loop, session store.

## Install and check (3 commands)
```bash
git clone --depth 1 https://github.com/SovRing-Labs/talon && cd talon
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -q pytest pyte
make test   # expect: All Talon release tests PASSED.
```
Needs: python3>=3.10, pytest, pyte.
Whole suite instead: `bash install.sh --no-gui` (python check → venv → every runnable `suite.json` test, headless; without `--no-gui` it then opens the local results page on 127.0.0.1).
Measured: 45.2s on a fresh clone (Intel i5-8365U laptop, 2026-10-16, exit 0).

## What the tests mean
`suite.json` lists every claim this repository makes with its verify command, the expected output, what passing means, how the number was obtained and how to use it in your own work (10 tests, in `suite.json` at the repository root). `STATUS.md` says what works today, what is not yet tested and the known failures; the umbrella's `STATUS.md` has one row per repository.

## Where the evidence is
- `DOSSIER.md` — history, metrics and limits
- `TECHNICAL-DISCLOSURE.md` — the method, as dated prior art
- `CITATION.cff` — DOI 10.5281/zenodo.23282041

## Honest limits (from DOSSIER.md)
Commander role plugins (fleet status, dispatcher queries) are not in 0.1.0; they read the original fleet's databases. Role prompts and tools are read from an OpenCode-style config; without one, roles fall back to defaults. No offline mode by design.

## Licence
PolyForm Small Business 1.0.0 OR PolyForm Noncommercial 1.0.0 (free trial for evaluation). Details: `LICENSING.md`.

## Guided setup through MCP
The SovRing umbrella ships an MCP server that explains, checks readiness, clones and runs these tests: https://github.com/SovRing-Labs/SovRing (README → "Paste this to your assistant"). Zero-install docs endpoint: https://gitmcp.io/SovRing-Labs/talon
