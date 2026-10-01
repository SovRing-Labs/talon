import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import sqlite3
import subprocess
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

CODE_INDEX_DB = Path.home() / ".local" / "share" / "terminal-launcher" / "code-index.sqlite"


class ResearchCodeIndexPlugin(TerminalPlugin):
    name = "research-code-index"
    version = "0.1.0"
    load_priority = 10

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("researcher.index.status", "ready")
        ctx.publish("researcher.index.count", str(self._count_entries()))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_grep_code", self._cmd_grep_code)

    def _ensure_db(self) -> None:
        CODE_INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(CODE_INDEX_DB)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS code_index "
            "(path TEXT, content TEXT, lineno INTEGER)"
        )
        conn.commit()
        conn.close()

    def _count_entries(self) -> int:
        if not CODE_INDEX_DB.exists():
            return 0
        try:
            conn = sqlite3.connect(CODE_INDEX_DB)
            cursor = conn.execute("SELECT COUNT(*) FROM code_index")
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _cmd_grep_code(self, args: list[str]) -> None:
        if not args:
            print("Usage: researcher_grep_code <pattern>")
            return
        pattern = args[0]
        if not CODE_INDEX_DB.exists():
            print("Code index not initialized")
            return
        try:
            conn = sqlite3.connect(CODE_INDEX_DB)
            cursor = conn.execute(
                "SELECT path, lineno, content FROM code_index WHERE content LIKE ?",
                (f"%{pattern}%",)
            )
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                print(f"No matches for: {pattern}")
            for path, lineno, content in rows[:20]:
                print(f"{path}:{lineno}: {content[:120]}")
        except Exception as e:
            print(f"Error: {e}")
