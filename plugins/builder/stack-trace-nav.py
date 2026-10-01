import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import re
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class StackTraceNavPlugin(TerminalPlugin):
    name = "stack-trace-nav"
    version = "0.1.0"
    load_priority = 35
    _frames: list[dict] = []
    _current_index: int = 0

    def on_output(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        text = data.decode(errors="replace")
        frames = self._parse_trace(text)
        if frames:
            self._frames = frames
            self._current_index = 0
            ctx.publish("builder.trace.count", str(len(frames)))
            ctx.publish("builder.trace.current", "0")
            ctx.publish("builder.trace.error_type", frames[0].get("type", "unknown"))
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_trace_goto", self._cmd_trace_goto)

    def _parse_trace(self, text: str) -> list[dict]:
        frames = []
        for i, line in enumerate(text.splitlines()):
            m = re.match(r'^\s*File "(.*?)", line (\d+), (.+?)(?:: (.*))?$', line)
            if m:
                frames.append({
                    "frame_id": str(i),
                    "file": m.group(1),
                    "line": m.group(2),
                    "func": m.group(3),
                    "msg": m.group(4) or "",
                })
        return frames

    def _cmd_trace_goto(self, args: list[str]) -> None:
        if not args:
            print("Usage: builder_trace_goto <frame_id>")
            return
        frame_id = args[0]
        for frame in self._frames:
            if frame["frame_id"] == frame_id:
                print(f"{frame['file']}:{frame['line']} in {frame['func']}")
                if frame['msg']:
                    print(f"  {frame['msg']}")
                self._current_index = self._frames.index(frame)
                ctx = getattr(self, "_ctx", None)
                if ctx:
                    ctx.publish("builder.trace.current", str(self._current_index))
                return
        print(f"Frame not found: {frame_id}")
