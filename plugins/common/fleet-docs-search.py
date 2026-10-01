import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

DOCS_CLI = Path("bin/fleet-docs")


class FleetDocsSearchPlugin(TerminalPlugin):
    name = "fleet-docs-search"
    version = "0.1.0"
    load_priority = 35

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("fleet_doc", self._cmd_search)
        registry.register("doc", self._cmd_search)

    def _cmd_search(self, args: list[str]) -> None:
        if not args:
            print("Usage: doc <query> (e.g. 'doc caw', 'doc dispatcherd')")
            return
        query = " ".join(args)
        if DOCS_CLI.exists():
            try:
                result = subprocess.run(
                    [str(DOCS_CLI), "search", query],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout:
                    print(result.stdout)
                elif result.stderr:
                    print(result.stderr)
                else:
                    print(f"No documentation found for '{query}'")
                return
            except Exception as e:
                print(f"Error calling fleet-docs: {e}")

        # Fallback simple grep in docs/
        docs_dir = Path("./docs")
        if docs_dir.exists():
            matches = list(docs_dir.glob(f"*{query}*.md"))
            if matches:
                print(f"Found docs matching '{query}':")
                for m in matches[:5]:
                    print(f"  {m.name}")
            else:
                print(f"No doc files matched '{query}' in {docs_dir}")
