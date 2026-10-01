# talon: Standalone Agent Runtime & Role-Shell Execution Engine

**Version:** 0.1.0  
**Licensor:** Juan Carlos De Santiago  
**Licence:** Dual **PolyForm Small Business 1.0.0 OR PolyForm Noncommercial 1.0.0** (user's choice). See `LICENSING.md`.  
**Prior Art Disclosure:** Published defensively as prior art; see `TECHNICAL-DISCLOSURE.md`.

---

## 1. Overview

`talon` is a standalone, lightweight agent execution engine and role-shell runtime developed for the Sovereign Raptor fleet. It provides:
1. **The Talon Engine:** An autonomous agent loop featuring multi-provider endpoint routing (direct HTTP/vLLM endpoints, local Ollama, OpenRouter, Groq), tool execution through standard JSON protocols (such as Omnitool or built-in tool handlers), turn-level message compaction, and persistent session storage.
2. **The Role Shell:** Specialized interactive terminal shells configured per agent role (`commander`, `builder`, `dev`, `oracle`, `researcher`, `grunt`, `bugstomp`), injecting role-tailored command registries and contextual plugins.
3. **`talon run` & `talon serve`:** Unified CLI interface to run single tasks or serve a local web dashboard for session monitoring with strict Origin-header security checks.

---

## 2. Quickstart & Usage

### Running Tasks

Run a task using a local Ollama model:
```bash
python3 src/talon_run.py -m ollama/qwen2.5:7b "Audit the src/ directory and summarize entry points"
```

Run a task using a generic OpenAI-compatible vLLM endpoint:
```bash
export TALON_API_KEY="optional-bearer-key"
python3 src/talon_run.py -m "http://localhost:8000/v1::meta-llama/Llama-3-8B" "Refactor module X"
```

### Starting the Server

```bash
python3 src/main.py --server --port 8919
```

---

## 3. Configuration & Credentials

### API Keys
Talon resolves API keys in deterministic order:
1. Environment variables (`TALON_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, etc.)
2. User configuration file: `~/.config/talon/keys.json`
3. Local/Ollama models are keyless and require no external keys.

*Note:* Talon never writes or exports API keys. All state is stored locally under `~/.local/share/talon/talon.db`.

---

## 4. Verification

Run the test suite:
```bash
make test
```
Requires Python 3.10+ and `pytest`.

---

## Dependencies and tests

Talon runs every mutating tool call (bash, write, edit) through **Omnitool**: clone
https://github.com/SovRing-Labs/omnitool and `export FLEET_EXEC=/path/to/omnitool/bin/fleet-exec`. Without it, read-only
tools work and mutating tools return an explicit error.

`make test` runs the full suite (196 tests). Tests that need Omnitool skip unless `FLEET_EXEC` is set; tests that need a
lane catalogue, OpenCode role config and provider keys skip unless `TALON_FLEET=1`. Reasons print with `pytest -rs`.
History, metrics and limits: `DOSSIER.md`.
