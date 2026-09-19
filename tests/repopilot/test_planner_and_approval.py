"""Tests for repopilot.planning.planner and repopilot.approval.workflow."""

import json

import pytest

from repopilot.analysis.impact import analyze_impact
from repopilot.analysis.issue import analyze_issue
from repopilot.analysis.repository import analyze_repository
from repopilot.approval.workflow import (
    ApprovalRejected,
    record_decision,
    request_approval,
    require_approval,
)
from repopilot.planning.planner import build_plan

ISSUE = "Fix JWT expiration handling - expired tokens are still accepted."


@pytest.fixture
def plan(sample_repo):
    repo = analyze_repository(sample_repo)
    issue_analysis = analyze_issue(ISSUE, repo)
    impact = analyze_impact(issue_analysis, repo)
    return build_plan(issue_analysis, repo, impact)


# --------------------------------------------------------------------- planner


def test_plan_names_the_affected_files(plan):
    assert "src/auth/jwt.py" in plan.affected_files


def test_plan_has_ordered_steps(plan):
    orders = [step.order for step in plan.steps]
    assert orders == sorted(orders)
    assert len(plan.steps) >= 4


def test_plan_includes_a_regression_test_step(plan):
    assert any("regression test" in step.action.lower() for step in plan.steps)


def test_plan_runs_targeted_before_broader_tests(plan):
    actions = [step.action.lower() for step in plan.steps]
    targeted = next(i for i, action in enumerate(actions) if "targeted" in action)
    broader = next(i for i, action in enumerate(actions) if "broader" in action)
    assert targeted < broader


def test_plan_records_assumptions_and_test_commands(plan):
    assert plan.tests_to_run
    assert plan.assumptions


def test_plan_markdown_is_readable(plan):
    markdown = plan.to_markdown()
    assert "# RepoPilot implementation plan" in markdown
    assert "src/auth/jwt.py" in markdown
    assert "## Steps" in markdown


def test_plan_is_json_serializable(plan):
    json.dumps(plan.to_dict())


def test_plan_without_candidates_admits_uncertainty(sample_repo):
    repo = analyze_repository(sample_repo)
    issue_analysis = analyze_issue("zzzqqq wobblefrotz", repo)
    impact = analyze_impact(issue_analysis, repo)
    empty_plan = build_plan(issue_analysis, repo, impact)

    assert "could not confidently locate" in empty_plan.summary
    assert empty_plan.risks


# -------------------------------------------------------------------- approval


def test_interactive_approval_yes(plan):
    decision = request_approval(plan, mode="interactive", prompt=lambda text: True)
    assert decision.approved is True
    assert decision.decided_by == "human"
    assert decision.decided_at > 0


def test_interactive_approval_no(plan):
    decision = request_approval(plan, mode="interactive", prompt=lambda text: False)
    assert decision.approved is False
    assert "rejected" in decision.reason


def test_prompt_receives_the_rendered_plan(plan):
    seen = {}

    def prompt(text: str) -> bool:
        seen["text"] = text
        return True

    request_approval(plan, mode="interactive", prompt=prompt)
    assert "# RepoPilot implementation plan" in seen["text"]


def test_auto_mode_approves_but_records_that_no_human_reviewed(plan):
    decision = request_approval(plan, mode="auto")
    assert decision.approved is True
    assert decision.decided_by == "config"
    assert "no human" in decision.reason


def test_dry_run_never_approves(plan):
    decision = request_approval(plan, mode="dry-run")
    assert decision.approved is False


def test_unknown_mode_raises(plan):
    with pytest.raises(ValueError):
        request_approval(plan, mode="whatever")


def test_require_approval_raises_on_rejection(plan):
    with pytest.raises(ApprovalRejected):
        require_approval(plan, mode="interactive", prompt=lambda text: False)


def test_record_decision_is_serializable():
    data = record_decision(True, mode="dashboard", reason="clicked approve").to_dict()
    json.dumps(data)
    assert data["approved"] is True
