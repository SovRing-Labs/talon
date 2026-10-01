import pytest
import subprocess
import sys
from pathlib import Path

TALON_BIN = Path("bin/talon")
TERMINAL_LAUNCHER_SRC = Path("./src")


class TestTalonCli:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [str(TALON_BIN), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_help_shows_usage(self):
        result = subprocess.run(
            [str(TALON_BIN), "--help"],
            capture_output=True,
            text=True,
        )
        assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()
        assert "--help" in result.stdout or "--help" in result.stderr

    def test_help_shows_role_option(self):
        result = subprocess.run(
            [str(TALON_BIN), "--help"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        assert "--role" in combined

    def test_list_exits_zero(self):
        result = subprocess.run(
            [str(TALON_BIN), "--list"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_list_shows_saved_sessions_header(self):
        result = subprocess.run(
            [str(TALON_BIN), "--list"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        assert "Saved sessions:" in combined

    def test_selftest_exits_zero(self):
        result = subprocess.run(
            [str(TALON_BIN), "--selftest"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_selftest_reports_pass(self):
        result = subprocess.run(
            [str(TALON_BIN), "--selftest"],
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        assert "PASS" in combined
        assert "9/9" in combined or "pass" in combined.lower()

    def test_role_commander_is_accepted(self):
        result = subprocess.run(
            [str(TALON_BIN), "--role", "commander", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--role" in combined

    def test_role_builder_is_accepted(self):
        result = subprocess.run(
            [str(TALON_BIN), "--role", "builder", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--role" in combined

    def test_role_oracle_is_accepted(self):
        result = subprocess.run(
            [str(TALON_BIN), "--role", "oracle", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--role" in combined

    def test_role_grunt_is_accepted(self):
        result = subprocess.run(
            [str(TALON_BIN), "--role", "grunt", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--role" in combined

    def test_role_researcher_is_accepted(self):
        result = subprocess.run(
            [str(TALON_BIN), "--role", "researcher", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "--role" in combined
