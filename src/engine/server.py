"""talon serve — local API + web front end for the Talon engine (TALON-COMPLETE, 2026-09-26).

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

Safety: tools run with J's permissions, so write requests must come from this origin — a POST
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .loop import ReentrantAgentLoop
from .providers import LANE_CATALOG_DB, LaneError, OpenAICompatibleClient, resolve_lane
from .sessions import SessionStore
from .tools import OmnitoolBridge
from . import field

log = logging.getLogger("talon.engine.server")
STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_WORKSPACE = "."
_STOP: dict[str, threading.Event] = {}
_RUNNING: set[str] = set()
_LOCK = threading.Lock()


class _Stopped(Exception):
    pass


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
        try:
            resolve_lane(lane)
            out.append(lane)
        except LaneError:
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

            field.arrive(sid, None, role, lane)
            loop = ReentrantAgentLoop(provider_client=OpenAICompatibleClient.from_lane(lane),
                                      tool_bridge=OmnitoolBridge(Path(cwd), env_extra={"TALON_SESSION": sid, "TALON_ROLE": role}),
                                      store=store)
            res = loop.run(prompt=prompt, role=role, lane=lane, on_turn=on_turn, session_id=sid,
                           inbox=field.Inbox(sid, None))
            store.finish(sid, "done" if res.get("ok") else "failed", None if res.get("ok") else res.get("error"))
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

    threading.Thread(target=work, name=f"talon-{sid}", daemon=True).start()


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
        if parts == ["api", "sessions"]:
            st = SessionStore()
            try:
                return self._json({"sessions": st.list(100)})
            finally:
                st.close()
        if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
            after = int((parse_qs(u.query).get("after") or ["0"])[0] or 0)
            st = SessionStore()
            try:
                s = st.get(parts[2])
                if not s:
                    return self._json({"error": "no such session"}, 404)
                s["live"] = parts[2] in _RUNNING
                return self._json({"session": s, "messages": st.messages(parts[2], after)})
            finally:
                st.close()
        return self._json({"error": "not found"}, 404)

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
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "stop":
            ev = _STOP.get(parts[2])
            if ev:
                ev.set()
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
