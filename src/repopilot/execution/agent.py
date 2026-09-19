"""
Phase 7 - code execution.

RepoPilot does not reimplement an agent. The reason/act/observe loop, the model
integrations and the execution environments all come from mini-swe-agent,
unmodified. This module is the thin layer around it: it builds the task prompt
out of the approved plan and the impact analysis, starts the upstream agent
inside the ingested checkout, and records what happened.

``minisweagent`` is imported lazily inside the functions that need it, so the
rest of RepoPilot (and its unit tests) can be imported without pulling in
litellm and friends.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from repopilot.analysis.impact import ImpactAnalysis
from repopilot.planning.planner import ImplementationPlan
from repopilot.testing.trajectory_scan import extract_command_runs


@dataclass
class ExecutionRecord:
    """What the agent did: the auditable half of "verification-first"."""

    exit_status: str = ""
    submission: str = ""
    commands: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    error: str = ""
    step_count: int = 0

    @property
    def succeeded(self) -> bool:
        return not self.error

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["succeeded"] = self.succeeded
        return data


def build_task_prompt(
    issue: str, plan: ImplementationPlan, impact: ImpactAnalysis, *, repo_path: str = ""
) -> str:
    """Compose the task text handed to the agent.

    The agent is given the *approved* plan and the predicted blast radius rather
    than just the raw issue, so its exploration starts from the analysis a human
    already signed off on. It is told explicitly that the blast radius is an
    estimate, so it does not treat the file list as a hard boundary.
    """
    lines = [f"## Issue to resolve\n\n{issue}\n"]
    if repo_path:
        lines.append(f"Repository checkout: {repo_path}\n")
    lines.append("## Approved implementation plan\n")
    lines.append(plan.to_markdown())
    lines.append("\n## Predicted blast radius (an estimate from static analysis, not a boundary)\n")
    lines.append(impact.explain())
    lines.append(
        "\n## How to work\n\n"
        "- Start from the files above, but verify the analysis yourself before editing.\n"
        "- If the real fix lies outside the predicted blast radius, say so explicitly in your reasoning "
        "and proceed - an inaccurate prediction is a finding, not a constraint.\n"
        "- Make the change, then prove it with a test. A change that no test exercises is not finished.\n"
        "- Keep edits inside this checkout.\n"
    )
    return "\n".join(lines)


def collect_command_runs(messages: list[dict]) -> list[dict[str, Any]]:
    """Flatten a trajectory into serializable ``{command, returncode, output}`` records."""
    return [
        {"command": run.command, "returncode": run.returncode, "output": run.output}
        for run in extract_command_runs(messages)
    ]


def build_agent(config: dict[str, Any], repo_path: Path, *, model_name: str | None = None, agent_type: str = "default"):
    """Construct an unmodified mini-swe-agent agent bound to ``repo_path``.

    The environment's ``cwd`` is pinned to the checkout here (rather than in
    YAML) because mini-swe-agent's environment config values are plain strings,
    not templates - and because pinning it in code is the execution boundary that
    keeps agent commands from running against the host project directory.
    """
    from minisweagent.agents import get_agent
    from minisweagent.environments import get_environment
    from minisweagent.models import get_model

    model = get_model(model_name, config=config.get("model", {}))
    env = get_environment(
        {**config.get("environment", {}), "cwd": str(repo_path)},
        default_type="local",
    )
    return get_agent(model, env, config.get("agent", {}), default_type=agent_type)


def run_agent(
    agent: Any,
    task: str,
    repo_path: Path,
    *,
    trajectory_path: Path | None = None,
) -> ExecutionRecord:
    """Run an already-constructed agent and capture an :class:`ExecutionRecord`.

    Exceptions from the agent loop are captured rather than propagated: a run
    that crashed halfway still has a trajectory and a diff worth reporting.
    """
    from repopilot.reporting.diff import get_changed_files

    record = ExecutionRecord()
    try:
        result = agent.run(task, repo_path=str(repo_path))
        record.exit_status = str(result.get("exit_status", ""))
        record.submission = str(result.get("submission", ""))
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"

    messages = list(getattr(agent, "messages", []))
    record.step_count = len(messages)
    record.commands = collect_command_runs(messages)
    record.changed_files = get_changed_files(repo_path)

    if trajectory_path is not None and hasattr(agent, "save"):
        try:
            agent.save(trajectory_path)
        except Exception as exc:
            record.error = (record.error + " | " if record.error else "") + f"trajectory save failed: {exc}"
    return record
