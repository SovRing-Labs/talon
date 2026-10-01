import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

SNIPPETS_DIR = Path.home() / ".config" / "terminal-launcher" / "snippets"
SNIPPETS_FILE = SNIPPETS_DIR / "snippets.json"


class SnippetBankPlugin(TerminalPlugin):
    name = "snippet-bank"
    version = "0.1.0"
    load_priority = 30

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        count = self._count_snippets()
        ctx.publish("builder.snippet.count", str(count))
        ctx.publish("builder.snippet.last_used", "")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_snippet_save", self._cmd_snippet_save)
        registry.register("builder_snippet_insert", self._cmd_snippet_insert)

    def _load_snippets(self) -> dict:
        if not SNIPPETS_FILE.exists():
            return {}
        try:
            with open(SNIPPETS_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_snippets(self, data: dict) -> None:
        SNIPPETS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(SNIPPETS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving snippet: {e}")

    def _count_snippets(self) -> int:
        return len(self._load_snippets())

    def _cmd_snippet_save(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: builder_snippet_save <key> <content>")
            return
        key = args[0]
        content = " ".join(args[1:])
        snippets = self._load_snippets()
        snippets[key] = content
        self._save_snippets(snippets)
        print(f"Saved snippet: {key}")

    def _cmd_snippet_insert(self, args: list[str]) -> None:
        if not args:
            print("Usage: builder_snippet_insert <key>")
            return
        key = args[0]
        snippets = self._load_snippets()
        if key in snippets:
            print(snippets[key])
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish("builder.snippet.last_used", key)
        else:
            print(f"Snippet not found: {key}")
