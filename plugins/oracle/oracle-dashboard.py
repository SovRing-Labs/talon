import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class OracleDashboardPlugin(TerminalPlugin):
    name = "oracle-dashboard"
    version = "0.1.0"
    load_priority = 10

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("oracle.dashboard.refresh", self._now())

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        ctx.publish("oracle.dashboard.refresh", self._now())

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_status", self._cmd_oracle_status)

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _cmd_oracle_status(self, args: list[str]) -> None:
        ctx = getattr(self, "_ctx", None)
        if not ctx:
            return
        state = ctx.get_fleet_state()
        lane_keys = [k for k in state.keys() if k.startswith("lane.")]
        disp_keys = [k for k in state.keys() if k.startswith("dispatcherd.")]
        plan_path = Path("control-center/PLAN.md")
        plan_exists = plan_path.exists()
        print(f"PLAN.md: {'exists' if plan_exists else 'MISSING'}")
        print(f"Active lanes: {len(lane_keys)}")
        print(f"Dispatcherd keys: {len(disp_keys)}")
