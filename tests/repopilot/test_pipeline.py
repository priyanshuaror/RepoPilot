"""End-to-end tests for repopilot.pipeline.

No network, no GitHub, no LLM, no real test suite: the repository is a local git
checkout, the agent is a fake that edits a file, and the test runner is a stub.
That is the point of the injection points in ``pipeline.run`` - the orchestration
itself is what is under test here.
"""

import json

import pytest

from repopilot.config.settings import RepoPilotConfig
from repopilot.pipeline import analyze, run
from repopilot.testing.verification import TestResult

ISSUE = "Fix JWT expiration handling - expired tokens are still accepted."


class FakeAgent:
    """Stands in for a mini-swe-agent agent: edits one file, records messages."""

    def __init__(self, repo_path, edits=None, exit_status="Submitted"):
        self.repo_path = repo_path
        self.edits = edits if edits is not None else {"src/auth/jwt.py": "# patched by the fake agent\n"}
        self.exit_status = exit_status
        self.messages = []
        self.saved_to = None
        self.tasks = []

    def run(self, task, **kwargs):
        self.tasks.append(task)
        for relative, content in self.edits.items():
            path = self.repo_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.messages = [
            {"role": "assistant", "extra": {"actions": [{"command": "ls", "tool_call_id": "c1"}]}},
            {"role": "tool", "tool_call_id": "c1", "extra": {"raw_output": "src", "returncode": 0}},
        ]
        return {"exit_status": self.exit_status, "submission": "done"}

    def save(self, path):
        self.saved_to = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.messages), encoding="utf-8")


def _passing(command, path):
    return TestResult(command=command, exit_code=0, passed=4, parsed=True)


def _failing(command, path):
    return TestResult(
        command=command,
        exit_code=1,
        failed=1,
        parsed=True,
        failures=["tests/test_auth.py::test_is_expired"],
        output='File "src/auth/jwt.py", line 9\nE   AssertionError: assert True is False',
    )


def _config(**overrides):
    defaults = {"approval_mode": "auto", "max_fix_attempts": 2}
    defaults.update(overrides)
    return RepoPilotConfig(**defaults)


# --------------------------------------------------------------- analyze only


def test_analyze_produces_every_read_only_stage(sample_git_repo, tmp_path):
    bundle = analyze(str(sample_git_repo), ISSUE, _config(), workspace=tmp_path / "ws")

    assert bundle.repo.commit_sha
    assert "src/auth/jwt.py" in bundle.repository_analysis.source_files
    assert "src/auth/jwt.py" in bundle.impact.paths
    assert bundle.plan is not None


def test_analyze_does_not_modify_the_source_repository(sample_git_repo, tmp_path):
    before = (sample_git_repo / "src" / "auth" / "jwt.py").read_text()
    analyze(str(sample_git_repo), ISSUE, _config(), workspace=tmp_path / "ws")
    assert (sample_git_repo / "src" / "auth" / "jwt.py").read_text() == before


def test_analyze_bundle_is_json_serializable(sample_git_repo, tmp_path):
    bundle = analyze(str(sample_git_repo), ISSUE, _config(), workspace=tmp_path / "ws")
    json.dumps(bundle.to_dict(), default=str)


# ----------------------------------------------------------------- full runs


def test_verified_run(sample_git_repo, tmp_path):
    agents = []

    def factory(path):
        agent = FakeAgent(path)
        agents.append(agent)
        return agent

    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=tmp_path / "out",
        run_id="test-run",
        agent_factory=factory,
        test_runner=_passing,
    )

    assert result.report.status == "VERIFIED"
    assert result.repair.verified is True
    assert result.repair.attempt_count == 0
    assert "src/auth/jwt.py" in result.changes.all_files
    assert len(agents) == 1  # no repair agent was needed


def test_run_writes_all_artifacts(sample_git_repo, tmp_path):
    out = tmp_path / "out"
    run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=out,
        agent_factory=lambda path: FakeAgent(path),
        test_runner=_passing,
    )

    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "changes.diff").is_file()
    assert (out / "trajectory.json").is_file()
    assert "src/auth/jwt.py" in (out / "changes.diff").read_text()


def test_rejected_plan_leaves_the_repository_untouched(sample_git_repo, tmp_path):
    called = []

    result = run(
        str(sample_git_repo),
        ISSUE,
        RepoPilotConfig(approval_mode="interactive"),
        output_dir=tmp_path / "out",
        approval_prompt=lambda text: False,
        agent_factory=lambda path: called.append(path) or FakeAgent(path),
        test_runner=_passing,
    )

    assert result.report.status == "REJECTED"
    assert called == [], "no agent should be constructed after a rejection"
    assert result.execution is None
    assert (tmp_path / "out" / "report.json").is_file()


def test_dry_run_mode_stops_before_implementation(sample_git_repo, tmp_path):
    result = run(
        str(sample_git_repo),
        ISSUE,
        RepoPilotConfig(approval_mode="dry-run"),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: pytest.fail("dry-run must not build an agent"),
        test_runner=_passing,
    )

    assert result.report.status == "REJECTED"
    assert result.report.plan, "a dry run should still produce a plan"


def test_failing_tests_trigger_bounded_repair_then_fail(sample_git_repo, tmp_path):
    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(max_fix_attempts=2),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: FakeAgent(path),
        test_runner=_failing,
    )

    assert result.report.status == "FAILED"
    assert result.repair.verified is False
    # identical failures every time: the loop must notice instead of burning attempts
    assert result.repair.stop_reason == "repeated_identical_failure"


def test_repair_succeeds_on_the_second_run(sample_git_repo, tmp_path):
    calls = {"n": 0}

    def flaky(command, path):
        calls["n"] += 1
        return _failing(command, path) if calls["n"] == 1 else _passing(command, path)

    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(run_broader_tests=False),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: FakeAgent(path),
        test_runner=flaky,
    )

    assert result.repair.attempt_count == 1
    assert result.report.status == "VERIFIED"


def test_broader_suite_regression_is_reported_as_failure(sample_git_repo, tmp_path):
    seen = []

    def runner(command, path):
        seen.append(command)
        return _passing(command, path) if len(seen) == 1 else _failing(command, path)

    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: FakeAgent(path),
        test_runner=runner,
    )

    assert len(seen) == 2, "the broader suite should run after targeted tests pass"
    assert result.report.status == "FAILED"


def test_agent_receives_the_approved_plan_and_blast_radius(sample_git_repo, tmp_path):
    agent_holder = {}

    def factory(path):
        agent = FakeAgent(path)
        agent_holder["agent"] = agent
        return agent

    run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=tmp_path / "out",
        agent_factory=factory,
        test_runner=_passing,
    )

    task = agent_holder["agent"].tasks[0]
    assert "Approved implementation plan" in task
    assert "Predicted blast radius" in task
    assert "src/auth/jwt.py" in task


def test_out_of_scope_change_is_warned_about(sample_git_repo, tmp_path):
    def factory(path):
        return FakeAgent(path, edits={"tools/unrelated_module.py": "x = 1\n"})

    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=tmp_path / "out",
        agent_factory=factory,
        test_runner=_passing,
    )

    assert "tools/unrelated_module.py" in result.changes.unexpected_files
    assert any("outside the predicted impact area" in w for w in result.changes.warnings)


def test_agent_error_is_recorded_not_raised(sample_git_repo, tmp_path):
    class ExplodingAgent(FakeAgent):
        def run(self, task, **kwargs):
            raise RuntimeError("model unavailable")

    result = run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: ExplodingAgent(path),
        test_runner=_passing,
    )

    assert "model unavailable" in result.execution.error
    assert any("agent run reported an error" in w for w in result.report.warnings)


def test_report_json_captures_every_stage(sample_git_repo, tmp_path):
    out = tmp_path / "out"
    run(
        str(sample_git_repo),
        ISSUE,
        _config(),
        output_dir=out,
        agent_factory=lambda path: FakeAgent(path),
        test_runner=_passing,
    )

    data = json.loads((out / "report.json").read_text())
    for key in (
        "repository",
        "repository_analysis",
        "issue_analysis",
        "impact",
        "plan",
        "approval",
        "execution",
        "test_selection",
        "targeted_tests",
        "changes",
        "status",
    ):
        assert data[key], f"report.json is missing {key}"
