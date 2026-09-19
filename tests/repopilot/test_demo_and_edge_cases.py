"""Tests for the offline demo in ``examples/`` and for repository edge cases.

The demo is part of the project's public surface - it is the first thing a
reviewer runs - so it is tested like any other code. These tests never shell out
to pytest inside the fixture repository; the test runner is stubbed, exactly as
``pipeline.run`` allows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from repopilot.analysis.impact import analyze_impact
from repopilot.analysis.issue import analyze_issue
from repopilot.analysis.repository import analyze_repository
from repopilot.config.settings import RepoPilotConfig
from repopilot.pipeline import run
from repopilot.testing.test_selector import select_tests
from repopilot.testing.verification import TestResult

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
sys.path.insert(0, str(EXAMPLES))

demo = pytest.importorskip("run_demo", reason="examples/ not present in this checkout")


def _passing(command, path):
    return TestResult(command=command, exit_code=0, passed=6, parsed=True)


# ------------------------------------------------------------------- fixtures


def test_demo_fixture_repository_exists():
    assert (EXAMPLES / "demo_repo" / "src" / "inventory" / "stock.py").is_file()
    assert (EXAMPLES / "demo_issue.md").is_file()


def test_demo_fixture_is_not_a_nested_git_repository():
    """A committed .git inside the project would confuse both git and reviewers."""
    assert not (EXAMPLES / "demo_repo" / ".git").exists()


def test_prepare_demo_repository_creates_a_real_checkout(tmp_path):
    repo = demo.prepare_demo_repository(tmp_path / "copy")

    assert (repo / ".git").is_dir()
    assert (repo / "src" / "inventory" / "stock.py").is_file()
    result = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    assert result.stdout.strip() == "", "the fresh checkout should be clean"


def test_demo_fix_actually_fixes_the_bug(tmp_path):
    """The scripted patch must be a real fix, not a plausible-looking string."""
    module = tmp_path / "stock.py"
    module.write_text(demo.FIXED_STOCK, encoding="utf-8")
    namespace: dict = {}
    exec(compile(module.read_text(), "stock.py", "exec"), namespace)  # noqa: S102

    with pytest.raises(ValueError):
        namespace["reserve"]("widget", -10, {"widget": 5})
    with pytest.raises(ValueError):
        namespace["reserve"]("widget", 0, {"widget": 5})
    assert namespace["reserve"]("widget", 2, {"widget": 5}) == {"widget": 3}


def test_broken_variant_really_is_broken(tmp_path):
    """--fail-first must fail for a real reason, not a rigged one."""
    namespace: dict = {}
    exec(compile(demo.BROKEN_STOCK, "stock.py", "exec"), namespace)  # noqa: S102

    with pytest.raises(ValueError):
        namespace["reserve"]("widget", -1, {"widget": 5})
    # quantity == 0 slips through: that is the defect the regression test catches
    assert namespace["reserve"]("widget", 0, {"widget": 5}) == {"widget": 5}


def test_scripted_agent_matches_the_agent_interface(tmp_path):
    repo = demo.prepare_demo_repository(tmp_path / "copy")
    agent = demo.ScriptedAgent(repo, {"src/inventory/stock.py": demo.FIXED_STOCK}, "fixed it")

    result = agent.run("do the thing")

    assert result["exit_status"] == "Submitted"
    assert "quantity must be positive" in (repo / "src" / "inventory" / "stock.py").read_text()
    assert agent.messages and agent.messages[0]["role"] == "assistant"

    agent.save(tmp_path / "trajectory.json")
    assert json.loads((tmp_path / "trajectory.json").read_text())


# -------------------------------------------------------- demo through pipeline


def test_demo_scenario_runs_end_to_end(tmp_path):
    """The demo's repository + issue must flow through every pipeline stage."""
    repo = demo.prepare_demo_repository(tmp_path / "copy")
    issue = (EXAMPLES / "demo_issue.md").read_text(encoding="utf-8")

    result = run(
        str(repo),
        issue,
        RepoPilotConfig(approval_mode="auto", max_fix_attempts=1),
        output_dir=tmp_path / "out",
        run_id="demo-test",
        agent_factory=lambda path: demo.ScriptedAgent(
            path,
            {"src/inventory/stock.py": demo.FIXED_STOCK},
            "fixed it",
        ),
        test_runner=_passing,
    )

    assert result.report.status == "VERIFIED"
    assert "src/inventory/stock.py" in result.changes.all_files
    assert result.changes.unexpected_files == []


def test_demo_impact_analysis_finds_the_right_module(tmp_path):
    repo = demo.prepare_demo_repository(tmp_path / "copy")
    issue = (EXAMPLES / "demo_issue.md").read_text(encoding="utf-8")

    repo_analysis = analyze_repository(repo)
    impact = analyze_impact(analyze_issue(issue, repo_analysis), repo_analysis)

    assert "src/inventory/stock.py" in impact.paths
    # orders.py imports stock, so the import graph must pull it in
    assert "src/inventory/orders.py" in impact.paths


def test_demo_test_selection_prefers_the_covering_test(tmp_path):
    repo = demo.prepare_demo_repository(tmp_path / "copy")
    selection = select_tests(["src/inventory/stock.py"], analyze_repository(repo))

    assert selection.selected_tests[0].path == "tests/test_stock.py"


# ------------------------------------------------------------- edge-case repos


def test_empty_repository_analyses_without_crashing(empty_git_repo):
    analysis = analyze_repository(empty_git_repo)

    assert analysis.source_files == []
    assert analysis.test_files == []
    assert analysis.primary_language is None


def test_empty_repository_yields_an_honest_plan(empty_git_repo, tmp_path):
    result = run(
        str(empty_git_repo),
        "Fix the authentication timeout handling",
        RepoPilotConfig(approval_mode="dry-run"),
        output_dir=tmp_path / "out",
        test_runner=_passing,
    )

    assert result.report.status == "REJECTED"
    assert result.bundle.impact.affected_files == []
    assert any("no seed file" in item for item in result.bundle.impact.uncertainties)


def test_repository_without_tests_is_flagged_in_the_plan(repo_without_tests):
    repo_analysis = analyze_repository(repo_without_tests)
    issue_analysis = analyze_issue("Fix the login helper rejecting valid users", repo_analysis)
    impact = analyze_impact(issue_analysis, repo_analysis)

    assert impact.related_tests == []
    assert any("no existing test" in item for item in impact.uncertainties)


def test_repository_without_tests_falls_back_to_the_full_suite(repo_without_tests):
    selection = select_tests(["src/auth.py"], analyze_repository(repo_without_tests))

    assert selection.selected_tests == []
    assert selection.confidence == 0.0


def test_repository_without_tests_still_completes_a_run(repo_without_tests, tmp_path):
    result = run(
        str(repo_without_tests),
        "Fix the login helper rejecting valid users",
        RepoPilotConfig(approval_mode="auto"),
        output_dir=tmp_path / "out",
        agent_factory=lambda path: demo.ScriptedAgent(
            path, {"src/auth.py": "def login(user):\n    return user is not None\n"}, "fixed"
        ),
        test_runner=_passing,
    )

    assert result.report.status == "VERIFIED"
    assert "src/auth.py" in result.changes.all_files
