# talon changelog

## 0.2.0 (2026-10-10)
Talon 0.2.0 (2026-10-01 to 2026-10-08): the session backend gained a live stream, more model lanes, a terminal with recall, and a ledger of cores it fired.

- Fast path: when `FLEET_EXEC_SOCK` is set, tool calls go to the resident `fleet-execd` fork server (198 to 38 ms per call), identical results; falls back to the subprocess.
- Vertex (Gemini) lanes: answers no longer end mid-sentence (output-limit continuation), each lane has its own output limit instead of one 4096 default, and the live thinking / text / tool stream is served over SSE (`/api/sessions/{id}/events`).
- Spend, context and compaction: per-session cost is computed for Vertex sessions, the context view and compaction control exist, and 429 / 5xx retries are visible. A loop guard lets Stop end a looping run instead of waiting on a stalled read.
- Harness as a head: `engine/harness_head.py` runs the Claude Code CLI (`claude -p`) as a lane (interface use only).
- `talon term`: a system terminal (`term`, `rec`, `recall`, `traces`), typed pipes, command recall, failure explanations, and `tsift` / `swapin` sift tools. These use the fleet's `bin/tw`, a wasm app and `omni-spec` that this repo does not ship; they import cleanly and do nothing useful without the fleet.
- Core ledger (`engine/ledger.py`): a session can see and record the cores it fired. It needs a `core-receipts.jsonl`, which this repo does not ship.
- Fix: the test suite and `talon --selftest` no longer rewrite saved session files, and saved sessions no longer carry the process environment.
- Tests need `pytest` and `pyte`. Tests that need live network or fleet files skip unless `TALON_FLEET=1`; do not set it unless you have a fleet. Set `MCP_MAX_CHARS` only if you mean to.

## 0.1.0 (2026-10-01)
Initial public release: agent engine (`talon run/serve`), role shells, portable `bin/talon`; full suite 196 tests
(184 pass / 12 skip on a clean machine); Omnitool via FLEET_EXEC; TECHNICAL-DISCLOSURE (prior art); DOSSIER. Weekly updates follow.
