import sys
import os
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

APPROVALS_FILE = Path("control-center/approvals.jsonl")


def _read_pending_approvals() -> list[dict]:
    if not APPROVALS_FILE.exists():
        return []
    pending = []
    try:
        for line in APPROVALS_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("state") == "pending":
                pending.append(item)
    except Exception:
        pass
    return pending


class DbrBatchPlugin(TerminalPlugin):
    name = "dbr-batch"
    version = "0.1.0"
    load_priority = 25

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        items = _read_pending_approvals()
        ctx.publish("dbr.pending_count", str(len(items)))
        ctx.publish("dbr.wip_status", "congested" if len(items) > 5 else "optimal")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("dbr_queue", self._cmd_queue)
        registry.register("dbr_approve_all", self._cmd_approve_all)
        registry.register("dbr_status", self._cmd_status)

    def _cmd_status(self, args: list[str]) -> None:
        items = _read_pending_approvals()
        print(f"\033[95;1m[TOC DBR BUFFER]\033[0m Pending Approvals: {len(items)}")
        if items:
            print(f"J-Bottleneck Status: BUFFER OCCUPIED ({len(items)} items awaiting confirm)")
        else:
            print("J-Bottleneck Status: BUFFER CLEAR (Zero starvation, zero backlog)")

    def _cmd_queue(self, args: list[str]) -> None:
        items = _read_pending_approvals()
        if not items:
            print("\033[92m[✓] DBR Buffer is empty. Zero pending approvals.\033[0m")
            return
        print(f"\033[95;1m=== DBR BATCH APPROVAL QUEUE ({len(items)} items) ===\033[0m")
        for i, item in enumerate(items, 1):
            task = item.get("task") or item.get("id") or "UNKNOWN"
            action = item.get("action") or "unspecified"
            reason = item.get("reason") or "no reason"
            print(f"  [{i}] \033[1m{task}\033[0m -> {action} ({reason[:60]})")
        print("\nCommands: 'dbr_approve_all' to approve batch, or use Cockpit modal.")

    def _cmd_approve_all(self, args: list[str]) -> None:
        items = _read_pending_approvals()
        if not items:
            print("No pending items to approve.")
            return
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:47811/control-center/approvals/all",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                print(f"\033[92;1m[✓] Batch Approved {len(items)} items via sidecar :47811.\033[0m")
        except Exception as e:
            print(f"\033[91m[!] Direct sidecar approval failed: {e}\033[0m")


if __name__ == "__main__":
    plugin = DbrBatchPlugin()
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "queue":
        plugin._cmd_queue(args[1:])
    elif cmd in ("approve_all", "all"):
        plugin._cmd_approve_all(args[1:])
    else:
        plugin._cmd_status(args[1:])
