"""Fleet Integration Layer for terminal-launcher.

Provides SwarmBusPublisher, SwarmAssembleLiaison, FleetState, session persistence
with env filtering, and worktree isolation.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

SWARM_BUS = Path("bin/swarm-bus")
SWARM_ASSEMBLE = Path("bin/swarm-assemble")
SESSIONS_DIR = Path("data/terminal-sessions")
AGENT_NAME = "TERM-LAUNCHER-01"

_swarm_logger = logging.getLogger("terminal-launcher.swarm")


SAFE_ENV_VARS = frozenset({
    "PATH", "HOME", "TERM", "LANG", "EDITOR", "SHELL", "USER",
    "LOGNAME", "PAGER", "COLORTERM", "LS_COLORS", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
})

_SECRET_ENV_PATTERNS = (
    "AWS_", "SECRET_", "TOKEN_", "PASSWORD_", "SSH_", "GPG_",
    "GNUPGHOME", "COOKIE", "SESSION", "LP_",
    "ANTHROPIC_", "OPENAI_", "GOOGLE_", "SLACK_", "AZURE_",
)


class SwarmBusPublisher:
    """Wraps swarm-bus put/get/state with structured logging."""

    def __init__(self, agent_name: str = AGENT_NAME):
        self.agent_name = agent_name

    def put(self, key: str, value: str) -> bool:
        try:
            result = subprocess.run(
                [str(SWARM_BUS), "put", self.agent_name, key, value],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                _swarm_logger.debug("swarm-bus put: %s=%s", key, value)
                return True
            _swarm_logger.warning(
                "swarm-bus put failed: %s=%s, rc=%s, stderr=%s",
                key, value, result.returncode, result.stderr.decode().strip(),
            )
            return False
        except subprocess.TimeoutExpired:
            _swarm_logger.warning("swarm-bus put timeout: %s", key)
            return False
        except FileNotFoundError:
            _swarm_logger.error("swarm-bus not found at %s", SWARM_BUS)
            return False
        except Exception as exc:
            _swarm_logger.warning("swarm-bus put error: %s=%s, exc=%s", key, value, exc)
            return False

    def get(self, key: str) -> Optional[str]:
        try:
            result = subprocess.run(
                [str(SWARM_BUS), "get", key],
                capture_output=True, timeout=5, text=True,
            )
            if result.returncode == 0:
                value = result.stdout.strip()
                _swarm_logger.debug("swarm-bus get: %s=%s", key, value)
                return value
            _swarm_logger.debug("swarm-bus get miss: %s (rc=%s)", key, result.returncode)
            return None
        except subprocess.TimeoutExpired:
            _swarm_logger.warning("swarm-bus get timeout: %s", key)
            return None
        except FileNotFoundError:
            _swarm_logger.error("swarm-bus not found at %s", SWARM_BUS)
            return None
        except Exception as exc:
            _swarm_logger.warning("swarm-bus get error: %s, exc=%s", key, exc)
            return None

    def state(self, state: str) -> bool:
        try:
            result = subprocess.run(
                [str(SWARM_BUS), "state", self.agent_name, state],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                _swarm_logger.debug("swarm-bus state: %s", state)
                return True
            _swarm_logger.warning(
                "swarm-bus state failed: %s, rc=%s",
                state, result.returncode,
            )
            return False
        except subprocess.TimeoutExpired:
            _swarm_logger.warning("swarm-bus state timeout: %s", state)
            return False
        except FileNotFoundError:
            _swarm_logger.error("swarm-bus not found at %s", SWARM_BUS)
            return False
        except Exception as exc:
            _swarm_logger.warning("swarm-bus state error: %s, exc=%s", state, exc)
            return False


class SwarmAssembleLiaison:
    """Wraps swarm-assemble allocate/free with fallback to HOME."""

    def __init__(self):
        self._lock = threading.Lock()

    def allocate(self, session_name: str) -> Optional[str]:
        with self._lock:
            try:
                result = subprocess.run(
                    [str(SWARM_ASSEMBLE), "allocate", session_name],
                    capture_output=True, timeout=10, text=True,
                )
                if result.returncode == 0:
                    worktree = result.stdout.strip()
                    if worktree:
                        _swarm_logger.debug("worktree allocated: %s -> %s", session_name, worktree)
                        return worktree
            except subprocess.TimeoutExpired:
                _swarm_logger.warning("swarm-assemble allocate timeout: %s", session_name)
            except FileNotFoundError:
                _swarm_logger.error("swarm-assemble not found at %s", SWARM_ASSEMBLE)
            except Exception as exc:
                _swarm_logger.warning("swarm-assemble allocate error: %s, exc=%s", session_name, exc)

        fallback = os.environ.get("HOME")
        if fallback:
            _swarm_logger.info("worktree allocation failed for %s, falling back to HOME=%s", session_name, fallback)
            return fallback
        return None

    def free(self, session_name: str) -> bool:
        with self._lock:
            try:
                result = subprocess.run(
                    [str(SWARM_ASSEMBLE), "free", session_name],
                    capture_output=True, timeout=10,
                )
                if result.returncode == 0:
                    _swarm_logger.debug("worktree freed: %s", session_name)
                    return True
                _swarm_logger.warning(
                    "swarm-assemble free failed: %s, rc=%s",
                    session_name, result.returncode,
                )
                return False
            except subprocess.TimeoutExpired:
                _swarm_logger.warning("swarm-assemble free timeout: %s", session_name)
                return False
            except FileNotFoundError:
                _swarm_logger.error("swarm-assemble not found at %s", SWARM_ASSEMBLE)
                return False
            except Exception as exc:
                _swarm_logger.warning("swarm-assemble free error: %s, exc=%s", session_name, exc)
                return False


class FleetState:
    """Reads swarm-bus keys for fleet awareness (other agents' states)."""

    def __init__(self, publisher: Optional[SwarmBusPublisher] = None):
        self._publisher = publisher or SwarmBusPublisher()

    def dump(self) -> dict[str, str]:
        try:
            result = subprocess.run(
                [str(SWARM_BUS), "dump"],
                capture_output=True, timeout=5, text=True,
            )
            if result.returncode == 0:
                state = {}
                for line in result.stdout.splitlines():
                    if ":" in line:
                        parts = line.split(":", 1)
                        state[parts[0]] = parts[1].strip()
                _swarm_logger.debug("fleet state dump: %d keys", len(state))
                return state
        except Exception as exc:
            _swarm_logger.warning("fleet state dump error: %s", exc)
        return {}

    def get_agent_state(self, agent_name: str) -> Optional[str]:
        return self._publisher.get(f"agent.{agent_name}.state")

    def get_term_launcher_sessions(self) -> list[dict]:
        fleet_raw = self._publisher.get("term-launcher.fleet")
        if fleet_raw:
            try:
                data = json.loads(fleet_raw)
                return data.get("sessions", [])
            except json.JSONDecodeError:
                _swarm_logger.warning("invalid fleet JSON: %s", fleet_raw)
        return []

    def get_session_count(self) -> int:
        count_raw = self._publisher.get("term-launcher.session_count")
        if count_raw:
            try:
                return int(count_raw)
            except ValueError:
                pass
        return 0


def filter_env_for_save(env: dict[str, str]) -> dict[str, str]:
    """Filter environment dict to strip secrets before saving session JSON."""
    filtered = {}
    for key, value in env.items():
        if key in SAFE_ENV_VARS:
            filtered[key] = value
        elif not any(secret in key.upper() for secret in _SECRET_ENV_PATTERNS):
            filtered[key] = value
    return filtered


def ensure_sessions_dir() -> Path:
    path = SESSIONS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def serialize_session(
    name: str,
    created_at: str,
    last_active: str,
    cwd: str,
    env: dict[str, str],
    shell: str,
    worktree: Optional[str],
    tags: list[str],
) -> dict:
    """Serialize session data to a dict suitable for JSON serialization."""
    return {
        "name": name,
        "created_at": created_at,
        "last_active": last_active,
        "cwd": cwd,
        "env": filter_env_for_save(env),
        "shell": shell,
        "worktree": worktree,
        "tags": list(tags),
    }


def persist_session(
    name: str,
    created_at: str,
    last_active: str,
    cwd: str,
    env: dict[str, str],
    shell: str,
    worktree: Optional[str],
    tags: list[str],
) -> Path:
    """Save a session to disk with filtered env."""
    ensure_sessions_dir()
    path = SESSIONS_DIR / f"{name}.json"
    data = serialize_session(
        name=name,
        created_at=created_at,
        last_active=last_active,
        cwd=cwd,
        env=env,
        shell=shell,
        worktree=worktree,
        tags=tags,
    )
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    _swarm_logger.debug("session persisted: %s -> %s", name, path)
    return path


_publisher: Optional[SwarmBusPublisher] = None
_assemble_liaison: Optional[SwarmAssembleLiaison] = None
_fleet_state: Optional[FleetState] = None
_init_lock = threading.Lock()


def get_publisher() -> SwarmBusPublisher:
    global _publisher
    if _publisher is None:
        with _init_lock:
            if _publisher is None:
                _publisher = SwarmBusPublisher()
    return _publisher


def get_assemble_liaison() -> SwarmAssembleLiaison:
    global _assemble_liaison
    if _assemble_liaison is None:
        with _init_lock:
            if _assemble_liaison is None:
                _assemble_liaison = SwarmAssembleLiaison()
    return _assemble_liaison


def get_fleet_state() -> FleetState:
    global _fleet_state
    if _fleet_state is None:
        with _init_lock:
            if _fleet_state is None:
                _fleet_state = FleetState(get_publisher())
    return _fleet_state
