import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import json
import os
from plugins import TerminalPlugin, PluginContext, CommandRegistry, Session

SEQUENCES_DIR = Path(os.path.expanduser("~/.config/terminal-launcher/exec-pad/sequences"))
SEQUENCES_DIR.mkdir(parents=True, exist_ok=True)


class ExecPadPlugin(TerminalPlugin):
    name = "exec-pad"
    version = "0.1.0"
    load_priority = 60
    _recording: bool = False
    _recording_name: str = ""
    _recording_steps: list[str] = []

    def on_session_create(self, ctx: PluginContext, session: Session) -> None:
        ctx.publish("commander.exec.history", "[]")
        ctx.publish("commander.exec.recording", json.dumps({"active": False}))

    def register_commands(self, registry: CommandRegistry) -> None:
        registry.register("commander_sequence_record", self._cmd_sequence_record)
        registry.register("commander_sequence_stop", self._cmd_sequence_stop)
        registry.register("commander_sequence_run", self._cmd_sequence_run)
        registry.register("commander_sequence_list", self._cmd_sequence_list)

    def _cmd_sequence_record(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_sequence_record <sequence_name>")
            return
        self._recording = True
        self._recording_name = args[0]
        self._recording_steps = []
        print(f"Recording sequence: {self._recording_name}")

    def _cmd_sequence_stop(self, args: list[str]) -> None:
        if not self._recording:
            print("Not recording")
            return
        self._recording = False
        seq_file = SEQUENCES_DIR / f"{self._recording_name}.json"
        try:
            with open(seq_file, "w") as f:
                json.dump({"name": self._recording_name, "steps": self._recording_steps}, f, indent=2)
            print(f"Saved sequence: {self._recording_name}")
        except Exception as e:
            print(f"Error saving sequence: {e}")
        self._recording = False
        self._recording_name = ""
        self._recording_steps = []

    def _cmd_sequence_run(self, args: list[str]) -> None:
        if not args:
            print("Usage: commander_sequence_run <sequence_name>")
            return
        name = args[0]
        seq_file = SEQUENCES_DIR / f"{name}.json"
        if not seq_file.exists():
            print(f"Sequence not found: {name}")
            return
        try:
            with open(seq_file) as f:
                seq = json.load(f)
            for step in seq.get("steps", []):
                print(f"  > {step}")
        except Exception as e:
            print(f"Error: {e}")

    def _cmd_sequence_list(self, args: list[str]) -> None:
        if not SEQUENCES_DIR.exists():
            print("No sequences")
            return
        for f in SEQUENCES_DIR.iterdir():
            if f.suffix == ".json":
                print(f.stem)
