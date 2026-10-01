import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

PLAN_PATH = Path("control-center/PLAN.md")
BRIEFS_DIR = Path("./spawn-briefs/active")


class OracleBriefAnnotatorPlugin(TerminalPlugin):
    name = "oracle-brief-annotator"
    version = "0.1.0"
    load_priority = 50

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("oracle_annotate", self._cmd_annotate)

    def _cmd_annotate(self, args: list[str]) -> None:
        if len(args) < 2:
            print("Usage: oracle_annotate <brief_name> <annotation>")
            return
        brief_name = args[0]
        annotation = " ".join(args[1:])
        brief_path = BRIEFS_DIR / f"{brief_name}.md"
        if not brief_path.exists():
            print(f"Brief not found: {brief_name}")
            return
        try:
            content = brief_path.read_text()
            annotated = content + f"\n\n<!-- ORACLE: {annotation} -->\n"
            brief_path.write_text(annotated)
            ctx = getattr(self, "_ctx", None)
            if ctx:
                ctx.publish("oracle.brief.annotated", brief_name)
            print(f"Annotated: {brief_name}")
        except Exception as e:
            print(f"Error: {e}")
