"""Tool surface bridge for the Talon Agent Engine.

Rewritten 2026-09-26:
  - every mutation (bash, write, edit) goes through Omnitool (bin/fleet-exec): preflight,
    intent/safety classification, write-scope refusal, telemetry. edit = read + replace +
    fleet-exec write, so edits get the same write scope as writes.
  - read-only helpers (read, glob, grep, fleet_kb_search) run natively for speed.
  - the tool list is filtered per role from the role's opencode tools map, so a researcher
    gets no fleet_exec, exactly like under opencode.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("talon.engine.tools")

AI_STACK_ROOT = Path(os.environ.get("AI_STACK_ROOT", "."))
# Omnitool (github.com/SovRing-Labs/omnitool) executes every mutation; point FLEET_EXEC at its bin/fleet-exec when it
# is installed outside AI_STACK_ROOT.
FLEET_EXEC = Path(os.environ.get("FLEET_EXEC", str(AI_STACK_ROOT / "bin" / "fleet-exec")))
FLEET_KB = Path(os.environ.get("FLEET_KB", str(AI_STACK_ROOT / "bin" / "fleet-kb")))
MAX_OUTPUT = int(os.environ.get("TALON_TOOL_MAX_CHARS", "20000"))


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


TOOLS: dict[str, dict] = {
    "bash": _fn("bash", "Run a bash command in the workspace (via Omnitool). Returns stdout, stderr, exit_code.",
                {"command": {"type": "string"},
                 "timeout_s": {"type": "number", "description": "seconds, default 120, max 900"}}, ["command"]),
    "read": _fn("read", "Read a text file. Use offset/limit (lines) for large files.",
                {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
    "write": _fn("write", "Create or overwrite a file with the given content (via Omnitool write scope).",
                 {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "edit": _fn("edit", "Replace exactly one occurrence of old_string with new_string in a file.",
                {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}},
                ["path", "old_string", "new_string"]),
    "glob": _fn("glob", "Find files by glob pattern (e.g. '**/*.py') under path.",
                {"pattern": {"type": "string"}, "path": {"type": "string"}}, ["pattern"]),
    "grep": _fn("grep", "Search file contents with a regex (ripgrep). Optional glob filter.",
                {"pattern": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}}, ["pattern"]),
    "fleet_exec": _fn("fleet_exec", "Batch several bash/read/write/patch/sql operations in one Omnitool call.",
                      {"operations": {"type": "array", "items": {"type": "object"},
                                      "description": "[{'type':'bash','command':...},{'type':'read','path':...}]"}},
                      ["operations"]),
    "fleet_kb_search": _fn("fleet_kb_search", "Full-text search over the fleet's system manuals, devlogs and reviews.",
                           {"query": {"type": "string"}}, ["query"]),
}
BASE = ["bash", "read", "write", "edit", "glob", "grep"]
# opencode tools-map key -> Talon tool
OPENCODE_KEYS = {"bash": "bash", "read": "read", "write": "write", "edit": "edit", "glob": "glob", "grep": "grep",
                 "sovereign-kernel_fleet_exec": "fleet_exec", "sovereign-kernel_fleet_kb_search": "fleet_kb_search"}


def tools_for(tools_map: Optional[dict]) -> list[str]:
    """Talon tool names allowed by an opencode tools map. Empty map = opencode default (all)."""
    if not tools_map:
        return BASE + ["fleet_exec", "fleet_kb_search"]
    names = [t for t in BASE if tools_map.get(t, True)]
    for key, t in OPENCODE_KEYS.items():
        if key.startswith("sovereign-kernel_") and tools_map.get(key) is True:
            names.append(t)
    return names


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT // 2] + f"\n… [{len(s) - MAX_OUTPUT} chars clipped] …\n" + s[-MAX_OUTPUT // 2:]


class OmnitoolBridge:
    """Executes tool calls: mutations through bin/fleet-exec, reads natively."""

    def __init__(self, workspace_root: Path = AI_STACK_ROOT, allowed: Optional[list[str]] = None,
                 env_extra: Optional[dict] = None):
        self.workspace_root = Path(workspace_root)
        self.allowed = allowed
        # identity for Omnitool's field marks (TALON_SESSION / TALON_TASK_ID / TALON_ROLE); per bridge,
        # not process env, so concurrent `talon serve` sessions each mark as themselves
        self.env_extra = {k: v for k, v in (env_extra or {}).items() if v}
        # FLEET-LOOP-RAPTOR S4: cores fired from this session stamp it on their receipts (engine/ledger.py reads them back)
        if self.env_extra.get("TALON_SESSION") and "CORE_CALLER" not in self.env_extra:
            self.env_extra["CORE_CALLER"] = f"talon:{self.env_extra['TALON_SESSION']}"

    def get_tool_definitions(self, role: str = "dev", tools_map: Optional[dict] = None) -> list[dict[str, Any]]:
        names = self.allowed or tools_for(tools_map)
        return [TOOLS[n] for n in names if n in TOOLS]

    def _path(self, p: str) -> Path:
        q = Path(os.path.expanduser(p or ""))
        return q if q.is_absolute() else self.workspace_root / q

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.allowed is not None and name not in self.allowed:
            return {"ok": False, "error": f"tool '{name}' is not available to this role"}
        a = arguments or {}
        try:
            if name == "bash":
                t = max(1.0, min(float(a.get("timeout_s") or 120), 900.0))
                return self._fleet([{"type": "bash", "command": a.get("command", ""),
                                     "cwd": str(self.workspace_root), "timeout_s": t}], timeout=t + 30)
            if name == "read":
                return self._read(a)
            if name == "write":
                return self._fleet([{"type": "write", "path": str(self._path(a.get("path", ""))),
                                     "content": a.get("content", "")}])
            if name == "edit":
                return self._edit(a)
            if name == "glob":
                return self._glob(a)
            if name == "grep":
                return self._grep(a)
            if name == "fleet_exec":
                return self._fleet(a.get("operations") or [], timeout=900)
            if name == "fleet_kb_search":
                r = subprocess.run([str(FLEET_KB), "search", str(a.get("query", ""))[:200]],
                                   capture_output=True, text=True, timeout=30)
                return {"ok": r.returncode == 0, "results": _clip(r.stdout)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        return {"ok": False, "error": f"Unknown tool: '{name}'"}

    def _fleet_via_daemon(self, sock_path: str, operations: list[dict[str, Any]], timeout: float):
        import socket as _socket
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM); s.settimeout(timeout); s.connect(sock_path)
        req = {"argv": [json.dumps({"operations": operations})], "cwd": str(self.workspace_root), "env": {**os.environ, **self.env_extra}}
        s.sendall((json.dumps(req) + "\n").encode()); buf = b""
        while (k := s.recv(65536)):
            buf += k
        s.close()
        r = json.loads(buf)
        return r["stdout"], r["stderr"], r["code"]

    def _fleet(self, operations: list[dict[str, Any]], timeout: float = 150) -> dict[str, Any]:
        sock = os.environ.get("FLEET_EXEC_SOCK")
        got = None
        if sock and os.path.exists(sock):                 # IPC-1: resident fleet-execd fork server (identical output)
            try:
                got = self._fleet_via_daemon(sock, operations, timeout)
            except OSError:
                got = None                                # daemon gone: fall back to the subprocess below
        if got is None:
            if not FLEET_EXEC.exists():
                return {"ok": False, "error": f"fleet-exec not found at {FLEET_EXEC}"}
            try:
                res = subprocess.run([str(FLEET_EXEC), json.dumps({"operations": operations})],
                                     capture_output=True, text=True, cwd=str(self.workspace_root), timeout=timeout,
                                     env={**os.environ, **self.env_extra})
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"Omnitool call timed out after {timeout:.0f}s"}
            got = (res.stdout, res.stderr, res.returncode)
        stdout, stderr, code = got
        try:
            data = json.loads(stdout)
        except Exception:
            return {"ok": code == 0, "stdout": _clip(stdout), "stderr": _clip(stderr), "exit_code": code}
        if not data.get("ok", False) and data.get("errs"):
            return {"ok": False, "error": "; ".join(data["errs"])}
        results = data.get("results", [])
        out = results[0] if len(operations) == 1 and results else data
        for k in ("stdout", "stderr", "content"):
            if isinstance(out.get(k), str):
                out[k] = _clip(out[k])
        for k in ("_key_hash", "_hash_prefix", "_capsule", "_intent", "duration_ms"):
            out.pop(k, None)
        return out

    def _read(self, a: dict[str, Any]) -> dict[str, Any]:
        p = self._path(a.get("path", ""))
        if not p.is_file():
            return {"ok": False, "error": f"not a file: {p}"}
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        off = max(0, int(a.get("offset") or 0))
        lim = int(a.get("limit") or 2000)
        chunk = lines[off:off + lim]
        return {"ok": True, "path": str(p), "total_lines": len(lines), "offset": off,
                "content": _clip("\n".join(chunk))}

    def _edit(self, a: dict[str, Any]) -> dict[str, Any]:
        p = self._path(a.get("path", ""))
        if not p.is_file():
            return {"ok": False, "error": f"File does not exist: {p}"}
        old, new = a.get("old_string", ""), a.get("new_string", "")
        text = p.read_text(encoding="utf-8")
        n = text.count(old) if old else 0
        if n != 1:
            return {"ok": False, "error": f"old_string found {n} times in {p} (need exactly 1)"}
        r = self._fleet([{"type": "write", "path": str(p), "content": text.replace(old, new, 1)}])
        if r.get("ok"):
            r["status"] = "edited"
        return r

    def _glob(self, a: dict[str, Any]) -> dict[str, Any]:
        base = self._path(a.get("path") or ".")
        if not base.is_dir():
            return {"ok": False, "error": f"Directory not found: {base}"}
        files = []
        for f in base.glob(a.get("pattern") or "*"):
            if f.is_file() and not any(part in (".git", "node_modules", "__pycache__") for part in f.parts):
                files.append(str(f))
                if len(files) >= 200:
                    break
        return {"ok": True, "count": len(files), "files": files}

    def _grep(self, a: dict[str, Any]) -> dict[str, Any]:
        cmd = ["rg", "-n", "--max-count", "50", "--max-columns", "300", a.get("pattern", ""),
               str(self._path(a.get("path") or "."))]
        if a.get("glob"):
            cmd += ["-g", a["glob"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            cmd = ["grep", "-rn", "-m", "50", a.get("pattern", ""), str(self._path(a.get("path") or "."))]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        lines = r.stdout.splitlines()
        return {"ok": r.returncode in (0, 1), "count": len(lines), "matches": lines[:300]}
