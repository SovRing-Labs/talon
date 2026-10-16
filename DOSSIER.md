# talon — Dossier

*History, specification, metrics, tests and limits. Companion to `TECHNICAL-DISCLOSURE.md` and `README.md`.*

## What it is

An agent engine and a role-equipped terminal. `talon run` executes an agent task on any OpenAI-compatible endpoint,
local Ollama or a hosted provider; `talon serve` gives a local, loopback-only dashboard; the terminal launcher opens
role shells (commander, dev, oracle, researcher, grunt, bugstomp) with each role's prompt and tools. Every mutating
tool call goes through Omnitool, so an agent's writes get the same repair, validation and guard as everything else.
In the stack it is the **body** that the hands (Omnitool) and memory (omni-ring) attach to. Stage (essay 07):
system → primitive.

## Creation history

| Date (2026) | Milestone |
|---|---|
| 09-08 | Talon begins as the fleet's terminal exoskeleton (role shells, PTY sessions); first DEVLOG entries |
| 09-18 – 09-22 | Curses/flag clean-ups; self-heal reports on the terminal stack; role kit wired (8 roles) |
| 09-26 | Talon engine made standalone: `talon run/serve` as a drop-in for the external agent runner, replacing an earlier scaffold whose gate was a false green (it ran prompts as shell commands when the model was unreachable and still exited 0). No offline fallback since: if the model cannot be reached, the run fails |
| 09-26 | Role tool binaries put on PATH in every shell (53 tools) |
| 09-29 | Release prep: symlinks excluded, keyring fallback opt-in, 65 tests reported |
| 10-01 | Release verification: the 65 covered 4 of 8 test modules; the full suite showed 20 failures, all missing dependencies (launcher script, Omnitool, fleet configuration). Fixed by shipping `bin/talon`, an Omnitool path setting (`FLEET_EXEC`), and explicit, reasoned skips; numbers re-measured |

## Specification (summary)

* **CLI contract:** `talon run --agent ROLE -m LANE [--title T] [--session S] [--brief PATH] PROMPT` — stdout: final
  assistant text; stderr: progress and `TALON_SESSION <id>`; exit 0 finished · 1 model/tool/turn-budget failure ·
  2 bad arguments or lane. `talon sessions`, `talon session <id>`, `talon lanes [--live]`, `talon serve [--port]`.
* **Lanes:** `ollama/<model>`, `http://host/v1::model`, or a lane from a catalogue; providers that only serve their
  own client are refused rather than spoofed.
* **Keys:** environment first, then `~/.config/talon/keys.json`; other fallbacks are opt-in. Talon never writes keys.
* **Store:** sessions in `~/.local/share/talon/talon.db`.
* **Server:** loopback only, with Origin checks against cross-site requests.

## Metrics (2026-10-01, Intel i5-8365U)

| Measurement | Result |
|---|---|
| Full suite, clean machine | 184 passed, 12 skipped, 0 failed (196 tests) |
| Full suite, clean machine, 0.2.0 (2026-10-16 re-run) | 207 passed, 13 skipped, 0 failed (220 tests) |
| Full suite, Omnitool installed | 187 passed, 9 skipped, 0 failed |
| Full suite, original fleet | 195 / 196 (the one: commander plugins, not shipped) |
| `talon serve` idle memory | 32.8 MB RSS |

## Tests

`make test` (full suite; reasons for skips with `pytest -rs`). `FLEET_EXEC=<omnitool>/bin/fleet-exec` enables the
Omnitool tests; `TALON_FLEET=1` enables tests that need a lane catalogue, role config and provider keys.

## Limits

* Commander role plugins (fleet status, dispatcher queries) are not in 0.1.0; they read the original fleet's databases.
* Role prompts and tools are read from an OpenCode-style config; without one, roles fall back to defaults.
* No offline mode by design.

## Lineage and credits

Written in the Sovereign Raptor fleet; see `PROVENANCE.md`. Updates: weekly, recorded here with dates.
