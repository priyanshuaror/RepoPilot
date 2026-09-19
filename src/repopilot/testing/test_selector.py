"""
Phase 8 - test selection.

Running the whole suite first is slow and, worse, drowns the one signal that
matters: did *this* change do what it was supposed to? RepoPilot therefore picks
a targeted set of tests first, and only widens to the full suite once those pass.

Selection is name/import based and explains itself, so a reviewer can see why a
test was chosen - and see when nothing relevant was found, which is a finding in
its own right.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repopilot.analysis.impact import module_name_for
from repopilot.analysis.repository import RepositoryAnalysis, read_text_safely

DEFAULT_MAX_TESTS = 12


@dataclass
class SelectedTest:
    """One chosen test file, with why it was chosen."""

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestSelection:
    """The targeted test set for a change."""

    selected_tests: list[SelectedTest] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    fallback_command: str = "python -m pytest"

    @property
    def paths(self) -> list[str]:
        return [test.path for test in self.selected_tests]

    def pytest_command(self, *, base: str = "python -m pytest") -> str:
        """The command that runs exactly the selected tests (or the whole suite)."""
        if not self.selected_tests:
            return self.fallback_command
        return f"{base} {' '.join(self.paths)} -q"

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_tests": [test.to_dict() for test in self.selected_tests],
            "reason": self.reason,
            "confidence": self.confidence,
            "command": self.pytest_command(),
        }


def _stem_variants(relative_path: str) -> set[str]:
    """Names a test file might use to refer to ``relative_path``."""
    stem = Path(relative_path).stem
    variants = {stem, stem.replace("_", ""), f"test_{stem}", f"{stem}_test"}
    return {item.lower() for item in variants if item}


def select_tests(
    changed_files: Iterable[str],
    repo_analysis: RepositoryAnalysis,
    *,
    issue_keywords: Iterable[str] = (),
    max_tests: int = DEFAULT_MAX_TESTS,
) -> TestSelection:
    """Pick the tests most likely to exercise ``changed_files``.

    Signals, in decreasing weight: a test that imports the changed module; a test
    whose filename mirrors the changed file's name; a test living in the mirrored
    directory; a test mentioning the issue's keywords.
    """
    changed = [path for path in changed_files if path]
    keywords = [word.lower() for word in issue_keywords]
    scores: dict[str, SelectedTest] = {}

    def add(test_path: str, score: float, reason: str) -> None:
        entry = scores.get(test_path)
        if entry is None:
            scores[test_path] = SelectedTest(path=test_path, score=round(score, 2), reasons=[reason])
        else:
            entry.score = round(entry.score + score, 2)
            if reason not in entry.reasons:
                entry.reasons.append(reason)

    changed_modules = {path: module_name_for(path) for path in changed if path.endswith(".py")}
    changed_dirs = {str(Path(path).parent) for path in changed}

    for test_path in repo_analysis.test_files:
        if test_path in changed:
            add(test_path, 5.0, "this test file was itself changed")

        text = read_text_safely(repo_analysis.root / test_path).lower() if test_path.endswith(".py") else ""

        for source_path, module in changed_modules.items():
            tail = module.rsplit(".", 1)[-1]
            if text and (module.lower() in text or re.search(rf"\b{re.escape(tail)}\b", text)):
                add(test_path, 4.0, f"references '{tail}' from {source_path}")

        test_stem = Path(test_path).stem.lower()
        for source_path in changed:
            if test_stem in _stem_variants(source_path):
                add(test_path, 3.0, f"filename mirrors {source_path}")

        parent = str(Path(test_path).parent)
        for directory in changed_dirs:
            tail = Path(directory).name
            if tail and tail in Path(parent).parts:
                add(test_path, 1.5, f"lives in the test directory mirroring {directory}")

        for keyword in keywords:
            if keyword and keyword in test_stem:
                add(test_path, 1.0, f"test name contains issue keyword '{keyword}'")

    ranked = sorted(scores.values(), key=lambda test: (-test.score, test.path))[:max_tests]
    selection = TestSelection(selected_tests=ranked)
    selection.fallback_command = (repo_analysis.test_commands or ["python -m pytest"])[0]

    if ranked:
        selection.reason = (
            f"{len(ranked)} test file(s) reference or mirror the {len(changed)} changed file(s); "
            "these run first, the full suite runs afterwards."
        )
        top = ranked[0].score
        selection.confidence = round(min(0.85, 0.3 + min(top, 8.0) / 16.0), 2)
    else:
        selection.reason = (
            "No existing test could be linked to the changed files. Falling back to the full suite; "
            "a new regression test is probably needed."
        )
        selection.confidence = 0.0
    return selection
