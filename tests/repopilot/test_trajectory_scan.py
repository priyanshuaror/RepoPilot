"""Tests for repopilot.testing.trajectory_scan.extract_command_runs.

The synthetic messages below are shaped exactly like what
minisweagent.models.litellm_model.LitellmModel / models.utils.actions_toolcall
actually produce (assistant message with extra.actions -> tool message with a
matching tool_call_id and extra.raw_output), so this test is really checking
"does our correlation logic match mini-swe-agent's real message format",
not just "does it work on made-up data".
"""

from repopilot.testing.pytest_parser import find_pytest_runs
from repopilot.testing.trajectory_scan import extract_command_runs


def _assistant_message(command: str, tool_call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": f"Running: {command}",
        "tool_calls": [{"id": tool_call_id, "function": {"name": "bash", "arguments": f'{{"command": "{command}"}}'}}],
        "extra": {"actions": [{"command": command, "tool_call_id": tool_call_id}]},
    }


def _tool_message(tool_call_id: str, output: str, returncode: int = 0) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": f"<returncode>{returncode}</returncode>\n<o>\n{output}</o>",
        "extra": {"raw_output": output, "returncode": returncode},
    }


def test_extract_single_command_run():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Please fix the bug."},
        _assistant_message("ls -la", "call_1"),
        _tool_message("call_1", "total 0\ndrwxr-xr-x  2 root root"),
    ]

    runs = extract_command_runs(messages)

    assert len(runs) == 1
    assert runs[0].command == "ls -la"
    assert "drwxr-xr-x" in runs[0].output
    assert runs[0].returncode == 0


def test_extract_multiple_sequential_command_runs():
    messages = [
        _assistant_message("echo one", "call_1"),
        _tool_message("call_1", "one"),
        _assistant_message("echo two", "call_2"),
        _tool_message("call_2", "two"),
    ]

    runs = extract_command_runs(messages)

    assert [r.command for r in runs] == ["echo one", "echo two"]
    assert [r.output for r in runs] == ["one", "two"]


def test_extract_ignores_messages_without_actions():
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "task description"},
        {"role": "assistant", "content": "just thinking, no tool call yet", "extra": {}},
    ]

    assert extract_command_runs(messages) == []


def test_find_pytest_runs_end_to_end():
    """Full pipeline: synthetic trajectory -> extract commands -> parse pytest output."""
    messages = [
        _assistant_message("ls -la", "call_1"),
        _tool_message("call_1", "app.py\ntests/"),
        _assistant_message("pytest -q", "call_2"),
        _tool_message(
            "call_2",
            "....F                                                                   [100%]\n"
            "FAILED tests/test_app.py::test_edge_case - AssertionError\n"
            "========================= 1 failed, 4 passed in 0.20s =========================",
        ),
    ]

    test_runs = find_pytest_runs(messages)

    assert len(test_runs) == 1
    assert test_runs[0]["command"] == "pytest -q"
    assert test_runs[0]["passed"] == 4
    assert test_runs[0]["failed"] == 1
    assert test_runs[0]["all_passed"] is False
    assert test_runs[0]["failing_tests"] == ["tests/test_app.py::test_edge_case"]


def test_find_pytest_runs_ignores_non_pytest_commands():
    messages = [
        _assistant_message("ls -la", "call_1"),
        _tool_message("call_1", "app.py"),
    ]
    assert find_pytest_runs(messages) == []
