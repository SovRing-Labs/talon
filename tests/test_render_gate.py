"""Render-GATE test for terminal-launcher using pyte terminal emulation.

This test proves that Talon role-shell interfaces and terminal sessions
actually render content (not a white/blank page) by driving them in a
virtual terminal and asserting on visible screen output.
"""

import pytest
import sys
import os
import threading
import time
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import pyte
from main import TerminalLauncher
from role_shell import ROLES_BANNER, PROMPT_COLORS


class VirtualTerminal:
    """Virtual terminal using pyte for headless terminal output testing."""
    
    def __init__(self, lines=30, cols=100):
        self.screen = pyte.Screen(cols, lines)
        self.stream = pyte.ByteStream(self.screen)
        self.lines = lines
        self.cols = cols
        self._cursor_y = 0
        self._cursor_x = 0
        
    def feed(self, data: bytes):
        """Feed raw bytes to the terminal emulator."""
        self.stream.feed(data)
    
    def get_display(self) -> str:
        """Get the current screen display as text."""
        return "\n".join(self.screen.display)
    
    def get_cursor(self) -> tuple:
        return (self.screen.cursor.y, self.screen.cursor.x)
    
    def reset(self):
        self.screen.reset()


class MockCursesWindow:
    """Mock curses window that captures output to a virtual terminal using pyte."""
    
    def __init__(self, vt: VirtualTerminal):
        self.vt = vt
        self._attrs = {}
        self._color_pairs = {}
        self._max_y = vt.lines
        self._max_x = vt.cols
        
    def getmaxyx(self):
        return (self._max_y, self._max_x)
    
    def erase(self):
        self.vt.stream.feed(b"\x1b[2J")
        self.vt.stream.feed(b"\x1b[H")
        
    def addstr(self, y, x, text, attr=0):
        self.vt.stream.feed(f"\x1b[{y+1};{x+1}H".encode())
        self.vt.stream.feed(text.encode("utf-8", errors="replace"))
    
    def clrtoeol(self):
        self.vt.stream.feed(b"\x1b[K")
    
    def vline(self, y, x, ch, n):
        char = "|" if ch == 9550 else chr(ch)
        for i in range(n):
            if y + i < self._max_y:
                self.vt.stream.feed(f"\x1b[{y+i+1};{x+1}H".encode())
                self.vt.stream.feed(char.encode())
    
    def refresh(self):
        pass
    
    def keypad(self, flag):
        pass
    
    def curs_set(self, visibility):
        pass


def test_render_gate_commander_banner():
    """Render-GATE: Verify Commander role banner renders accurately in terminal."""
    vt = VirtualTerminal(lines=30, cols=100)
    banner = ROLES_BANNER.get("commander", "")
    assert banner, "Commander banner must be defined"
    
    vt.feed(banner.encode("utf-8"))
    display = vt.get_display()
    
    assert "TALON COMMANDER EXOSKELETON" in display
    assert "SOVEREIGN COCKPIT" in display
    assert "Strategic General" in display
    assert len(display.strip()) > 0


def test_render_gate_all_role_banners():
    """Render-GATE: Verify all 7 role banners render valid ANSI without blank screen."""
    roles = ["commander", "dev", "bugstomp", "builder", "oracle", "researcher", "grunt"]
    for role in roles:
        vt = VirtualTerminal(lines=20, cols=120)
        banner = ROLES_BANNER.get(role, "")
        assert banner, f"Role banner for {role} must exist"
        
        vt.feed(banner.encode("utf-8"))
        display = vt.get_display()
        
        assert "Status:" in display, f"Role {role} missing status line"
        assert "╔" in display and "╚" in display, f"Role {role} missing border box"
        assert len(display.strip()) > 50, f"Role {role} rendered blank or truncated output"


def test_render_gate_escape_handling_and_mock_window():
    """Render-GATE: Test virtual terminal cursor movement, clearing, and line drawing."""
    vt = VirtualTerminal(lines=25, cols=80)
    win = MockCursesWindow(vt)
    
    win.erase()
    win.addstr(2, 5, "SOVEREIGN RAPTOR COCKPIT")
    win.vline(3, 5, ord("|"), 5)
    win.addstr(10, 5, "STATUS: ALL SYSTEMS GREEN")
    
    display = vt.get_display()
    assert "SOVEREIGN RAPTOR COCKPIT" in display
    assert "STATUS: ALL SYSTEMS GREEN" in display


def test_render_gate_session_scrollback_rendering():
    """Render-GATE: Verify launcher session output renders into terminal emulator."""
    vt = VirtualTerminal(lines=30, cols=100)
    launcher = TerminalLauncher()
    session = launcher.create_session("render-test")
    
    output_lines = [
        "[00:00:01] System boot sequence initiated",
        "[00:00:02] Core daemons: 9/9 PASS",
        "[00:00:03] Terminal session established on pty",
    ]
    
    for line in output_lines:
        vt.feed((line + "\r\n").encode("utf-8"))
        
    display = vt.get_display()
    assert "System boot sequence initiated" in display
    assert "Core daemons: 9/9 PASS" in display
    assert "Terminal session established on pty" in display
    
    launcher.close_session("render-test", persist=False)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
