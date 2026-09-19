"""Tests for repopilot.ingestion.github_repo.

These tests never touch the network: they create a real local git repo on disk
(git can clone from a local path just as well as from a URL) and clone *that*,
which exercises the exact same code path as cloning from GitHub.
"""

import subprocess

import pytest

from repopilot.ingestion.github_repo import CloneError, clone_repository, current_commit_hash


@pytest.fixture
def local_source_repo(tmp_path):
    """Create a tiny real git repo with one commit, return its path."""
    source = tmp_path / "source_repo"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "hello.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=source, check=True, capture_output=True)
    return source


def test_clone_repository_creates_local_copy(local_source_repo, tmp_path):
    dest = tmp_path / "cloned"
    result = clone_repository(str(local_source_repo), dest, depth=None)

    assert result.local_path == dest
    assert (dest / "hello.py").exists()
    assert (dest / "hello.py").read_text() == "print('hello')\n"


def test_clone_repository_raises_on_bad_url(tmp_path):
    dest = tmp_path / "cloned"
    with pytest.raises(CloneError):
        clone_repository("https://not-a-real-repo.invalid/nothing.git", dest)


def test_current_commit_hash_returns_short_hash(local_source_repo, tmp_path):
    dest = tmp_path / "cloned"
    clone_repository(str(local_source_repo), dest, depth=None)

    commit_hash = current_commit_hash(dest)
    assert len(commit_hash) >= 7  # short hashes are usually 7-8 chars
    assert commit_hash.isalnum()
