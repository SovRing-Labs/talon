# AGENTS.md — talon

For coding assistants (and people) setting this repository up. Plain steps; every command runs from the repo root.

## What it is
`talon` is a standalone, lightweight agent execution engine and role-shell runtime developed for the Sovereign Raptor fleet. It provides: 1. The Talon Engine: An autonomous agent loop featuring multi-provider endpoint routing (direct HTTP/vLLM endpoints, local Ollama, OpenRouter, Groq), tool execution through standard JSON protocols (such as Omnitool or built-in tool handlers), turn-level message compaction, and persistent session storage. 2. The Role Shell: Specialized interactive terminal shells configured per agent role (`commander`, `builder`, `dev`, `oracle`, `researcher`, `grunt`, …

## Install and check (3 commands)
```bash
git clone --depth 1 https://github.com/SovRing-Labs/talon && cd talon
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -q pytest pyte
make test   # expect: All Talon release tests PASSED.
```
Needs: python3>=3.10, pytest, pyte.
Measured: 45.2s on a fresh clone (Intel i5-8365U laptop, 2026-10-16, exit 0).

## What the tests mean
`suite.json` lists every claim this repository makes with its verify command, the expected output, what passing means, how the number was obtained and how to use it in your own work (being written). `STATUS.md` says what works today, what is not yet tested and the known failures; the umbrella's `STATUS.md` has one row per repository.

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
