"""Release test config: isolate stores; skip tests whose dependency is absent (reason printed by `pytest -rs`)."""
import os
from pathlib import Path

import pytest

NEEDS_OMNITOOL = {
    "test_tools_bash_write_edit_read",
    "test_write_scope_is_enforced_through_omnitool",
    "test_loop_keeps_the_provider_it_was_given",
    "test_cli_mock_run_records_a_session",
    "test_inbox_message_lands_at_the_next_turn",
    "test_run_posts_arrive_and_debrief_and_marks_the_session",
}
FLEET_ONLY = {  # need a lane catalogue, OpenCode role config and provider keys (the original fleet's setup)
    "test_opencode_only_free_tier_is_refused",
    "test_lane_prefix_is_stripped_and_endpoint_is_real",
    "test_roles_come_from_opencode_config",
    "test_cli_mock_run_records_a_session",
    "test_opencode_compat_flags_are_accepted",
    "test_server_api_and_cross_origin_refusal",
    "test_inbox_message_lands_at_the_next_turn",
    "test_run_posts_arrive_and_debrief_and_marks_the_session",
}
NOT_SHIPPED = {"test_commander_commands_executable"}  # commander role plugins are not part of 0.1.0


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELD_DB", str(tmp_path / "field.sqlite"))
    monkeypatch.setenv("FLEET_EXEC_MARKS", "1")
    monkeypatch.setenv("TALON_DB", str(tmp_path / "talon.db"))
    monkeypatch.setenv("FLEET_EVENTS_PATH", str(tmp_path / "events.jsonl"))


def pytest_collection_modifyitems(config, items):
    has_omnitool = Path(os.environ.get("FLEET_EXEC", "bin/fleet-exec")).is_file()
    in_fleet = os.environ.get("TALON_FLEET") == "1"
    for item in items:
        name = item.name.split("[")[0]
        if name in NEEDS_OMNITOOL and not has_omnitool:
            item.add_marker(pytest.mark.skip(reason="needs Omnitool: set FLEET_EXEC to its bin/fleet-exec"))
        if name in FLEET_ONLY and not in_fleet:
            item.add_marker(pytest.mark.skip(reason="needs a lane catalogue, role config and provider keys (TALON_FLEET=1)"))
        if name in NOT_SHIPPED:
            item.add_marker(pytest.mark.skip(reason="commander role plugins are not shipped in 0.1.0"))
