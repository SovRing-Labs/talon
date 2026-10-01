import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import hashlib
import time
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

CACHE_TTL = 300


class CmdCachePlugin(TerminalPlugin):
    name = "cmd-cache"
    version = "0.1.0"
    load_priority = 20
    _cache: dict[str, tuple[str, float]] = {}

    def on_input(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        text = data.decode(errors="replace").strip()
        if not text:
            return data
        key = self._cache_key(text)
        now = time.time()
        if key in self._cache:
            cached_value, cached_time = self._cache[key]
            if now - cached_time < CACHE_TTL:
                ctx.publish(f"term-launcher.cmd-cache.{key[:32]}", cached_value)
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        pass

    def _cache_key(self, cmd: str) -> str:
        return hashlib.sha256(cmd.encode()).hexdigest()[:16]

    def cache_put(self, cmd: str, result: str) -> None:
        key = self._cache_key(cmd)
        self._cache[key] = (result, time.time())
