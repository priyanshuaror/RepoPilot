"""
Repository ingestion: turn a GitHub URL (+ optional branch) into a validated
local clone that every later stage of the pipeline works inside of.

Intentionally dependency-free (plain ``git`` via subprocess, no GitPython) so it
is easy to read, easy to mock in tests, and has no import-time cost.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: https / ssh / scp-style git remotes.
_REMOTE_RE = re.compile(
    r"^(?:https?://[^\s]+|git://[^\s]+|ssh://[^\s]+|git@[\w.\-]+:[\w.\-/]+)$",
    re.IGNORECASE,
)


class CloneError(RuntimeError):
    """Raised when git fails: bad URL, no network, private repo, bad branch."""


@dataclass
class ClonedRepo:
    """Everything downstream code needs to know about a repository we ingested."""

    source_url: str
    local_path: Path
    branch: str | None = None
    commit_sha: str | None = None
    commit_subject: str = ""

    @property
    def name(self) -> str:
        """Best-effort ``owner/project`` label for reports."""
        cleaned = self.source_url.rstrip("/").removesuffix(".git")
        parts = [p for p in re.split(r"[/:]", cleaned) if p]
        return "/".join(parts[-2:]) if len(parts) >= 2 else cleaned

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["local_path"] = str(self.local_path)
        data["name"] = self.name
        return data


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def is_plausible_repository_url(url: str) -> bool:
    """Cheap syntactic check before we spend time on a network round trip.

    A local filesystem path holding a git repository also counts, since git can
    clone from one and the unit tests rely on that.
    """
    if not url or not url.strip():
        return False
    url = url.strip()
    if _REMOTE_RE.match(url):
        return True
    candidate = Path(url)
    return candidate.is_dir() and ((candidate / ".git").exists() or (candidate / "HEAD").exists())


def prepare_workspace(path: Path, *, overwrite: bool = False) -> Path:
    """Make sure ``path`` is an empty directory we are allowed to clone into.

    Raises
    ------
    CloneError: if the directory exists, is non-empty, and ``overwrite`` is False.
    """
    path = Path(path)
    if path.exists():
        if any(path.iterdir()):
            if not overwrite:
                raise CloneError(f"Workspace {path} already exists and is not empty.")
            shutil.rmtree(path)
        else:
            return path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clone_repository(
    repo_url: str,
    dest_dir: Path,
    *,
    branch: str | None = None,
    depth: int | None = 1,
    overwrite: bool = False,
) -> ClonedRepo:
    """Clone ``repo_url`` into ``dest_dir`` and return a populated :class:`ClonedRepo`.

    Parameters
    ----------
    repo_url: HTTPS/SSH URL, or a local path (tests clone from a temp repo).
    dest_dir: Where to put the clone. Must not exist, or must be empty, unless
        ``overwrite`` is set.
    branch: Check out this branch instead of the repository's default branch.
    depth: Shallow-clone depth; ``None`` for a full clone (needed if the agent
        should be able to inspect history or older tags).
    overwrite: Delete a non-empty destination first.

    Raises
    ------
    CloneError: on an implausible URL or a non-zero ``git clone``.
    """
    if not is_plausible_repository_url(repo_url):
        raise CloneError(f"{repo_url!r} does not look like a git repository URL or a local git repository.")

    dest_dir = prepare_workspace(Path(dest_dir), overwrite=overwrite)

    command = ["clone"]
    if depth is not None:
        command += ["--depth", str(depth)]
    if branch:
        command += ["--branch", branch]
    command += [repo_url, str(dest_dir)]

    result = _git(command)
    if result.returncode != 0:
        raise CloneError(
            f"Failed to clone {repo_url!r} into {str(dest_dir)!r}.\n"
            f"Command: git {' '.join(command)}\n"
            f"stderr:\n{result.stderr.strip()}"
        )

    return ClonedRepo(
        source_url=repo_url,
        local_path=dest_dir,
        branch=branch or current_branch(dest_dir),
        commit_sha=current_commit_hash(dest_dir),
        commit_subject=current_commit_subject(dest_dir),
    )


def current_commit_hash(repo_path: Path, *, short: bool = True) -> str:
    """Return the commit hash currently checked out at ``repo_path``."""
    args = ["rev-parse", "--short", "HEAD"] if short else ["rev-parse", "HEAD"]
    result = _git(args, cwd=Path(repo_path))
    if result.returncode != 0:
        raise CloneError(f"Could not read HEAD commit in {str(repo_path)!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch(repo_path: Path) -> str | None:
    """Return the checked-out branch name, or None on a detached HEAD."""
    result = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=Path(repo_path))
    name = result.stdout.strip()
    if result.returncode != 0 or not name or name == "HEAD":
        return None
    return name


def current_commit_subject(repo_path: Path) -> str:
    """Return the subject line of HEAD, or an empty string if unavailable."""
    result = _git(["log", "-1", "--pretty=%s"], cwd=Path(repo_path))
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_repository(repo_path: Path) -> None:
    """Raise :class:`CloneError` unless ``repo_path`` is a usable git checkout."""
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise CloneError(f"{repo_path} is not a directory.")
    result = _git(["rev-parse", "--is-inside-work-tree"], cwd=repo_path)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise CloneError(f"{repo_path} is not a git repository.")


def ingest(
    repo_url: str,
    dest_dir: Path,
    *,
    branch: str | None = None,
    depth: int | None = 1,
    overwrite: bool = False,
) -> ClonedRepo:
    """Clone and then validate, so later stages can assume a sane checkout."""
    cloned = clone_repository(repo_url, dest_dir, branch=branch, depth=depth, overwrite=overwrite)
    validate_repository(cloned.local_path)
    return cloned
