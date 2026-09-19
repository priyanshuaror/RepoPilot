"""Tests for repopilot.reporting.report."""

import json

from repopilot.reporting.report import (
    STATUS_FAILED,
    STATUS_PLANNED,
    STATUS_REJECTED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    RunReport,
    determine_status,
)


def _report() -> RunReport:
    return RunReport(
        run_id="20260101-120000",
        issue="Fix JWT expiration handling",
        repository={"name": "example/project", "commit_sha": "abc1234", "branch": "main"},
        impact={"affected_files": [{"path": "src/auth/jwt.py"}, {"path": "src/middleware/auth.py"}]},
        approval={"approved": True, "mode": "interactive"},
        targeted_tests={"passed": 18, "failed": 0, "errors": 0, "parsed": True},
        broader_tests={"passed": 142, "failed": 0, "errors": 0, "parsed": True},
        repair={"attempt_count": 1, "stop_reason": "tests_passed"},
        changes={
            "all_files": ["src/auth/jwt.py", "tests/test_auth.py"],
            "stats": {"files_changed": 2, "insertions": 20, "deletions": 4},
            "warnings": [],
        },
        status=STATUS_VERIFIED,
    )


def test_markdown_contains_the_headline_facts():
    markdown = _report().to_markdown()

    assert "# RepoPilot Report" in markdown
    assert "example/project" in markdown
    assert "abc1234" in markdown
    assert "src/auth/jwt.py" in markdown
    assert "18 passed" in markdown
    assert "142 passed" in markdown
    assert "Repair attempts: 1" in markdown
    assert "Final status: VERIFIED" in markdown
    assert "- None" in markdown  # no warnings


def test_markdown_lists_warnings_when_present():
    report = _report()
    report.warnings = ["agent run reported an error: boom"]
    assert "boom" in report.to_markdown()


def test_markdown_handles_a_run_that_never_executed():
    report = RunReport(run_id="r", issue="i", status=STATUS_REJECTED)
    markdown = report.to_markdown()

    assert "not reached" in markdown
    assert "not run" in markdown
    assert "- (none)" in markdown


def test_report_json_roundtrip():
    data = json.loads(_report().to_json())
    assert data["status"] == STATUS_VERIFIED
    assert data["repository"]["name"] == "example/project"


def test_save_writes_report_json(tmp_path):
    path = _report().save(tmp_path / "nested" / "report.json")
    assert path.is_file()
    assert json.loads(path.read_text())["run_id"] == "20260101-120000"


def test_determine_status_rejected():
    status = determine_status(approved=False, executed=False, targeted_passed=None, broader_passed=None)
    assert status == STATUS_REJECTED


def test_determine_status_planned_when_not_executed():
    status = determine_status(approved=True, executed=False, targeted_passed=None, broader_passed=None)
    assert status == STATUS_PLANNED


def test_determine_status_unverified_when_no_tests_ran():
    status = determine_status(approved=True, executed=True, targeted_passed=None, broader_passed=None)
    assert status == STATUS_UNVERIFIED


def test_determine_status_failed_on_targeted_failure():
    assert determine_status(approved=True, executed=True, targeted_passed=False, broader_passed=None) == STATUS_FAILED


def test_determine_status_failed_when_broader_suite_regresses():
    assert determine_status(approved=True, executed=True, targeted_passed=True, broader_passed=False) == STATUS_FAILED


def test_determine_status_verified_requires_passing_tests():
    assert determine_status(approved=True, executed=True, targeted_passed=True, broader_passed=True) == STATUS_VERIFIED
