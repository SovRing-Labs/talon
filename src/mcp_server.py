"""MCP Server for terminal-launcher.

Provides an HTTP API for managing PTY sessions, allowing external agents
and tools to interact with the terminal launcher via a RESTful interface.

Usage:
    python3 -m terminal_launcher.mcp_server
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, request, jsonify, Response

from main import (
    TerminalLauncher,
    get_launcher,
    list_sessions,
    load_session,
    delete_session,
    Session,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_logger = logging.getLogger("terminal-launcher.mcp")

app = Flask(__name__)

_launcher: Optional[TerminalLauncher] = None
_launcher_lock = threading.RLock()


def get_mcp_launcher() -> TerminalLauncher:
    global _launcher
    if _launcher is None:
        with _launcher_lock:
            if _launcher is None:
                role = os.environ.get("LAUNCHER_ROLE")
                _launcher = get_launcher(role=role)
    return _launcher


@app.route("/health", methods=["GET"])
def health() -> tuple[Response, int]:
    return jsonify({"status": "ok", "service": "terminal-launcher-mcp"}), 200


@app.route("/sessions", methods=["GET"])
def list_sessions_endpoint() -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    sessions = launcher.list_sessions()
    return jsonify({"sessions": sessions, "count": len(sessions)}), 200


@app.route("/sessions", methods=["POST"])
def create_session_endpoint() -> tuple[Response, int]:
    data = request.get_json() or {}
    name = data.get("name")
    shell = data.get("shell", "/bin/bash")
    cwd = data.get("cwd")

    if not name:
        return jsonify({"error": "name is required"}), 400

    launcher = get_mcp_launcher()
    try:
        session = launcher.create_session(name=name, shell=shell, cwd=cwd)
        return jsonify({
            "name": session.name,
            "shell": session.shell,
            "cwd": session.cwd,
            "created_at": session.created_at,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503


@app.route("/sessions/<name>", methods=["GET"])
def get_session_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    if name not in launcher.sessions:
        saved = load_session(name)
        if saved is None:
            return jsonify({"error": f"Session '{name}' not found"}), 404
        return jsonify({
            "name": saved.name,
            "shell": saved.shell,
            "cwd": saved.cwd,
            "created_at": saved.created_at,
            "last_active": saved.last_active,
            "is_background": saved.is_background,
        }), 200

    session = launcher.sessions[name]
    return jsonify({
        "name": session.name,
        "shell": session.shell,
        "cwd": session.cwd,
        "created_at": session.created_at,
        "last_active": session.last_active,
        "is_background": session.is_background,
    }), 200


@app.route("/sessions/<name>", methods=["DELETE"])
def delete_session_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    if name in launcher.sessions:
        launcher.close_session(name, persist=True)
        return jsonify({"message": f"Session '{name}' closed and persisted"}), 200

    if delete_session(name):
        return jsonify({"message": f"Session '{name}' deleted"}), 200

    return jsonify({"error": f"Session '{name}' not found"}), 404


@app.route("/sessions/<name>/write", methods=["POST"])
def write_session_endpoint(name: str) -> tuple[Response, int]:
    data = request.get_json() or {}
    content = data.get("content", "")

    if not content:
        return jsonify({"error": "content is required"}), 400

    launcher = get_mcp_launcher()
    try:
        launcher.write_to_session(name, content)
        return jsonify({"message": f"Wrote to session '{name}'"}), 200
    except KeyError:
        return jsonify({"error": f"Session '{name}' not found"}), 404


@app.route("/sessions/<name>/resize", methods=["POST"])
def resize_session_endpoint(name: str) -> tuple[Response, int]:
    data = request.get_json() or {}
    rows = data.get("rows")
    cols = data.get("cols")

    if rows is None or cols is None:
        return jsonify({"error": "rows and cols are required"}), 400

    launcher = get_mcp_launcher()
    try:
        launcher.resize_session(name, int(rows), int(cols))
        return jsonify({"message": f"Resized session '{name}' to {rows}x{cols}"}), 200
    except KeyError:
        return jsonify({"error": f"Session '{name}' not found"}), 404


@app.route("/sessions/<name>/switch", methods=["POST"])
def switch_session_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    if launcher.switch_session(name):
        return jsonify({"message": f"Switched to session '{name}'"}), 200
    return jsonify({"error": f"Session '{name}' not found"}), 404


@app.route("/sessions/<name>/background", methods=["POST"])
def background_session_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    if launcher.background_session(name):
        return jsonify({"message": f"Session '{name}' backgrounded"}), 200
    return jsonify({"error": f"Session '{name}' not found"}), 404


@app.route("/sessions/<name>/output", methods=["GET"])
def session_output_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    if name not in launcher.sessions:
        return jsonify({"error": f"Session '{name}' not found"}), 404

    session = launcher.sessions[name]
    buffer = session.output_buffer.get()
    output = b"".join(buffer).decode("utf-8", errors="replace")
    return jsonify({
        "name": name,
        "output": output,
        "buffer_size": len(buffer),
    }), 200


@app.route("/saved", methods=["GET"])
def list_saved_sessions_endpoint() -> tuple[Response, int]:
    sessions = list_sessions()
    return jsonify({"saved_sessions": sessions, "count": len(sessions)}), 200


@app.route("/saved/<name>", methods=["GET"])
def get_saved_session_endpoint(name: str) -> tuple[Response, int]:
    saved = load_session(name)
    if saved is None:
        return jsonify({"error": f"Saved session '{name}' not found"}), 404
    return jsonify({
        "name": saved.name,
        "shell": saved.shell,
        "cwd": saved.cwd,
        "created_at": saved.created_at,
        "last_active": saved.last_active,
    }), 200


@app.route("/saved/<name>", methods=["DELETE"])
def delete_saved_session_endpoint(name: str) -> tuple[Response, int]:
    if delete_session(name):
        return jsonify({"message": f"Saved session '{name}' deleted"}), 200
    return jsonify({"error": f"Saved session '{name}' not found"}), 404


@app.route("/saved/<name>/restore", methods=["POST"])
def restore_session_endpoint(name: str) -> tuple[Response, int]:
    launcher = get_mcp_launcher()
    session = launcher.restore_session(name)
    if session is None:
        return jsonify({"error": f"Saved session '{name}' not found"}), 404
    return jsonify({
        "name": session.name,
        "shell": session.shell,
        "cwd": session.cwd,
        "created_at": session.created_at,
    }), 200


def run_server(host: str = "127.0.0.1", port: int = 47999) -> None:
    _logger.info(f"Starting MCP server on {host}:{port}")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Terminal Launcher MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=47999, help="Port to bind to")
    args = parser.parse_args()
    run_server(args.host, args.port)
