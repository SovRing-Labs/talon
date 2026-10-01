"""Terminal Launcher TUI — Deprecation Shim (GENR v19.3 / TALON-PRUNE-CURSES-TUI).

The 445-line curses-based multiplexer has been pruned per the 3/3 Oracle Converge decision.
Native bash role-shell (role_shell.py) is the sole interactive terminal interface.
This shim provides backward-compatibility for legacy imports and callers.
"""

import sys
import logging

LOG = logging.getLogger("terminal-launcher.tui")


def launch_tui(launcher=None) -> None:
    """Legacy launch_tui entrypoint: defaults to native role-shell."""
    role = getattr(launcher, "role", None) or "commander"
    LOG.info("Curses TUI pruned; redirecting to native %s role-shell", role)
    from role_shell import launch_role_shell
    launch_role_shell(role)


class TerminalTUI:
    """Deprecated curses TUI stub for backward-compatibility."""
    def __init__(self, stdscr=None, launcher=None):
        self.launcher = launcher
        self.stdscr = stdscr
        LOG.warning("TerminalTUI is deprecated and pruned in GENR v19.3.")

    def loop(self) -> None:
        launch_tui(self.launcher)


class ScrollbackBuffer:
    """Lightweight scrollback buffer compatibility shim."""
    def __init__(self, maxlen=1000):
        self.lines = []
        self.maxlen = maxlen

    def append(self, text: str) -> None:
        for line in text.splitlines():
            self.lines.append(line)
            if len(self.lines) > self.maxlen:
                self.lines.pop(0)
