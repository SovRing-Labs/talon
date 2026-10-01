import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import re
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

TEMPLATES_DIR = Path.home() / ".config" / "terminal-launcher" / "templates"


class FileTemplatePlugin(TerminalPlugin):
    name = "file-template"
    version = "0.1.0"
    load_priority = 40

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        count = self._count_templates()
        ctx.publish("builder.template.count", str(count))
        ctx.publish("builder.template.last", "")

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_template_expand", self._cmd_template_expand)

    def _list_templates(self) -> list[str]:
        if not TEMPLATES_DIR.exists():
            return []
        return [f.stem for f in TEMPLATES_DIR.iterdir() if f.suffix in (".tmpl", ".template")]

    def _load_template(self, name: str) -> str | None:
        for ext in (".tmpl", ".template"):
            tpath = TEMPLATES_DIR / f"{name}{ext}"
            if tpath.exists():
                try:
                    return tpath.read_text()
                except Exception:
                    pass
        return None

    def _count_templates(self) -> int:
        return len(self._list_templates())

    def _expand_vars(self, template: str, vars: dict) -> str:
        result = template
        for k, v in vars.items():
            result = result.replace(f"${{{k}}}", v)
            result = result.replace(f"${k}", v)
        return result

    def _cmd_template_expand(self, args: list[str]) -> None:
        if not args:
            print("Usage: builder_template_expand <template_name> [var1=val1 ...]")
            return
        name = args[0]
        vars_str = args[1:]
        vars_dict = {}
        for v in vars_str:
            if "=" in v:
                k, val = v.split("=", 1)
                vars_dict[k] = val
        template = self._load_template(name)
        if template is None:
            print(f"Template not found: {name}")
            return
        expanded = self._expand_vars(template, vars_dict)
        print(expanded)
        ctx = getattr(self, "_ctx", None)
        if ctx:
            ctx.publish("builder.template.last", name)
