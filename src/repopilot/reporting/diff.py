"""
After the agent finishes working inside a cloned repo, this module answers the
question "what did it actually change?" by shelling out to `git diff`.

Kept separate from the agent/CLI code so it's independently testable and so it
can later be reused by a dashboard.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


def _untracked_files(repo_path: Path) -> list[str]:
    """New files git does not track yet, honouring .gitignore."""
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=Path(repo_path),
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_diff(repo_path: Path) -> str:
    """Return the unified diff of all uncommitted changes in `repo_path`.

    Includes both tracked-file modifications and brand-new (untracked) files,
    since an agent fixing an issue will often create new files (e.g. new tests).
    """
    repo_path = Path(repo_path)

    # Tracked-file changes.
    tracked = subprocess.run(
        ["git", "diff"], cwd=repo_path, capture_output=True, text=True
    ).stdout

    # New files that git doesn't know about yet: diff them against the null
    # device (os.devnull, so this also works on Windows) to get the same
    # unified-diff format.
    untracked_files = _untracked_files(repo_path)

    untracked_diffs = []
    for rel_path in untracked_files:
        result = subprocess.run(
            ["git", "diff", "--no-index", os.devnull, rel_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        # `git diff --no-index` exits 1 when it found a difference (which is what we want).
        if result.stdout:
            untracked_diffs.append(result.stdout)

    return tracked + "".join(untracked_diffs)


def get_changed_files(repo_path: Path) -> list[str]:
    """Return a short list of file paths the agent touched (modified + new)."""
    repo_path = Path(repo_path)
    modified = subprocess.run(
        ["git", "diff", "--name-only"], cwd=repo_path, capture_output=True, text=True
    ).stdout.splitlines()
    untracked = _untracked_files(repo_path)
    return sorted({*modified, *untracked} - {""})


def save_diff(repo_path: Path, output_path: Path) -> Path:
    """Write the diff to `output_path` and return it. Creates parent dirs as needed."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(get_diff(repo_path), encoding="utf-8")
    return output_path


@dataclass
class DiffStats:
    """Counts for the final report's diff section."""

    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChangeSummary:
    """A structured view of everything the agent changed in the checkout."""

    modified_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    stats: DiffStats = field(default_factory=DiffStats)
    unexpected_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diff: str = ""

    @property
    def all_files(self) -> list[str]:
        return sorted({*self.modified_files, *self.created_files, *self.deleted_files})

    def to_dict(self) -> dict:
        return {
            "modified_files": self.modified_files,
            "created_files": self.created_files,
            "deleted_files": self.deleted_files,
            "all_files": self.all_files,
            "stats": self.stats.to_dict(),
            "unexpected_files": self.unexpected_files,
            "warnings": self.warnings,
        }


def get_diff_stats(repo_path: Path) -> DiffStats:
    """Parse `git diff --numstat` into insertion/deletion counts.

    Untracked (newly created) files are counted by their line count, since git
    does not report them in numstat.
    """
    repo_path = Path(repo_path)
    stats = DiffStats()
    numstat = subprocess.run(
        ["git", "diff", "--numstat"], cwd=repo_path, capture_output=True, text=True
    ).stdout
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed = parts[0], parts[1]
        stats.files_changed += 1
        stats.insertions += int(added) if added.isdigit() else 0
        stats.deletions += int(removed) if removed.isdigit() else 0

    for relative in _untracked_files(repo_path):
        stats.files_changed += 1
        try:
            stats.insertions += len((repo_path / relative).read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            continue
    return stats


def get_deleted_files(repo_path: Path) -> list[str]:
    """Files the agent removed from the working tree."""
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=Path(repo_path), capture_output=True, text=True
    ).stdout
    deleted = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        status, _, path = line[:2], line[2:3], line[3:].strip()
        if "D" in status:
            deleted.append(path.strip('"'))
    return sorted(deleted)


def get_created_files(repo_path: Path) -> list[str]:
    """Newly created (untracked) files."""
    return sorted(_untracked_files(Path(repo_path)))


def get_modified_files(repo_path: Path) -> list[str]:
    """Tracked files whose contents changed."""
    output = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=M"], cwd=Path(repo_path), capture_output=True, text=True
    ).stdout
    return sorted(line for line in output.splitlines() if line.strip())


def summarize_changes(repo_path: Path, expected_paths: Iterable[str] = ()) -> ChangeSummary:
    """Build a :class:`ChangeSummary`, flagging changes outside the expected area.

    ``expected_paths`` is normally the impact analysis's blast radius. Anything
    touched outside it (or outside its directories) is reported as a warning -
    not an error, since the prediction is only an estimate, but always something
    a reviewer should look at.
    """
    repo_path = Path(repo_path)
    summary = ChangeSummary(
        modified_files=get_modified_files(repo_path),
        created_files=get_created_files(repo_path),
        deleted_files=get_deleted_files(repo_path),
        stats=get_diff_stats(repo_path),
        diff=get_diff(repo_path),
    )

    expected = {path for path in expected_paths if path}
    if expected:
        expected_dirs = {str(Path(path).parent) for path in expected}
        summary.unexpected_files = [
            path
            for path in summary.all_files
            if path not in expected and str(Path(path).parent) not in expected_dirs
        ]
        if summary.unexpected_files:
            summary.warnings.append(
                f"{len(summary.unexpected_files)} file(s) changed outside the predicted impact area: "
                + ", ".join(summary.unexpected_files[:5])
            )
    if summary.deleted_files:
        summary.warnings.append(f"{len(summary.deleted_files)} file(s) were deleted")
    if not summary.all_files:
        summary.warnings.append("the agent produced no changes at all")
    return summary
