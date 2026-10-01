import sys
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

MEMORY_FILE = Path(os.path.expanduser("~/.local/share/terminal-launcher/core-memory.json"))
FALLBACK_FILE = Path("control-center/core-memory.json")

DEFAULT_BLOCKS = {
    "MEMORY_HUMAN": (
        "- Operational Preferences: $0 paid spend; never spawn high-parameter models during heavy builds\n"
        "- Risk Threshold: Always confirm irreversible actions (financial, data deletion, remote push)\n"
        "- Hardware Limits: 16GB RAM constraint — prioritize sqlite-vec and in-process engines"
    ),
    "MEMORY_PROJECT": (
        "- Active Horizon: Living Cockpit, Talon Native Stations, Zero-Token Swarm Architecture\n"
        "- Invariants: SQLite dispatch.db authority, 4-phase swarm protocol (lock->build->gate->ratify)\n"
        "- Recent Milestone: Caw terminal creation resilience & sqlite-vec benchmark ratified"
    ),
    "MEMORY_LESSONS": (
        "- Model Slugs: Never strip provider namespaces (e.g. opencode/); CLI requires full provider paths\n"
        "- Caw Port Invariant: WebViews must proxy through :47811/caw/terminals; Caw :47820 lacks CORS OPTIONS\n"
        "- Settle Window: Always allow settle-probe to clear before checking PID state"
    )
}


def _load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if FALLBACK_FILE.exists():
        try:
            return json.loads(FALLBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(DEFAULT_BLOCKS)


def _save_memory(data: dict) -> None:
    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        try:
            FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
            FALLBACK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


class CoreMemoryPlugin(TerminalPlugin):
    name = "core-memory"
    version = "0.1.0"
    load_priority = 10

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        mem = _load_memory()
        ctx.publish("memory.human.summary", mem.get("MEMORY_HUMAN", "")[:100])
        ctx.publish("memory.project.summary", mem.get("MEMORY_PROJECT", "")[:100])
        ctx.publish("memory.lessons.count", str(len(mem.get("MEMORY_LESSONS", "").splitlines())))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("core_memory_show", self._cmd_show)
        registry.register("core_memory_update", self._cmd_update)
        registry.register("core_memory_recall", self._cmd_recall)

    def _cmd_show(self, args: list[str]) -> None:
        mem = _load_memory()
        target_block = args[0].upper() if args else None
        if target_block and target_block in mem:
            print(f"[{target_block}]\n{mem[target_block]}")
        else:
            for block, text in mem.items():
                print(f"[{block}]\n{text}\n")

    def _cmd_update(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: core_memory_update <MEMORY_HUMAN|MEMORY_PROJECT|MEMORY_LESSONS> <content>")
            return
        block = args[0].upper()
        content = " ".join(args[1:])
        mem = _load_memory()
        if block in mem:
            mem[block] = f"{mem[block]}\n- {content}"
        else:
            mem[block] = f"- {content}"
        _save_memory(mem)
        print(f"Updated [{block}] successfully.")

    def _cmd_recall(self, args: list[str]) -> None:
        if not args:
            print("Usage: core_memory_recall <query>")
            return
        query = " ".join(args).lower()
        mem = _load_memory()
        results = []
        for block, text in mem.items():
            for line in text.splitlines():
                if query in line.lower():
                    results.append(f"[{block}] {line.strip()}")
        if results:
            print("\n".join(results))
        else:
            print(f"No memories matched: '{query}'")


if __name__ == "__main__":
    plugin = CoreMemoryPlugin()
    args = sys.argv[1:]
    cmd = args[0] if args else "show"
    if cmd == "show":
        plugin._cmd_show(args[1:])
    elif cmd == "update":
        plugin._cmd_update(args[1:])
    elif cmd == "recall":
        plugin._cmd_recall(args[1:])
    else:
        plugin._cmd_show(args)
