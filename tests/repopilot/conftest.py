"""Shared fixtures for the RepoPilot test suite.

Everything here is local and deterministic: no network, no GitHub API, no LLM.
``sample_repo`` builds a tiny but realistic project on disk (source package,
tests, pyproject) so the analysis stages can be exercised against a real tree
rather than mocks of a filesystem.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SAMPLE_FILES: dict[str, str] = {
    "pyproject.toml": '[project]\nname = "sample"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    "README.md": "# sample\n",
    "src/auth/__init__.py": "",
    "src/auth/jwt.py": (
        "import time\n\n\n"
        "def decode_token(token):\n"
        "    \"\"\"Decode a JWT and check its expiration.\"\"\"\n"
        "    return {'exp': time.time() + 60}\n\n\n"
        "def is_expired(token):\n"
        "    return decode_token(token)['exp'] < time.time()\n"
    ),
    "src/middleware/__init__.py": "",
    "src/middleware/auth.py": (
        "from auth.jwt import is_expired\n\n\n"
        "def require_auth(token):\n"
        "    if is_expired(token):\n"
        "        raise PermissionError('token expired')\n"
        "    return True\n"
    ),
    "src/billing/invoice.py": "def total(items):\n    return sum(items)\n",
    "tests/test_auth.py": (
        "from auth.jwt import is_expired\n\n\n"
        "def test_is_expired():\n"
        "    assert is_expired('tok') is False\n"
    ),
    "tests/test_billing.py": (
        "from billing.invoice import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"
    ),
    "node_modules/junk/index.js": "module.exports = {};\n",
    "build/artifact.bin": "not source\n",
}


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A small, realistic Python project (not a git repo)."""
    return _write_tree(tmp_path / "sample", SAMPLE_FILES)


def init_git_repo(path: Path) -> Path:
    """Turn ``path`` into a git repo with one commit. Used by several tests."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True, capture_output=True)
    return path


@pytest.fixture
def sample_git_repo(sample_repo: Path) -> Path:
    """The sample project, committed to git, ready for diff/verification tests."""
    return init_git_repo(sample_repo)


@pytest.fixture
def git_init():
    """Expose :func:`init_git_repo` to tests that build their own repositories."""
    return init_git_repo


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """A git repository with no files beyond an empty initial commit."""
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial commit"], cwd=repo, check=True, capture_output=True
    )
    return repo


@pytest.fixture
def repo_without_tests(tmp_path: Path) -> Path:
    """A Python project that has source files but no tests and no test runner."""
    repo = tmp_path / "untested"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "auth.py").write_text("def login(user):\n    return bool(user)\n", encoding="utf-8")
    (repo / "README.md").write_text("# untested\n", encoding="utf-8")
    return init_git_repo(repo)
