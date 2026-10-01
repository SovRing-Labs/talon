import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class OracleLaneWatchPlugin(TerminalPlugin):
    name = "oracle-lane-watch"
    version = "0.1.0"
    load_priority = 40

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        state = ctx.get_fleet_state()
        heat = {}
        for key, value in state.items():
            if key.startswith("lane."):
                heat[key] = value
        ctx.publish("oracle.lanes.heat", json.dumps(heat))

    def register_commands(self, registry: CommandRegistry) -> None:
        pass

    def _publish_lane_state(self, ctx: PluginContext) -> None:
        state = ctx.get_fleet_state()
        for key in ["dispatcherd.queue_depth", "dispatcherd.active_lanes", "lane.*"]:
            for k, v in state.items():
                if k.startswith("dispatcherd.") or k.startswith("lane."):
                    ctx.publish(f"oracle.lanes.heat", json.dumps({k: v}))
