"""Tests for the change-summary half of repopilot.reporting.diff.

The original diff tests live in test_diff.py; these cover the structured summary
(created/modified/deleted, stats, and out-of-scope warnings).
"""

import json

from repopilot.reporting.diff import (
    get_created_files,
    get_deleted_files,
    get_diff_stats,
    get_modified_files,
    summarize_changes,
)


def test_classifies_modified_created_and_deleted(sample_git_repo):
    (sample_git_repo / "src" / "auth" / "jwt.py").write_text("# rewritten\n")
    (sample_git_repo / "src" / "auth" / "refresh.py").write_text("def refresh():\n    return None\n")
    (sample_git_repo / "src" / "billing" / "invoice.py").unlink()

    assert "src/auth/jwt.py" in get_modified_files(sample_git_repo)
    assert "src/auth/refresh.py" in get_created_files(sample_git_repo)
    assert "src/billing/invoice.py" in get_deleted_files(sample_git_repo)


def test_diff_stats_count_insertions_and_deletions(sample_git_repo):
    (sample_git_repo / "src" / "billing" / "invoice.py").write_text(
        "def total(items):\n    return sum(items)\n\n\ndef tax(x):\n    return x * 0.2\n"
    )
    stats = get_diff_stats(sample_git_repo)

    assert stats.files_changed >= 1
    assert stats.insertions >= 3


def test_new_files_are_counted_in_stats(sample_git_repo):
    (sample_git_repo / "newmod.py").write_text("a = 1\nb = 2\nc = 3\n")
    stats = get_diff_stats(sample_git_repo)

    assert stats.files_changed >= 1
    assert stats.insertions >= 3


def test_summary_warns_about_changes_outside_the_expected_area(sample_git_repo):
    (sample_git_repo / "src" / "auth" / "jwt.py").write_text("# fix\n")
    (sample_git_repo / "src" / "billing" / "invoice.py").write_text("# unrelated!\n")

    summary = summarize_changes(sample_git_repo, expected_paths=["src/auth/jwt.py"])

    assert summary.unexpected_files == ["src/billing/invoice.py"]
    assert any("outside the predicted impact area" in warning for warning in summary.warnings)


def test_summary_does_not_warn_for_siblings_of_expected_files(sample_git_repo):
    (sample_git_repo / "src" / "auth" / "refresh.py").write_text("x = 1\n")

    summary = summarize_changes(sample_git_repo, expected_paths=["src/auth/jwt.py"])
    assert summary.unexpected_files == []


def test_summary_warns_when_nothing_changed(sample_git_repo):
    summary = summarize_changes(sample_git_repo)
    assert summary.all_files == []
    assert any("no changes at all" in warning for warning in summary.warnings)


def test_summary_warns_about_deletions(sample_git_repo):
    (sample_git_repo / "README.md").unlink()
    summary = summarize_changes(sample_git_repo)
    assert any("deleted" in warning for warning in summary.warnings)


def test_summary_carries_the_diff_text(sample_git_repo):
    (sample_git_repo / "README.md").write_text("# changed\n")
    summary = summarize_changes(sample_git_repo)
    assert "README.md" in summary.diff


def test_summary_is_json_serializable(sample_git_repo):
    (sample_git_repo / "README.md").write_text("# changed\n")
    json.dumps(summarize_changes(sample_git_repo).to_dict())
