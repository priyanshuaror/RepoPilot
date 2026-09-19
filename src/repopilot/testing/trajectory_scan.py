"""
Walks an agent's message list (the exact format mini-swe-agent produces and saves
to trajectory.json) and pulls out (command, output) pairs - i.e. "what did the
agent actually run, and what came back."

This depends only on the message *shape* mini-swe-agent's tool-calling models
produce (see minisweagent/models/utils/actions_toolcall.py), not on any specific
agent subclass, so it works on any saved trajectory - live or loaded from disk.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandRun:
    command: str
    output: str
    returncode: int | None


def extract_command_runs(messages: list[dict]) -> list[CommandRun]:
    """Pair up every bash command the agent ran with its observed output.

    How the pairing works (see mini-swe-agent's actions_toolcall.py):
    - An assistant message that issues N bash commands carries them in
      `extra.actions`, each item shaped like `{"command": ..., "tool_call_id": ...}`.
    - The tool-result message(s) that follow carry the command's output in
      `extra.raw_output`, linked back to the command via a matching top-level
      `tool_call_id`.

    This walks the message list once, remembering pending commands by their
    tool_call_id until their matching result shows up.
    """
    pending_commands: dict[str, str] = {}
    runs: list[CommandRun] = []

    for message in messages:
        for action in message.get("extra", {}).get("actions", []):
            tool_call_id = action.get("tool_call_id")
            if tool_call_id:
                pending_commands[tool_call_id] = action["command"]

        tool_call_id = message.get("tool_call_id")
        if tool_call_id and tool_call_id in pending_commands:
            extra = message.get("extra", {})
            runs.append(
                CommandRun(
                    command=pending_commands.pop(tool_call_id),
                    output=extra.get("raw_output", ""),
                    returncode=extra.get("returncode"),
                )
            )

    return runs
