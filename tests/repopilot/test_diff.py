"""Tests for repopilot.reporting.diff."""

import subprocess

import pytest

from repopilot.reporting.diff import get_changed_files, get_diff, save_diff


@pytest.fixture
def git_repo(tmp_path):
    """A real local git repo with one committed file, ready to be modified."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


def test_get_diff_empty_when_nothing_changed(git_repo):
    assert get_diff(git_repo).strip() == ""


def test_get_diff_shows_modified_file(git_repo):
    (git_repo / "app.py").write_text("value = 2\n")
    diff_text = get_diff(git_repo)
    assert "app.py" in diff_text
    assert "-value = 1" in diff_text
    assert "+value = 2" in diff_text


def test_get_diff_shows_new_untracked_file(git_repo):
    (git_repo / "new_feature.py").write_text("def new_feature():\n    return 42\n")
    diff_text = get_diff(git_repo)
    assert "new_feature.py" in diff_text
    assert "new_feature" in diff_text


def test_get_changed_files_lists_modified_and_new(git_repo):
    (git_repo / "app.py").write_text("value = 2\n")
    (git_repo / "extra.py").write_text("x = 1\n")

    changed = get_changed_files(git_repo)
    assert changed == sorted(["app.py", "extra.py"])


def test_save_diff_writes_file(git_repo, tmp_path):
    (git_repo / "app.py").write_text("value = 999\n")
    output_path = tmp_path / "out" / "changes.diff"

    result_path = save_diff(git_repo, output_path)

    assert result_path == output_path
    assert output_path.exists()
    assert "value = 999" in output_path.read_text()
