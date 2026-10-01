import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import os
import subprocess
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session


class ProjectContextPlugin(TerminalPlugin):
    name = "project-context"
    version = "0.1.0"
    load_priority = 25

    def on_session_switch(self, ctx: PluginContext, old: Session | None, new: Session) -> None:
        root = self._detect_project_root(new.cwd)
        lang = self._detect_language(new.cwd)
        file_count = self._count_files(new.cwd)
        ctx.publish("builder.project.root", root or "")
        ctx.publish("builder.project.lang", lang or "")
        ctx.publish("builder.project.file_count", str(file_count))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("builder_project_tree", self._cmd_project_tree)

    def _detect_project_root(self, cwd: str) -> str | None:
        markers = [".git", "package.json", "Cargo.toml", "pyproject.toml",
                   "go.mod", "Makefile", ".project", "pom.xml"]
        current = Path(cwd)
        for parent in [current] + list(current.parents):
            if any((parent / m).exists() for m in markers):
                return str(parent)
        return None

    def _detect_language(self, cwd: str) -> str | None:
        checks = {
            "package.json": "javascript/typescript",
            "Cargo.toml": "rust",
            "pyproject.toml": "python",
            "go.mod": "go",
            "pom.xml": "java",
            "Makefile": "make",
        }
        for fname, lang in checks.items():
            if Path(cwd, fname).exists():
                return lang
        return None

    def _count_files(self, cwd: str) -> int:
        try:
            result = subprocess.run(
                ["find", ".", "-type", "f", "-not", "-path", "./.git/*"],
                cwd=cwd, capture_output=True, timeout=10, text=True
            )
            if result.returncode == 0:
                return len(result.stdout.strip().splitlines())
        except Exception:
            pass
        return 0

    def _cmd_project_tree(self, args: list[str]) -> None:
        path = args[0] if args else "."
        try:
            result = subprocess.run(
                ["find", path, "-type", "f", "-not", "-path", "*/.git/*", "-not", "-path", "*/__pycache__/*"],
                capture_output=True, text=True, timeout=10
            )
            print(result.stdout[:3000])
        except Exception as e:
            print(f"Error: {e}")
