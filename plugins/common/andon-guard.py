import sys
import os
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

ANDON_STATE_FILE = Path("/tmp/talon-andon.json")


def _read_andon() -> dict:
    if ANDON_STATE_FILE.exists():
        try:
            return json.loads(ANDON_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"state": "clear", "reason": "", "tripped_by": "", "timestamp": 0}


def _write_andon(state: str, reason: str = "", by: str = "") -> None:
    import time
    data = {
        "state": state,
        "reason": reason,
        "tripped_by": by,
        "timestamp": int(time.time()),
    }
    try:
        ANDON_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class AndonGuardPlugin(TerminalPlugin):
    name = "andon-guard"
    version = "0.1.0"
    load_priority = 15

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        andon = _read_andon()
        ctx.publish("andon.state", andon.get("state", "clear"))
        ctx.publish("andon.reason", andon.get("reason", ""))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("andon_status", self._cmd_status)
        registry.register("andon_trip", self._cmd_trip)
        registry.register("andon_clear", self._cmd_clear)

    def _cmd_status(self, args: list[str]) -> None:
        andon = _read_andon()
        state = andon.get("state", "clear")
        if state == "tripped":
            print(f"\033[91;1m[ANDON TRIPPED — LINE HALTED]\033[0m")
            print(f"Reason: {andon.get('reason', 'Unspecified defect')}")
            print(f"Tripped By: {andon.get('tripped_by', 'system')}")
        else:
            print("\033[92m[ANDON CLEAR]\033[0m Production line operating normally.")

    def _cmd_trip(self, args: list[str]) -> None:
        reason = " ".join(args) if args else "Defect detected in test or compiler gate"
        _write_andon("tripped", reason, os.environ.get("USER", "agent"))
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("andon.state", "tripped")
            ctx.publish("andon.reason", reason)
        sys.stdout.write("\x07")
        sys.stdout.flush()
        print(f"\033[91;1m[!] ANDON CORD PULLED: {reason}\033[0m")
        print("Stop-the-line protocol active. Fix defect before resuming.")

    def _cmd_clear(self, args: list[str]) -> None:
        _write_andon("clear")
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("andon.state", "clear")
            ctx.publish("andon.reason", "")
        print("\033[92;1m[✓] ANDON CLEARED: Production line resumed.\033[0m")


if __name__ == "__main__":
    plugin = AndonGuardPlugin()
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "trip":
        plugin._cmd_trip(args[1:])
    elif cmd == "clear":
        plugin._cmd_clear(args[1:])
    else:
        plugin._cmd_status(args[1:])
