import sys
import os
import sqlite3
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DB_PATH = Path("control-center/blackboard.db")


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blackboard (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            author TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    return conn


class StigmergyBlackboardPlugin(TerminalPlugin):
    name = "stigmergy-blackboard"
    version = "0.1.0"
    load_priority = 30

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        try:
            conn = _get_db()
            count = conn.execute("SELECT count(*) c FROM blackboard").fetchone()["c"]
            ctx.publish("blackboard.items_count", str(count))
            conn.close()
        except Exception:
            pass

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("blackboard_post", self._cmd_post)
        registry.register("blackboard_get", self._cmd_get)
        registry.register("blackboard_list", self._cmd_list)

    def _cmd_post(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: blackboard_post <key> <value>")
            return
        key = args[0]
        value = " ".join(args[1:])
        author = os.environ.get("USER", "agent")
        now = int(time.time())
        try:
            conn = _get_db()
            conn.execute("""
                INSERT INTO blackboard(key, value, author, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, author=excluded.author, updated_at=excluded.updated_at
            """, (key, value, author, now, now))
            conn.commit()
            conn.close()
            print(f"\033[92m[✓] Blackboard posted: '{key}'\033[0m")
        except Exception as e:
            print(f"\033[91m[!] Blackboard post error: {e}\033[0m")

    def _cmd_get(self, args: list[str]) -> None:
        if not args:
            print("Usage: blackboard_get <key>")
            return
        key = args[0]
        try:
            conn = _get_db()
            row = conn.execute("SELECT * FROM blackboard WHERE key=?", (key,)).fetchone()
            conn.close()
            if row:
                print(f"[{row['key']}] ({row['author']} @ {row['updated_at']}):\n{row['value']}")
            else:
                print(f"Key '{key}' not found on blackboard.")
        except Exception as e:
            print(f"Error reading blackboard: {e}")

    def _cmd_list(self, args: list[str]) -> None:
        try:
            conn = _get_db()
            rows = conn.execute("SELECT key, author, updated_at, substr(value, 1, 60) preview FROM blackboard ORDER BY updated_at DESC LIMIT 20").fetchall()
            conn.close()
            if not rows:
                print("Blackboard is empty.")
                return
            print("\033[96;1m=== STIGMERGIC BLACKBOARD ===\033[0m")
            for r in rows:
                print(f"  \033[1m{r['key']}\033[0m ({r['author']}): {r['preview']}...")
        except Exception as e:
            print(f"Error querying blackboard: {e}")


if __name__ == "__main__":
    plugin = StigmergyBlackboardPlugin()
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd == "post":
        plugin._cmd_post(args[1:])
    elif cmd == "get":
        plugin._cmd_get(args[1:])
    else:
        plugin._cmd_list(args[1:])
