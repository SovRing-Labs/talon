# talon — Technical Disclosure, Part 2 (2026-10-08): live streams, long-answer continuation, spend and context control, a loop guard, a resident executor, a harness as a head

**Title:** A local-first agent engine that (a) streams model text, reasoning and tool calls to the page as they happen, (b) continues answers cut by an output limit, (c) prices every message, shows the context in use and compacts it on request, (d) cancels a model call that stalls and pauses a run that repeats itself, (e) sends tool calls to a resident executor process, and (f) can drive an outside agent CLI as one of its model heads while it keeps the session store.
**Part 1:** `TECHNICAL-DISCLOSURE.md`. **First public disclosure:** the date of the first public commit of this file. **Licence of the code:** as Part 1 (`LICENSING.md`). **Intent:** published so that everything it describes is prior art; the authors claim no patent and ask that none be granted to anyone on it. **Hardware:** Intel i5-8365U laptop, Linux. Numbers are from the project's development log; the release tests (207 passed, 13 skipped) are in `tests/`.

---

## 1. Summary
1. **Live event stream.** A per-session bus carries text, reasoning and tool-call deltas; `GET /api/sessions/{id}/events` serves them as server-sent events and the bundled page renders them as they arrive instead of polling finished messages.
2. **Length continuation.** A reply that stops because of the output limit is continued, up to three times, and stored as one message. A follow-up sent while a run is finishing is queued by the page and sent when the run is free.
3. **Per-model limits.** Each lane carries its own output and context limits, instead of one blanket 4096-token request.
4. **Spend and context.** Usage is normalised per provider (thinking counted as output, long-context price tier, cache), each message carries a cost, `/api/spend` totals it, and a context meter plus `/compact` and `/autocompact` control the window. Retries (429 / 5xx) appear in the transcript as notices.
5. **Cancel and loop guard.** The model call runs on a worker thread, so Stop abandons a stalled read at once; a guard pauses a run after three identical call/result shapes in its last six.
6. **Resident executor.** Tool calls go to a resident fork server when its socket exists and fall back to a subprocess otherwise, with identical results.
7. **Harness as a head.** `talon run -m <cli>/<model>` runs an outside agent CLI in its streaming-JSON mode as one head; Talon keeps the session rows, delivers mid-run messages to its input, and enforces the exit contract.
8. **Session hygiene.** Saved sessions drop any environment variable whose name contains KEY, TOKEN, SECRET, PASSW, CREDENTIAL, AUTH, COOKIE or PRIVATE; tests and the self-test no longer write to the real session store.
9. **Core ledger.** Each session's tools carry a caller id; receipts from the fleet's core registry are synced into a ledger table and shown as transcript notices. This needs a receipts file that this release does not ship.

## 2. Live stream (`src/engine/server.py`, providers)
Providers were rewritten to stream. Each delta goes to a per-session bus; the SSE endpoint replays from the bus; the page uses `EventSource` and reveals text smoothly. For Vertex-hosted Gemini models the request sets `include_thoughts`, otherwise no reasoning is returned. Measured: `curl -N` on the events URL during a run shows deltas; one provider holds its output about 15 s before the first delta (provider side, not ours).

## 3. Long answers and limits (`src/engine/loop.py`, `providers.py`)
Failure found: answers from a Vertex model ended mid-sentence. The 4096-token limit included hidden thinking and `finish_reason=length` was treated as final. Fix: continue length-cut replies up to three times as one stored message; a Vertex cap of 16,384; per-model limits from a limits table filled from published model metadata. Check: `tests/test_loop_length_continue.py`; a live probe with the limit forced to 400 tokens.

## 4. Spend, context, compaction
Failure found: Vertex sessions showed a cost of $0 over 22.9 M tokens, because the endpoint returns no cost and reports thinking tokens outside the completion count. Fix: price from public model metadata (thinking as output, the 200k tier, cache read/write); per-message cost; `/api/spend`; a `ctx_tokens` meter; `/compact`, `/autocompact`; provider retries written as `notice` messages. A backfill repriced stored sessions (at least $33.11 of previously invisible spend).

## 5. Stop and the loop guard
Failure found: Stop was checked only between turns, and a stalled read blocked the run thread (180 s, four retries). Fix: the model call runs on a worker thread and `provider.cancel` abandons it (0.35 s measured live); a guard (`TALON_LOOP_GUARD`) pauses after three same-shape call/result pairs in the last six. Not measured: false-pause rate on long legitimate runs.

## 6. Resident executor
Every tool call used to start the executor as a new Python process (about 170 to 200 ms). A resident fork server (warm interpreter, one forked child per request, full environment forwarded) answers instead when `FLEET_EXEC_SOCK` points at it. Measured (20 calls, identical results on both paths): `bash` 197.7 to 38.4 ms, `write` 179.0 to 22.9 ms. The 20 ms bar was **not met** (registered; it failed). The executor itself is a separate project (Tarsus).

## 7. Harness as a head (`src/engine/harness_head.py`)
Talon starts the outside CLI with stream-JSON input and output, owns the session rows and usage, writes mid-run messages to its stdin, and ends the run on its own exit line. Three faults were found and fixed the first day: only the last turn's result reached stdout (a DONE line before a notification turn was lost); silent thinking was read as a stall (now `thinking ~N tokens` progress lines); a turn that started background work closed stdin before the completion turn (stdin now stays open until a turn starts no new background work, with a 30-minute idle cap). Verified by three probes (a tool-using run, a mid-run message, a background sleep). Interface use only; no code from that CLI is included.

## 8. What is in this release and what is not
Talon's terminal modules (`term`, `rec`, `recall`, `traces`, `tsift`, `swapin`) drive a process watcher that is not released; they are excluded, and `talon term` prints a message. The core ledger is included but only useful with the fleet's receipts file.

## 9. Not measured / open
- Cost figures rest on published price tables and were checked against stored sessions, not against provider invoices.
- The loop guard's false-pause rate; compaction quality (what the summary loses).
- Latency of the SSE path under many concurrent sessions.
- The 20 ms resident-executor bar (failed at 22.9 ms for `write`).

## 10. Provenance (borrowed / ours)
**Borrowed:** server-sent events; the OpenAI-compatible chat API shape; Application Default Credentials for Vertex; public model price and limit tables; the streaming-JSON interface of an outside agent CLI; fork servers as a technique. **Ours:** the per-session bus and replay, length continuation as one stored message, the usage normaliser, the loop guard on call/result shapes, the head socket and its exit contract, the environment filter for saved sessions, the core ledger. The earlier development log is private; this Part states only what the numbers above show.
