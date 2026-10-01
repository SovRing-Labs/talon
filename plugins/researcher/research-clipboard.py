import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import subprocess
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

RESEARCH_DATA_DIR = Path.home() / ".local" / "share" / "terminal-launcher"


class ResearchClipboardPlugin(TerminalPlugin):
    name = "research-clipboard"
    version = "0.1.0"
    load_priority = 20

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("researcher.clipboard.yank_count", "0")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_clip_yank", self._cmd_clip_yank)

    def _yank_count(self) -> int:
        count_file = RESEARCH_DATA_DIR / "clipboard-yank-count.txt"
        if not count_file.exists():
            return 0
        try:
            return int(count_file.read_text().strip())
        except Exception:
            return 0

    def _increment_yank(self) -> None:
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        count_file = RESEARCH_DATA_DIR / "clipboard-yank-count.txt"
        count_file.write_text(str(self._yank_count() + 1))

    def _cmd_clip_yank(self, args: list[str]) -> None:
        if not args:
            print("Usage: researcher_clip_yank <content>")
            return
        content = " ".join(args)
        try:
            subprocess.run(["xclip", "-selection", "clipboard"], input=content, text=True, check=True)
            self._increment_yank()
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish("researcher.clipboard.yank_count", str(self._yank_count()))
            print(f"Yanked: {content[:60]}")
        except FileNotFoundError:
            print("xclip not available; content echoed:")
            print(content[:200])
        except Exception as e:
            print(f"Error: {e}")
