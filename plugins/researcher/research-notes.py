import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from datetime import datetime, timezone
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

RESEARCH_DATA_DIR = Path.home() / ".local" / "share" / "terminal-launcher"


class ResearchNotesPlugin(TerminalPlugin):
    name = "research-notes"
    version = "0.1.0"
    load_priority = 20

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("researcher.notes.count", "0")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("researcher_note", self._cmd_note)

    def _notes_file(self) -> Path:
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
        return RESEARCH_DATA_DIR / "research-notes.jsonl"

    def _count_notes(self) -> int:
        f = self._notes_file()
        if not f.exists():
            return 0
        try:
            return sum(1 for _ in open(f))
        except Exception:
            return 0

    def _cmd_note(self, args: list[str]) -> None:
        if not args:
            print("Usage: researcher_note <note_text>")
            return
        note = " ".join(args)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "note": note,
        }
        try:
            with open(self._notes_file(), "a") as f:
                f.write(json.dumps(entry) + "\n")
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish("researcher.notes.count", str(self._count_notes()))
            print(f"Note saved: {note[:60]}")
        except Exception as e:
            print(f"Error: {e}")
