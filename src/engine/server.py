"""talon serve — local API + web front end for the Talon engine (2026-09-26).

Loopback only (127.0.0.1). Runs agent sessions in background threads on the same engine as
`talon run`, persisted in the same talon.db, so CLI runs and dispatcherd runs show up in the UI.

  GET  /                              the web front end (static/index.html)
  GET  /api/health                    {"ok": true}
  GET  /api/roles  /api/lanes         pickers (lanes = resolvable live lanes)
  GET  /api/sessions                  newest first
  GET  /api/sessions/<id>?after=N     session + messages with seq > N
  POST /api/sessions                  {"prompt", "role", "lane", "title"?}      -> {"id"}
  POST /api/sessions/<id>/messages    {"prompt", "lane"?}  continue a session  -> {"id"}
  POST /api/sessions/<id>/stop        stop after the current turn
  POST /api/sessions/<id>/nudge       {"message"} → delivered at the run's next turn ([OPS-INJECT])
  POST /api/sessions/<id>/compact     {"focus"?}  model-written summary replaces the history sent
  POST /api/sessions/<id>/settings    {"autocompact": tokens|0}  compact before a turn at that context
  GET  /api/spend                     paid spend: today / 7 days / all, per lane (TALON-VERTEX-OPS-0)

Safety: tools run with the user's permissions, so write requests must come from this origin — a POST
carrying a foreign Origin header is refused (blocks cross-site requests from a browser tab), and
the server never binds a non-loopback address.
"""

from __future__ import annotations

import argparse
import json
import os
import logging
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import harness_head  # Claude heads startable from the launcher via talon serve
from urllib.parse import parse_qs, urlparse

from . import config
from .loop import ReentrantAgentLoop, compact
from .providers import LANE_CATALOG_DB, LaneError, OpenAICompatibleClient, lane_limits, lane_price, resolve_lane
from . import sessions as _sessions
from .sessions import SessionStore
from .tools import OmnitoolBridge
from . import field

log = logging.getLogger("talon.engine.server")
STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_WORKSPACE = "."
_STOP: dict[str, threading.Event] = {}
_RUNNING: set[str] = set()
_PROVIDERS: dict[str, OpenAICompatibleClient] = {}   # run's model client, so Stop can abandon its in-flight call
_LOCK = threading.Lock()


class _Stopped(Exception):
    pass


# ── live events (TALON-SSE-1, J 10-02: "the thoughts/tools and message live stream is not live")
# One in-process bus: every viewer of a session gets a queue. Events:
#   {"type":"status","status":"running"|"done"|"failed"|"stopped","error":...}
#   {"type":"message","turn":n,"message":<stored row>}           (user / assistant / tool, as written)
#   {"type":"delta","turn":n,"kind":"text"|"reasoning"|"tool"|"reset"|"turn","text":...}
import queue as _queue

_SUBS: dict[str, list[_queue.Queue]] = {}
_TURN: dict[str, int] = {}
_SUBS_LOCK = threading.Lock()


def publish(sid: str, event: dict) -> None:
    with _SUBS_LOCK:
        subs = list(_SUBS.get(sid, ()))
    for q in subs:
        try:
            q.put_nowait(event)
        except _queue.Full:
            pass  # a stalled viewer drops deltas; it re-syncs from the stored messages


def _on_message(sid: str, row: dict) -> None:
    publish(sid, {"type": "message", "turn": _TURN.get(sid, 0), "message": row})


_sessions.LISTENERS.append(_on_message)


def _live_lanes() -> list[str]:
    try:
        rows = sqlite3.connect(f"file:{LANE_CATALOG_DB}?mode=ro", uri=True).execute(
            "SELECT id FROM lanes WHERE status='live' ORDER BY health_score DESC, id").fetchall()
    except sqlite3.Error:
        return []
    out = []
    default = os.environ.get("TALON_LANE", "openrouter/nvidia/nemotron-3-super-120b-a12b:free")
    rows = sorted(rows, key=lambda r: r[0] != default)
    for (lane,) in rows:
        if harness_head.handles(lane):          # a harness head: offer its models, not the bare lane id
            out += [f"{lane}/sonnet", f"{lane}/opus"]
            continue
        try:
            resolve_lane(lane)
            out.append(lane)
        except LaneError:
            pass
    # Vertex Gemini models from opencode.jsonc's vertexii block (not catalog lanes: Vertex is
    # reached with ADC, not a catalog key). Offered only when ADC actually yields a token.
    try:
        from .providers import vertex_models, adc_token
        if vertex_models() and adc_token():
            out += [f"vertexii/{m}" for m in vertex_models()]
    except Exception:
        pass
    return out


def _start_run(sid: str, prompt: str, role: str, lane: str, cwd: str) -> None:
    stop = threading.Event()
    with _LOCK:
        _STOP[sid] = stop
        _RUNNING.add(sid)

    def work() -> None:
        store = SessionStore()
        try:
            store.conn.execute("UPDATE sessions SET status='running', error=NULL, lane=? WHERE id=?", (lane, sid))
            store.conn.commit()

            def on_turn(turn, msg):
                if stop.is_set():
                    raise _Stopped()

            def on_delta(turn, kind, text):
                _TURN[sid] = turn
                publish(sid, {"type": "delta", "turn": turn, "kind": kind, "text": text})

            publish(sid, {"type": "status", "status": "running"})

            field.arrive(sid, None, role, lane)
            if harness_head.handles(lane):
                res = harness_head.run_claude_head(prompt=prompt, role=role, lane=lane, cwd=cwd, store=store,
                                                   sid=sid, inbox=field.Inbox(sid, None))
                store.finish(sid, "done" if res.get("ok") else "failed", None if res.get("ok") else res.get("error"))
                field.debrief(sid, None, role, bool(res.get("ok")), res.get("content") or res.get("error") or "")
                return
            provider = _PROVIDERS[sid] = OpenAICompatibleClient.from_lane(lane)
            if stop.is_set():
                provider.cancel.set()
            loop = ReentrantAgentLoop(provider_client=provider,
                                      tool_bridge=OmnitoolBridge(Path(cwd), env_extra={"TALON_SESSION": sid, "TALON_ROLE": role}),
                                      store=store)
            res = loop.run(prompt=prompt, role=role, lane=lane, on_turn=on_turn, session_id=sid,
                           inbox=field.Inbox(sid, None), on_delta=on_delta)
            store.finish(sid, "done" if res.get("ok") else "stopped" if res.get("stopped") else "failed",
                         None if res.get("ok") else res.get("error"))
            field.debrief(sid, None, role, bool(res.get("ok")), res.get("content") or res.get("error") or "")
        except _Stopped:
            store.finish(sid, "stopped", "stopped from the UI")
        except Exception as e:
            store.finish(sid, "failed", f"{type(e).__name__}: {e}")
        finally:
            store.close()
            with _LOCK:
                _RUNNING.discard(sid)
                _STOP.pop(sid, None)
                _PROVIDERS.pop(sid, None)
            _TURN.pop(sid, None)
            try:
                st2 = SessionStore()
                row = st2.get(sid) or {}
                st2.close()
                publish(sid, {"type": "status", "status": row.get("status") or "done", "error": row.get("error")})
            except Exception:
                publish(sid, {"type": "status", "status": "done"})

    threading.Thread(target=work, name=f"talon-{sid}", daemon=True).start()


def _start_compact(sid: str, lane: str, focus: str) -> None:
    """Compaction runs like a turn: the session shows running, notices stream to viewers."""
    with _LOCK:
        _RUNNING.add(sid)

    def work() -> None:
        store = SessionStore()
        note = lambda level, text: store.append(sid, {"role": "notice", "name": level, "content": text})
        try:
            publish(sid, {"type": "status", "status": "running"})
            note("info", "compacting the conversation…" + (f" (focus: {focus})" if focus else ""))
            provider = OpenAICompatibleClient.from_lane(lane)
            provider.on_notice = note
            compact(provider, store.history(sid), store=store, session_id=sid, focus=focus, note=note)
        except Exception as e:
            note("error", f"compaction failed: {e}")
        finally:
            with _LOCK:
                _RUNNING.discard(sid)
            row = store.get(sid) or {}
            store.close()
            publish(sid, {"type": "status", "status": row.get("status") or "done", "error": row.get("error")})

    threading.Thread(target=work, name=f"talon-compact-{sid}", daemon=True).start()


def _spend() -> dict:
    lt = time.localtime()
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    st = SessionStore()
    try:
        today = st.spend(midnight)
        return {"today": today["cost"], "week": st.spend(midnight - 6 * 86400)["cost"], "all": st.spend(0)["cost"],
                "lanes_today": today["lanes"]}
    finally:
        st.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "talon/1.0"

    def log_message(self, fmt, *args):  # quiet access log
        log.debug(fmt, *args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if u.path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts == ["api", "health"]:
            return self._json({"ok": True, "running": len(_RUNNING)})
        if parts == ["api", "roles"]:
            return self._json({"roles": config.known_roles() or ["dev"]})
        if parts == ["api", "lanes"]:
            return self._json({"lanes": _live_lanes()})
        if parts == ["api", "spend"]:
            return self._json(_spend())
        if parts == ["api", "sessions"]:
            st = SessionStore()
            try:
                return self._json({"sessions": st.list(100)})
            finally:
                st.close()
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "events":
            return self._events(parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
            after = int((parse_qs(u.query).get("after") or ["0"])[0] or 0)
            st = SessionStore()
            try:
                s = st.get(parts[2])
                if not s:
                    return self._json({"error": "no such session"}, 404)
                s["live"] = parts[2] in _RUNNING
                s["ctx_limit"], s["max_output"] = lane_limits(s.get("lane") or "")
                s["price"] = lane_price(s.get("lane") or "")
                if not s.get("ctx_tokens"):  # never measured (pre-2026-10-04 run): estimate from what would be sent
                    s["ctx_tokens"] = int(len(json.dumps(st.history(parts[2]), default=str)) / 3.5)
                    s["ctx_estimated"] = True
                return self._json({"session": s, "messages": st.messages(parts[2], after)})
            finally:
                st.close()
        return self._json({"error": "not found"}, 404)

    def _events(self, sid: str) -> None:
        """Server-sent events for one session: status + every stored message + live deltas."""
        q: _queue.Queue = _queue.Queue(maxsize=5000)
        with _SUBS_LOCK:
            _SUBS.setdefault(sid, []).append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            hello = {"type": "status", "status": "running" if sid in _RUNNING else "idle", "turn": _TURN.get(sid, 0)}
            self.wfile.write(f"data: {json.dumps(hello)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = f"data: {json.dumps(ev, default=str)}\n\n"
                except _queue.Empty:
                    payload = ": keepalive\n\n"
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _SUBS_LOCK:
                lst = _SUBS.get(sid, [])
                if q in lst:
                    lst.remove(q)
                if not lst:
                    _SUBS.pop(sid, None)

    def do_POST(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin not in (f"http://{host}", f"http://127.0.0.1:{self.server.server_port}",
                                     f"http://localhost:{self.server.server_port}"):
            return self._json({"error": "cross-origin request refused"}, 403)
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        b = self._body()
        if parts == ["api", "sessions"]:
            prompt, lane, role = (b.get("prompt") or "").strip(), b.get("lane") or "", b.get("role") or "dev"
            if not prompt:
                return self._json({"error": "prompt required"}, 400)
            try:
                if not harness_head.handles(lane):
                    resolve_lane(lane)
            except LaneError as e:
                return self._json({"error": str(e)}, 400)
            st = SessionStore()
            sid = st.create(title=b.get("title") or prompt.splitlines()[0][:80], role=role, lane=lane,
                            cwd=b.get("cwd") or DEFAULT_WORKSPACE)
            st.close()
            _start_run(sid, prompt, role, lane, b.get("cwd") or DEFAULT_WORKSPACE)
            return self._json({"id": sid}, 201)
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "messages":
            sid = parts[2]
            if sid in _RUNNING:
                return self._json({"error": "session is still running"}, 409)
            st = SessionStore()
            s = st.get(sid)
            st.close()
            if not s:
                return self._json({"error": "no such session"}, 404)
            prompt = (b.get("prompt") or "").strip()
            if not prompt:
                return self._json({"error": "prompt required"}, 400)
            lane = b.get("lane") or s["lane"]
            try:
                if not harness_head.handles(lane):
                    resolve_lane(lane)
            except LaneError as e:
                return self._json({"error": str(e)}, 400)
            _start_run(sid, prompt, s["role"], lane, s["cwd"] or DEFAULT_WORKSPACE)
            return self._json({"id": sid}, 202)
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "nudge":
            text = (b.get("message") or "").strip()
            if not text:
                return self._json({"error": "message required"}, 400)
            c = field.marks().connect()
            mid = field.marks().send(c, parts[2], text, b.get("from") or "talon-ui")
            c.close()
            return self._json({"queued": mid}, 202)
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] in ("compact", "settings"):
            sid = parts[2]
            st = SessionStore()
            try:
                s = st.get(sid)
                if not s:
                    return self._json({"error": "no such session"}, 404)
                if parts[3] == "settings":
                    try:
                        ac = max(0, int(b.get("autocompact") or 0))
                    except (TypeError, ValueError):
                        return self._json({"error": "autocompact must be a token count (0 = off)"}, 400)
                    st.set(sid, autocompact=ac)
                    st.append(sid, {"role": "notice", "name": "info",
                                    "content": f"auto-compact at {ac:,} tokens of context" if ac else "auto-compact off"})
                    return self._json({"autocompact": ac})
            finally:
                st.close()
            if sid in _RUNNING:
                return self._json({"error": "session is still running"}, 409)
            lane = b.get("lane") or s["lane"]
            if harness_head.handles(lane):
                return self._json({"error": "compaction is for engine lanes; harness heads manage their own context"}, 400)
            try:
                resolve_lane(lane)
            except LaneError as e:
                return self._json({"error": str(e)}, 400)
            _start_compact(sid, lane, (b.get("focus") or "").strip())
            return self._json({"compacting": sid}, 202)
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "stop":
            ev = _STOP.get(parts[2])
            if ev:
                ev.set()
            if parts[2] in _PROVIDERS:
                _PROVIDERS[parts[2]].cancel.set()   # abandon the in-flight model call now, not after it returns
            return self._json({"stopping": bool(ev)})
        return self._json({"error": "not found"}, 404)


def make_server(port: int = 4100) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="talon serve")
    p.add_argument("--port", type=int, default=4100)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    srv = make_server(a.port)
    print(f"talon serve: http://127.0.0.1:{a.port}/  (loopback only; Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
