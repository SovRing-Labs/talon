"""TALON Emergency Dashboard - stdlib-only read-mostly fleet dashboard.

Usage:
    talon --dash

Stdlib curses only. Panels: fleet metrics header, task board (dispatch.db),
saved sessions, events log tail. Enter on a session opens a shell in that
session's working directory (honoring saved role/provider/model). Press n for
the new-session wizard: choose name, role, provider and model. Collapses to a
plain-text report when no TTY is present.
"""

import datetime
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import curses
except ImportError:
    curses = None

AI_STACK = Path(".")
DISPATCH_DB = AI_STACK / "control-center" / "dispatch.db"
SESSIONS_DIR = AI_STACK / "data" / "terminal-sessions"
EVENTS_FILE = AI_STACK / "control-center" / "events.jsonl"
ANDON_FILE = Path("/tmp/talon-andon.json")
FLEET_METRICS = AI_STACK / "bin" / "fleet-metrics"
LANE_CATALOG_DB = AI_STACK / "control-center" / "lane-catalog.db"
OPENCODE_BIN = Path("opencode")
SESSION_RC_DIR = Path("/tmp/talon-sessions")

LIVE_STATUSES = ("dispatched", "approved", "staged", "in_progress", "blocked")
STATUS_ORDER = {"dispatched": 0, "approved": 1, "in_progress": 2, "staged": 3, "blocked": 4}
STATUS_QUEUE = {"dispatched": "LIVE", "approved": "QUEUED", "staged": "STAGED", "blocked": "BLOCKED"}

ROLE_LIST = ("commander", "dev", "bugstomp", "builder", "oracle", "researcher", "grunt", "none")

WIZ_STEPS = ("NAME", "ROLE", "PROVIDER", "MODEL", "CONFIRM")


def run_cmd(cmd, timeout=6):
    """Run a command and return (returncode, stdout, stderr); fail-soft on errors."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return None, "", str(exc)


def parse_metrics(text):
    """Parse fleet-metrics text output into a compact dict."""
    metrics = {
        "total": None, "done": None, "staged": None, "active": None,
        "pending": None, "completion": None, "models": [], "error": None,
    }
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Tasks:"):
            for match in re.finditer(r"(\d+)\s+(\w+)", stripped):
                metrics[match.group(2)] = int(match.group(1))
        match = re.search(r"Completion:\s+([\d.]+)%", stripped)
        if match:
            metrics["completion"] = float(match.group(1))
        match = re.search(r"Pending \(active\):\s+(\d+)", stripped)
        if match:
            metrics["pending"] = int(match.group(1))
    model_lines = re.findall(r"^\s*(\S+)\s+(\d+)/(\d+)\s+([\d.]+)%", text, re.MULTILINE)
    metrics["models"] = model_lines[:5]
    return metrics


def get_metrics(metrics_bin=None, timeout=8):
    """Fetch fleet metrics from bin/fleet-metrics."""
    metrics_bin = Path(metrics_bin or FLEET_METRICS)
    if not metrics_bin.exists():
        return {"error": f"missing {metrics_bin}", "warning": True}
    _, stdout, stderr = run_cmd([str(metrics_bin)], timeout)
    parsed = parse_metrics(stdout)
    if parsed["total"] is None and stderr:
        parsed["error"] = stderr.strip()
    return parsed


def read_tasks(db_path=None, limit=12):
    """Read task board from dispatch.db in SQLite read-only mode."""
    db_path = Path(db_path or DISPATCH_DB)
    result = {"counts": {}, "rows": [], "error": None}
    if not db_path.exists():
        result["error"] = f"missing {db_path}"
        return result
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=6)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    try:
        for row in conn.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status"):
            result["counts"][row[0]] = row[1]
        order = "CASE status " + " ".join(
            f"WHEN '{status}' THEN {rank}" for status, rank in STATUS_ORDER.items()
        ) + " ELSE 99 END"
        query = (
            "SELECT id, title, status, role, updated_at FROM tasks "
            f"WHERE status IN ({','.join('?' for _ in LIVE_STATUSES)}) "
            f"ORDER BY {order}, updated_at DESC LIMIT ?"
        )
        result["rows"] = conn.execute(query, (*LIVE_STATUSES, limit)).fetchall()
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        conn.close()
    return result


def _parse_caps(raw):
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def read_catalog(db_path=None):
    """Read providers + enabled models from lane-catalog.db (SQLite read-only).

    Returns {"providers": [{id,name,enabled}], "models": [{id,provider,name,
    caps,cost,quality,speed}], "error": str|None}. Models exclude tombstones.
    """
    candidates = []
    if db_path is not None:
        candidates.append(Path(db_path))
    candidates.append(Path(LANE_CATALOG_DB))
    candidates.append(Path.home() / ".lane-catalog.db")
    found = next((cand for cand in candidates if cand and cand.exists()), None)
    result = {"providers": [], "models": [], "error": None}
    if found is None:
        result["error"] = "lane-catalog.db missing"
        return result
    try:
        conn = sqlite3.connect(f"file:{found}?mode=ro", uri=True, timeout=6)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    try:
        models = [
            {
                "id": row[0], "provider": row[1], "name": row[2] or "",
                "caps": _parse_caps(row[4]), "cost": row[5],
                "quality": row[6], "speed": row[7],
            }
            for row in conn.execute(
                "SELECT id, provider_id, name, best, caps, cost, quality, speed"
                " FROM models WHERE is_enabled=1 AND is_tombstone=0"
                " ORDER BY provider_id, cost ASC, quality DESC"
            )
        ]
        result["models"] = models
        providers = [
            {"id": row[0], "name": row[1] or row[0], "enabled": bool(row[5])}
            for row in conn.execute(
                "SELECT id, name, type, cap, rate_min, is_enabled FROM providers"
            )
        ]
        provider_ids_with_models = {model["provider"] for model in models}
        providers.sort(key=lambda p: (
            0 if p["id"] in provider_ids_with_models else 1,
            0 if p["enabled"] else 1,
            p["id"],
        ))
        result["providers"] = providers
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        conn.close()
    return result


def models_for_provider(catalog, provider_id):
    """Return enabled models, optionally filtered to one provider ('auto' = all)."""
    models = catalog.get("models", []) if catalog else []
    if provider_id in (None, "", "auto"):
        return models
    return [model for model in models if model["provider"] == provider_id]


def model_ref(model, provider_id=None):
    """Compute the opencode -m reference for a model dict."""
    mid = model.get("id", "")
    pid = provider_id or model.get("provider", "")
    if not mid:
        return ""
    if pid and mid.startswith(f"{pid}/"):
        return mid
    if pid:
        return f"{pid}/{mid}"
    return mid


def default_provider(catalog):
    """First enabled provider that has models, else first enabled, else 'auto'."""
    providers = (catalog or {}).get("providers", []) or []
    models = (catalog or {}).get("models", []) or []
    provider_ids_with_models = {model.get("provider") for model in models}
    for provider in providers:
        if provider.get("enabled") and provider["id"] in provider_ids_with_models:
            return provider["id"]
    for provider in providers:
        if provider.get("enabled"):
            return provider["id"]
    return providers[0]["id"] if providers else "auto"


def sanitize_name(name):
    """Sanitize a session name to safe filename characters."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or "").strip("._")
    if not cleaned:
        cleaned = f"manual-{int(time.time())}"
    return cleaned


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save_manual_session(name, role=None, provider=None, model=None,
                        cwd=None, tags=()):
    """Persist a manual session JSON so --list and the dashboard see it."""
    safe_name = sanitize_name(name)
    base_tags = list(tags) or ["manual"]
    if provider:
        base_tags.append(f"provider:{provider}")
    if model:
        base_tags.append(f"model:{model}")
    now = _utc_now()
    record = {
        "name": safe_name,
        "created_at": now,
        "last_active": now,
        "cwd": cwd or os.environ.get("HOME", "/"),
        "env": {},
        "shell": "/bin/bash",
        "worktree": None,
        "tags": base_tags,
        "role": role or "",
        "provider": provider or "",
        "model": model or "",
    }
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SESSIONS_DIR / f"{safe_name}.json", "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return record


def build_session_rc(record):
    """Build a bash --rcfile for a session record (role wind-up + model pin)."""
    role = (record.get("role") or "").lower() or "commander"
    provider = record.get("provider") or ""
    model = record.get("model") or ""
    name = record.get("name") or "session"
    block = []
    block.append(f"# TALON session rc - {name} (dashboard-generated)")
    block.append("[ -f ~/.bashrc ] && source ~/.bashrc")
    block.append(f"export TALON_ROLE={role}")
    block.append(f'export TALON_SESSION={name.replace(" ", "_")}')
    block.append(f"export TALON_SESSION_PROVIDER={provider}")
    block.append(f"export TALON_SESSION_MODEL={model}")
    block.append('export PATH="bin:./.opencode/bin:$PATH"')
    block.append(
        'export PYTHONPATH="./src:'
        './plugins:$PYTHONPATH"'
    )
    if model:
        ref = model if "/" in model else f"{provider}/{model}"
        block.append(
            f'opc() {{ opencode -m "{ref.replace(chr(34), "")}" "$@"; }}'
        )
    block.append(f'export PS1="[{name}] \\w \\$ "')
    block.append(f'echo "[talon] session={name} role={role} provider={provider} model={model}"')
    return "\n".join(block)


def launch_session_record(record):
    """Write the session rc and exec a bash using it (ends the dashboard)."""
    name = record.get("name") or "session"
    os.environ["TALON_DASH_SESSION"] = name
    try:
        SESSION_RC_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    rc_file = SESSION_RC_DIR / f"bashrc-{sanitize_name(name)}.sh"
    rc_file.write_text(build_session_rc(record), encoding="utf-8")
    cwd = record.get("cwd") or os.environ.get("HOME", "/")
    try:
        os.chdir(cwd)
    except OSError:
        pass
    env = dict(os.environ)
    env["PS1"] = f"[{name}] \\w \\$ "
    os.execvpe("/bin/bash", ["/bin/bash", "--rcfile", str(rc_file)], env)


def read_sessions(sessions_dir=None, max_sessions=40):
    """List persisted sessions from data/terminal-sessions sorted by last_active."""
    sessions_dir = Path(sessions_dir or SESSIONS_DIR)
    sessions = []
    if not sessions_dir.exists():
        return []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            last_active = data.get("last_active", "") or ""
            sessions.append({
                "name": data.get("name", path.stem),
                "shell": data.get("shell", "/bin/bash"),
                "cwd": data.get("cwd", ""),
                "worktree": data.get("worktree"),
                "last_active": last_active,
                "tags": data.get("tags", []),
                "role": data.get("role") or "",
                "provider": data.get("provider") or "",
                "model": data.get("model") or "",
            })
        except Exception:
            continue
    sessions.sort(key=lambda s: s["last_active"], reverse=True)
    return sessions[:max_sessions]


def tail_lines(path, count=24):
    """Return the last `count` lines of a file, reading from the end."""
    path = Path(path)
    if not path.exists():
        return ["(no events file)"]
    lines = []
    buffer = b""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            position = size
            while position > 0 and len(lines) < count + 2:
                position = max(0, position - 8192)
                handle.seek(position)
                block = handle.read(min(8192, size - position))
                buffer = block + buffer
                lines = buffer.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ["(events unreadable)"]
    decoded = [line for line in buffer.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return decoded[-count:] or ["(empty events file)"]


def get_andon(andon_file=None):
    """Return andon state string, defaulting to 'clear'."""
    andon_file = Path(andon_file or ANDON_FILE)
    if not andon_file.exists():
        return "clear"
    try:
        return json.loads(andon_file.read_text(encoding="utf-8")).get("state", "clear")
    except Exception:
        return "clear"


def snapshot(limit=12, event_count=24):
    """Gather one complete read-only snapshot of dashboard data."""
    metrics = get_metrics()
    tasks = read_tasks(limit=limit)
    sessions = read_sessions()
    events = tail_lines(EVENTS_FILE, event_count)
    return {
        "ts": int(time.time()),
        "metrics": metrics,
        "tasks": tasks,
        "sessions": sessions,
        "events": events,
        "andon": get_andon(),
    }


def fmt_time(timestamp):
    """Format an ISO timestamp to a compact local time."""
    if not timestamp:
        return "?"
    try:
        iso = timestamp[:19].replace("T", " ")
        return iso
    except Exception:
        return str(timestamp)[:16]


def text_report(data):
    """Render a plain-text report for non-TTY invocation."""
    metrics = data["metrics"]
    lines = ["TALON DASHBOARD (emergency report)"]
    lines.append(f"time={cfg_time(data['ts'])} andon={data['andon']}")
    counts = data["tasks"]["counts"]
    if counts:
        lines.append(
            "tasks: " + " | ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )
    lines.append("")
    lines.append("session:")
    for session in data["sessions"]:
        tag = ""
        if session.get("provider"):
            tag = f" [{session['provider']}/{session.get('model', '')}]"
        lines.append(
            f"  {session['name']:28s} shell={session['shell']} cwd={session['cwd'] or '/'}{tag}"
        )
    lines.append("")
    lines.append("events tail:")
    lines.extend("  " + line for line in data["events"])
    return "\n".join(lines)


def cfg_time(timestamp):
    try:
        return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(timestamp)


def _move_cursor(key, index, length):
    """Return the moved cursor index for j/k/arrows within [0, length)."""
    if length <= 0:
        return 0
    if key in (curses.KEY_DOWN, ord("j")):
        return min(length - 1, index + 1)
    if key in (curses.KEY_UP, ord("k")):
        return max(0, index - 1)
    if key in (curses.KEY_NPAGE, ord("d")):
        return min(length - 1, index + 10)
    if key in (curses.KEY_PPAGE, ord("u")):
        return max(0, index - 10)
    if key in (ord("g"),):
        return 0
    if key in (ord("G"),):
        return max(0, length - 1)
    return index


def _window_for(cursor, length, visible):
    """Return (start, end) so the cursor is always visible in the window."""
    visible = max(1, visible)
    start = min(max(cursor - visible // 2, 0), max(0, length - visible))
    return start, start + visible


def run_dash(role=None):
    """Entry point for talon --dash. Falls back to a text report if no TTY."""
    def worker(stdscr):
        return _run_loop(stdscr, role)

    try:
        if curses is None or not sys.stdin.isatty() or not sys.stdout.isatty():
            print(text_report(snapshot()))
            return 0
        curses.wrapper(worker)
        return 0
    except Exception as exc:
        print(f"[talon-dash] curses unavailable, printing report: {exc}")
        print(text_report(snapshot()))
        return 0


PANEL_TASKS, PANEL_SESSIONS, PANEL_EVENTS = 0, 1, 2
PANEL_NAMES = ("TASKS", "SESSIONS", "EVENTS")

STATUS_COLORS = {
    "dispatched": 2,
    "approved": 6,
    "staged": 6,
    "blocked": 1,
    "done": 3,
    "retired": 8,
}


def _status_color(status, color_map):
    color = color_map.get(status)
    return color if color is not None else 7


def _render_create(stdscr, wiz, catalog):
    """Draw the new-session wizard screen; returns True if a launch happened."""
    try:
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        step = wiz["step"]
        step_name = WIZ_STEPS[step]
        title = f" TALON NEW SESSION  step {step + 1}/{len(WIZ_STEPS)}: {step_name} "
        stdscr.attron(curses.A_BOLD)
        stdscr.addnstr(0, 0, title[: max(0, width - 1)], width - 1)
        stdscr.attroff(curses.A_BOLD)

        if step == 0:
            stdscr.addnstr(2, 0, "session name (Enter to accept, backspace to edit):", width - 1)
            stdscr.addnstr(3, 0, f"> {wiz['name']}_", width - 1)
        elif step == 1:
            _render_option_list(stdscr, 3, width, height - 8,
                                "role (wind-up + aliases):", ROLE_LIST, wiz["role_idx"])
        elif step == 2:
            providers = (catalog or {}).get("providers", []) or []
            labels = [f"{p['id']}  ({'enabled' if p['enabled'] else 'disabled'})" for p in providers]
            if not labels:
                stdscr.addnstr(3, 0, "no providers in lane-catalog.db", width - 1)
            else:
                stdscr.addnstr(2, 0, "provider (auto-balance lanes available separately):", width - 1)
                _render_option_list(stdscr, 3, width, height - 8, "", labels, wiz["prov_idx"])
        elif step == 3:
            provider = _current_provider_id(catalog, wiz)
            models = models_for_provider(catalog, provider)
            labels = []
            for model in models:
                caps = ",".join(model.get("caps", []) or []) or "n/a"
                label = f"{model['id']}  (cost {model['cost']} q{model['quality']} s{model['speed']} caps:{caps})"
                labels.append(label)
            header = f"model for provider '{provider}' ({len(labels)} available):"
            stdscr.addnstr(2, 0, header[: max(0, width - 1)], width - 1)
            if not labels:
                stdscr.addnstr(3, 0, "no enabled models for this provider", width - 1)
            else:
                _render_option_list(stdscr, 3, width, height - 8, "", labels, wiz["model_idx"])
        elif step == 4:
            provider = _current_provider_id(catalog, wiz)
            model = _current_model(catalog, wiz)
            stdscr.addnstr(2, 0, "summary:", width - 1)
            stdscr.addnstr(3, 0, f"  name     = {wiz['name']}", width - 1)
            stdscr.addnstr(4, 0, f"  role     = {ROLE_LIST[wiz['role_idx']]}", width - 1)
            stdscr.addnstr(5, 0, f"  provider = {provider}", width - 1)
            stdscr.addnstr(6, 0, f"  model    = {model.get('id', '(none)') if model else '(none)'}", width - 1)
            if model:
                ref = model_ref(model, provider)
                stdscr.addnstr(7, 0, f"  opencode -m {ref}", width - 1)

        footer = "ENTER next  j/k move  b back  esc cancel  (n = new session)"
        stdscr.attron(curses.A_REVERSE)
        stdscr.addnstr(height - 1, 0, footer[: max(0, width - 1)], width - 1)
        stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()
    except curses.error:
        pass
    return False


def _render_option_list(stdscr, top, width, max_rows, header, options, cursor):
    if header:
        stdscr.addnstr(top, 0, header[: max(0, width - 1)], width - 1)
        top += 1
    start, end = _window_for(cursor, len(options), max_rows)
    end = min(end, len(options))
    row = top
    for index in range(start, end):
        if row >= top + max_rows:
            break
        marker = ">" if index == cursor else " "
        line = f" {marker} {options[index]}"
        attr = curses.A_BOLD if index == cursor else curses.color_pair(4)
        stdscr.addnstr(row, 0, line[: max(0, width - 1)], width - 1, attr)
        row += 1


def _current_provider_id(catalog, wiz):
    providers = (catalog or {}).get("providers", []) or []
    index = wiz["prov_idx"]
    if providers and index < len(providers):
        return providers[index]["id"]
    return "auto"


def _current_model(catalog, wiz):
    provider = _current_provider_id(catalog, wiz)
    models = models_for_provider(catalog, provider)
    index = wiz["model_idx"]
    if models and index < len(models):
        return models[index]
    return None


def _run_loop(stdscr, role):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    defaults = {}
    for index in range(1, 8):
        try:
            curses.init_pair(index, index, -1)
            defaults[index] = index
        except curses.error:
            pass
    color_map = ({status: curses.color_pair(pair) for status, pair in STATUS_COLORS.items()}
                 if defaults else {})

    data = snapshot()
    catalog = read_catalog()
    scroll = {PANEL_TASKS: 0, PANEL_SESSIONS: 0, PANEL_EVENTS: 0}
    focus = PANEL_TASKS
    message = ""
    wiz = None

    def render():
        nonlocal message
        try:
            if wiz is not None:
                _render_create(stdscr, wiz, catalog)
                return
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            header_rows, footer_rows = 3, 1
            body_rows = max(1, height - header_rows - footer_rows)
            tasks_rows = max(2, int(body_rows * 0.45))
            sessions_rows = max(2, int(body_rows * 0.3))
            events_rows = max(1, body_rows - tasks_rows - sessions_rows)

            andon = data["andon"]
            metrics = data.get("metrics", {}) or {}
            counts = (data.get("tasks", {}) or {}).get("counts", {}) or {}
            headline = f"TALON DASHBOARD  andon={andon}  "
            parts = [f"staged={counts.get('staged', 0)}", f"queued={counts.get('approved', 0)}",
                     f"live={counts.get('dispatched', 0)}", f"done={counts.get('done', 0)}",
                     f"total={counts.get('total', 0)}"]
            if metrics.get("pending") is not None:
                parts.append(f"pending={metrics['pending']}")
            headline += " ".join(parts)
            if metrics.get("completion") is not None:
                headline += f"  completion={metrics['completion']:.0f}%"

            stdscr.attron(curses.A_BOLD)
            for index, text in enumerate([headline, f"time={cfg_time(data['ts'])}"]):
                stdscr.addnstr(index, 0, text[: max(0, width - 1)], width - 1)
            stdscr.attroff(curses.A_BOLD)

            top = header_rows
            _draw_panel(stdscr, top, width, tasks_rows, PANEL_NAMES[PANEL_TASKS],
                        _tasks_lines(data, color_map), focus == PANEL_TASKS, scroll[PANEL_TASKS], message)
            top += tasks_rows
            _draw_panel(stdscr, top, width, sessions_rows, PANEL_NAMES[PANEL_SESSIONS],
                        _sessions_lines(data), focus == PANEL_SESSIONS, scroll[PANEL_SESSIONS], "")
            top += sessions_rows
            _draw_panel(stdscr, top, width, events_rows, PANEL_NAMES[PANEL_EVENTS],
                        data.get("events", []), focus == PANEL_EVENTS, scroll[PANEL_EVENTS], "")

            footer = (
                "q quit  r refresh  TAB panel  n NEW SESSION  j/k scroll  "
                "ENTER open session  (emergency text dashboard)"
            )
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(height - 1, 0, footer[: max(0, width - 1)], width - 1)
            stdscr.attroff(curses.A_REVERSE)
            stdscr.refresh()
        except curses.error:
            pass

    def handle_create(key):
        nonlocal wiz, data, message
        step = wiz["step"]
        if key in (ord("q"), ord("Q"), 27):
            wiz = None
            return
        if key in (ord("b"), ord("B")):
            if step > 0:
                wiz["step"] = step - 1
            return
        if step == 0:
            if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                wiz["name"] = sanitize_name(wiz["name"].strip() or f"manual-{int(time.time())}")
                wiz["step"] = 1
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                wiz["name"] = wiz["name"][:-1]
            elif 32 <= key <= 126:
                wiz["name"] += chr(key)
            return
        if step in (1, 2, 3):
            index_key = "role_idx" if step == 1 else ("prov_idx" if step == 2 else "model_idx")
            length = len(ROLE_LIST) if step == 1 else len((catalog or {}).get("providers", []) or [])
            if step == 3:
                provider = _current_provider_id(catalog, wiz)
                length = len(models_for_provider(catalog, provider))
            if key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
                if length > 0:
                    wiz["step"] = step + 1
                    if step == 2:
                        wiz["model_idx"] = 0
            else:
                wiz[index_key] = _move_cursor(key, wiz[index_key], length)
            return
        if step == 4:
            if key in (ord("y"), ord("Y"), curses.KEY_ENTER, ord("\n"), ord("\r")):
                provider = _current_provider_id(catalog, wiz)
                model = _current_model(catalog, wiz)
                record = save_manual_session(
                    wiz["name"],
                    role=None if ROLE_LIST[wiz["role_idx"]] == "none" else ROLE_LIST[wiz["role_idx"]],
                    provider=provider,
                    model=model.get("id", "") if model else "",
                    cwd=os.environ.get("HOME", "/"),
                )
                message = f"created session {record['name']}, launching..."
                try:
                    curses.endwin()
                except Exception:
                    pass
                launch_session_record(record)
                return
            return

    while True:
        render()
        try:
            key = stdscr.getch()
        except curses.error:
            break
        if wiz is not None:
            handle_create(key)
            continue
        if key in (ord("q"), ord("Q"), 27):
            break
        elif key in (ord("r"), ord("R"), ord(" "), curses.KEY_F5):
            data = snapshot()
            catalog = read_catalog()
            message = f"refreshed {cfg_time(data['ts'])}"
        elif key in (ord("n"), ord("N")):
            providers = (catalog or {}).get("providers", []) or []
            default_prov = default_provider(catalog)
            prov_idx = next((i for i, provider in enumerate(providers)
                             if provider["id"] == default_prov), 0)
            wiz = {
                "step": 0,
                "name": "",
                "role_idx": 0,
                "prov_idx": prov_idx,
                "model_idx": 0,
            }
            message = ""
        elif key in (ord("\t"),):
            focus = (focus + 1) % 3
            scroll[focus] = min(scroll[focus], _max_scroll(data, focus))
        elif key in (ord("1"),):
            focus = PANEL_TASKS
        elif key in (ord("2"),):
            focus = PANEL_SESSIONS
        elif key in (ord("3"),):
            focus = PANEL_EVENTS
        elif key in (curses.KEY_DOWN, ord("j")):
            scroll[focus] = min(scroll[focus] + 1, _max_scroll(data, focus))
        elif key in (curses.KEY_UP, ord("k")):
            scroll[focus] = max(0, scroll[focus] - 1)
        elif key in (curses.KEY_PPAGE, ord("u")):
            scroll[focus] = max(0, scroll[focus] - 10)
        elif key in (curses.KEY_NPAGE, ord("d")):
            scroll[focus] = min(scroll[focus] + 10, _max_scroll(data, focus))
        elif key in (ord("g"),):
            scroll[focus] = 0
        elif key in (ord("G"),):
            scroll[focus] = _max_scroll(data, focus)
        elif key in (curses.KEY_ENTER, ord("\n"), ord("\r")):
            if focus == PANEL_SESSIONS and data["sessions"]:
                chosen_index = scroll[PANEL_SESSIONS]
                if chosen_index < len(data["sessions"]):
                    _confirm_and_open(stdscr, data["sessions"][chosen_index])
                    data = snapshot()
                    message = "session opened (re-launch talon --dash to return)"
    return 0


def _max_scroll(data, panel):
    if panel == PANEL_TASKS:
        count = len((data.get("tasks", {}) or {}).get("rows", []))
    elif panel == PANEL_SESSIONS:
        count = len(data.get("sessions", []))
    else:
        count = len(data.get("events", []))
    return max(0, count)


def _draw_panel(stdscr, top, width, rows, title, lines, focused, offset, message):
    try:
        height, _ = stdscr.getmaxyx()
        if top >= height - 1:
            return
        available = max(0, rows - 2)
        end = top + rows
        if end > height - 1:
            end = height - 1
            available = max(0, end - top - 2)
        attr = curses.A_BOLD if focused else curses.color_pair(4)
        title_text = f" {title} "
        stdscr.addnstr(top, 0, f"─{title_text}{'─' * max(0, width - len(title_text) - 2)}", width - 1)
        if message and focused:
            message_text = f" ({message})"
            stdscr.addnstr(top, 0, message_text[: width - 1], width - 1, curses.A_DIM)
        for row in range(available):
            item = lines[offset + row] if (offset + row) < len(lines) else None
            if item is None:
                text, attr = " ", 0
            elif isinstance(item, tuple):
                text, attr = item
                attr = attr or 0
            else:
                text, attr = item, 0
            text = str(text)[: max(0, width - 1)]
            if attr:
                stdscr.addnstr(top + 1 + row, 0, text, len(text), attr)
            else:
                stdscr.addnstr(top + 1 + row, 0, text, len(text))
    except curses.error:
        pass


def fmt_task_time(value):
    """Format either an integer epoch or an ISO text timestamp compactly."""
    if value is None:
        return "?"
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M")
        except Exception:
            return str(value)
    return fmt_time(str(value))[:16]


def _tasks_lines(data, color_map):
    tasks = (data.get("tasks", {}) or {})
    rows = tasks.get("rows", [])
    lines = []
    for task_id, title, status, role, updated_at in rows:
        queue = STATUS_QUEUE.get(status, status.upper())
        tag = f"[{queue}]"
        line = f"{task_id:22s} {tag:9s} {fmt_task_time(updated_at):8s} {title}"
        if role:
            line += f"  ({role})"
        lines.append((line, _status_color(status, color_map)))
    if not rows and tasks.get("error"):
        lines.append((f"tasks unavailable: {tasks['error']}", 0))
    elif not rows:
        lines.append(("no live tasks", 0))
    return lines


def _sessions_lines(data):
    lines = []
    for session in data.get("sessions", []):
        cwd = session.get("cwd") or "/"
        worktree = f" wt={session['worktree']}" if session.get("worktree") else ""
        pin = ""
        if session.get("provider"):
            pin = f" [{session['provider']}/{session.get('model', '')}]"
        lines.append(
            f"{session['name']:26s} {fmt_time(session.get('last_active')):17s} "
            f"{session.get('shell')} · {cwd}{worktree}{pin}"
        )
    if not lines:
        lines.append("no saved sessions")
    return lines


def _confirm_and_open(stdscr, session):
    pin = ""
    if session.get("provider") or session.get("model"):
        pin = f" (provider={session.get('provider')} model={session.get('model')})"
    prompt = (
        f"Open {session['name']} in {session.get('shell', '/bin/bash')} at "
        f"{session.get('cwd') or '/'}{pin}? [y/N] "
    )
    try:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, prompt[: width - 1], width - 1)
        stdscr.refresh()
        curses.curs_set(1)
        key = stdscr.getch()
        curses.curs_set(0)
    except curses.error:
        return
    if key in (ord("y"), ord("Y")):
        try:
            curses.endwin()
        except Exception:
            pass
        launch_session_record(session)