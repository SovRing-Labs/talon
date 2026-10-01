import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from datetime import datetime, timezone
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

RESEARCH_DATA_DIR = Path.home() / ".local" / "share" / "terminal-launcher"


class ResearchUrlCapturePlugin(TerminalPlugin):
    name = "research-url-capture"
    version = "0.1.0"
    load_priority = 20

    def on_output(self, ctx: PluginContext, session: Session, data: bytes) -> bytes:
        text = data.decode(errors="replace")
        urls = self._extract_urls(text)
        if urls:
            for url in urls:
                self._save_url(url)
            ctx.publish("researcher.urls.count", str(self._count_urls()))
            ctx.publish("researcher.urls.last", urls[-1])
        return data

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_urls", self._cmd_urls)

    def _extract_urls(self, text: str) -> list[str]:
        import re
        return re.findall(r"https?://[^\s<>\"]+", text)

    def _urls_file(self) -> Path:
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        return RESEARCH_DATA_DIR / "research-urls.jsonl"

    def _save_url(self, url: str) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "url": url}
        try:
            with open(self._urls_file(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _count_urls(self) -> int:
        f = self._urls_file()
        if not f.exists():
            return 0
        try:
            return sum(1 for _ in open(f))
        except Exception:
            return 0

    def _cmd_urls(self, args: list[str]) -> None:
        f = self._urls_file()
        if not f.exists():
            print("No URLs captured")
            return
        try:
            with open(f) as fh:
                lines = fh.readlines()
            for line in lines[-20:]:
                entry = json.loads(line)
                print(f"{entry['ts']} | {entry['url']}")
        except Exception as e:
            print(f"Error: {e}")
