import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import sqlite3
import time
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DISPATCH_DB = Path("control-center/dispatch.db")
DISPATCH_LEDGER = Path("control-center/dispatch-ledger.jsonl")


class FleetCommanderPlugin(TerminalPlugin):
    name = "fleet-commander"
    version = "0.1.0"
    load_priority = 20

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        sessions = self._get_sessions(ctx)
        ctx.publish("commander.fleet.sessions", json.dumps(sessions))
        ctx.publish("commander.fleet.heartbeat", json.dumps({"ts": self._now()}))

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        sessions = self._get_sessions(ctx)
        ctx.publish("commander.fleet.sessions", json.dumps(sessions))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_fleet_status", self._cmd_fleet_status)
        registry.register("commander_dispatcherd_query", self._cmd_dispatcherd_query)
        registry.register("commander_session_tail", self._cmd_session_tail)
        registry.register("commander_wait_on", self._cmd_wait_on)
        registry.register("commander_auto_loop", self._cmd_auto_loop)

    def _get_sessions(self, ctx: PluginContext) -> list[dict]:
        sessions = []
        if ctx.launcher:
            for name, sess in ctx.launcher.sessions.items():
                sessions.append({
                    "name": name,
                    "cwd": sess.cwd,
                    "active": not sess.is_background,
                    "last_active": sess.last_active,
                })
        return sessions

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _cmd_fleet_status(self, args: list[str]) -> None:
        if not DISPATCH_DB.exists():
            print("dispatch.db not found")
            return
        try:
            conn = sqlite3.connect(DISPATCH_DB)
            cursor = conn.execute(
                "SELECT task_id, state, model, datetime(dispatched_at, 'unixepoch') "
                "FROM dispatches ORDER BY dispatched_at DESC LIMIT 20"
            )
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                print("No dispatches found in dispatch.db")
            for row in rows:
                print(f"{row[0]} | {row[1]} | {row[2]} | {row[3]}")
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_dispatcherd_query(self, args: list[str]) -> None:
        state = {}
        if DISPATCH_DB.exists():
            try:
                conn = sqlite3.connect(DISPATCH_DB)
                cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'approved'")
                state["approved"] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM dispatches WHERE state = 'dispatched'")
                state["running"] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'needs_review'")
                state["needs_review"] = cursor.fetchone()[0]
                cursor = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'")
                state["done"] = cursor.fetchone()[0]
                conn.close()
            except Exception as e:
                state["error"] = str(e)
        print(json.dumps(state))

    def _cmd_session_tail(self, args: list[str]) -> None:
        session_name = args[0] if args else ""
        if ctx := getattr(self, "_ctx", None):
            if ctx.launcher and session_name in ctx.launcher.sessions:
                sess = ctx.launcher.sessions[session_name]
                buf = sess.output_buffer.get() if hasattr(sess.output_buffer, "get") else sess.output_buffer
                for line in buf[-20:]:
                    print(line.decode(errors="replace") if isinstance(line, bytes) else str(line))

    def _cmd_wait_on(self, args: list[str]) -> None:
        """Wait for task(s), swarm, or idle quiescence without burning LLM tokens.
        
        Usage:
          commander_wait_on <task_id|prefix|all|idle> [timeout_seconds]
        """
        if not args:
            print("Usage: commander_wait_on <task_id|prefix|all|idle> [timeout_s]")
            return
        target = args[0]
        timeout_s = float(args[1]) if len(args) > 1 else 300.0
        start = time.time()
        deadline = start + timeout_s

        print(f"⚡ Commander going dormant waiting on '{target}' (timeout: {int(timeout_s)}s)...")
        if ctx := getattr(self, "_ctx", None):
            ctx.publish("commander.state.mode", f"WAITING_ON:{target}")

        while time.time() < deadline:
            time.sleep(2.0)
            if not DISPATCH_DB.exists():
                continue
            try:
                conn = sqlite3.connect(f"file:{DISPATCH_DB}?mode=ro", uri=True)
                if target in ("all", "idle"):
                    active = conn.execute("SELECT COUNT(*) FROM dispatches WHERE state='dispatched'").fetchone()[0]
                    conn.close()
                    if active == 0:
                        dur = int(time.time() - start)
                        print(f"✓ Quiescence reached: 0 in-flight dispatches remaining ({dur}s).")
                        if ctx := getattr(self, "_ctx", None):
                            ctx.publish("commander.state.mode", "DORMANT")
                        return
                elif "-" in target and len(target) <= 10 and not any(target.endswith(s) for s in ("-A", "-B", "-C", "-D", "-1", "-2", "-3")):
                    # Swarm prefix wait (e.g. GEN0-2, SOV-CAW)
                    rows = conn.execute("SELECT id, status FROM tasks WHERE id LIKE ?", (f"{target}%",)).fetchall()
                    conn.close()
                    if rows and all(r[1] in ("done", "retired", "abandoned") for r in rows):
                        dur = int(time.time() - start)
                        summary = ", ".join(f"{r[0]}:{r[1]}" for r in rows)
                        print(f"✓ Swarm wave '{target}' settled in {dur}s ({summary}).")
                        if ctx := getattr(self, "_ctx", None):
                            ctx.publish("commander.state.mode", "WORKING")
                        return
                else:
                    # Single task wait
                    row = conn.execute("SELECT status FROM tasks WHERE id=?", (target,)).fetchone()
                    conn.close()
                    if row and row[0] in ("done", "needs_review", "blocked", "retired", "abandoned"):
                        dur = int(time.time() - start)
                        print(f"✓ Task '{target}' settled to '{row[0]}' in {dur}s.")
                        if ctx := getattr(self, "_ctx", None):
                            ctx.publish("commander.state.mode", "WORKING")
                        return
            except Exception:
                pass

        print(f"⚠ Timeout reached ({int(timeout_s)}s) waiting on '{target}'.")
        if ctx := getattr(self, "_ctx", None):
            ctx.publish("commander.state.mode", "TIMEOUT")

    def _cmd_auto_loop(self, args: list[str]) -> None:
        """Toggle autonomous dormant loop / auto-wake mode anytime.
        
        Usage:
          commander_auto_loop <enable|disable|status>
        """
        action = args[0].lower() if args else "status"
        state_file = Path("control-center/commander-state.json")
        data = {}
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        
        now = time.time()
        if action in ("enable", "on", "1", "true"):
            data["enabled"] = True
            data["mode"] = "WORKING"
            data["set_by"] = "commander"
            data["ts"] = now
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("✓ Autonomous Commander Dormant Loop: ENABLED (running)")
            if ctx := getattr(self, "_ctx", None):
                ctx.publish("commander.autonomous.enabled", "true")
        elif action in ("disable", "off", "0", "false"):
            data["enabled"] = False
            data["mode"] = "DORMANT"
            data["set_by"] = "commander"
            data["ts"] = now
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("✓ Autonomous Commander Dormant Loop: DISABLED (dormant/quiet)")
            if ctx := getattr(self, "_ctx", None):
                ctx.publish("commander.autonomous.enabled", "false")
        else:
            status = "ENABLED" if data.get("enabled") else "DISABLED"
            mode = data.get("mode", "UNKNOWN")
            print(f"Autonomous Commander Loop: {status} (Mode: {mode}, set_by: {data.get('set_by')})")
