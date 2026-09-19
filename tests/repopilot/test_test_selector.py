"""Tests for repopilot.testing.test_selector."""

import json

from repopilot.analysis.repository import analyze_repository
from repopilot.testing.test_selector import select_tests


def test_selects_the_test_that_imports_the_changed_module(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["src/auth/jwt.py"], repo)

    assert "tests/test_auth.py" in selection.paths
    assert "tests/test_billing.py" not in selection.paths


def test_selection_explains_each_choice(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["src/auth/jwt.py"], repo)

    assert all(test.reasons for test in selection.selected_tests)
    assert selection.reason


def test_changed_test_file_is_selected(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["tests/test_billing.py"], repo)

    top = selection.selected_tests[0]
    assert top.path == "tests/test_billing.py"
    assert any("itself changed" in reason for reason in top.reasons)


def test_pytest_command_targets_only_selected_tests(sample_repo):
    repo = analyze_repository(sample_repo)
    command = select_tests(["src/auth/jwt.py"], repo).pytest_command()

    assert "tests/test_auth.py" in command
    assert "tests/test_billing.py" not in command


def test_no_match_falls_back_to_the_full_suite(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["docs/unrelated.md"], repo)

    assert selection.selected_tests == []
    assert selection.confidence == 0.0
    assert "regression test is probably needed" in selection.reason
    assert selection.pytest_command() == "python -m pytest"


def test_confidence_is_bounded(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["src/auth/jwt.py"], repo)
    assert 0.0 < selection.confidence <= 0.85


def test_max_tests_is_respected(sample_repo):
    repo = analyze_repository(sample_repo)
    selection = select_tests(["src/auth/jwt.py", "src/billing/invoice.py"], repo, max_tests=1)
    assert len(selection.selected_tests) == 1


def test_selection_is_json_serializable(sample_repo):
    repo = analyze_repository(sample_repo)
    json.dumps(select_tests(["src/auth/jwt.py"], repo).to_dict())
