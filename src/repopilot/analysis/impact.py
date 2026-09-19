"""
Phase 4 - change impact analysis.

Given an issue and a repository map, estimate the *blast radius*: which files are
likely to change, which files depend on those, which tests cover them, and what
user-visible behaviour could move as a result.

Every entry carries the reasons it was included, because an impact analysis that
cannot explain itself is not reviewable - and reviewability is the whole point of
the approval checkpoint that consumes this output.

The graph work here is Python-specific (module imports resolved against the
checkout). For other languages the analysis degrades gracefully to name/keyword
signals rather than pretending to a precision it does not have.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repopilot.analysis.issue import IssueAnalysis
from repopilot.analysis.repository import RepositoryAnalysis, read_text_safely

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(?P<from>[.\w]+)\s+import|import\s+(?P<import>[.\w]+(?:\s*,\s*[.\w]+)*))",
    re.MULTILINE,
)

#: Cap on how far a seed file's dependents are followed.
DEFAULT_MAX_DEPTH = 2
#: Cap on how many files an impact report may list, so it never degenerates into
#: "every file in the repository".
DEFAULT_MAX_FILES = 25


@dataclass
class ImpactedFile:
    """One file inside the blast radius, with its justification."""

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)
    kind: str = "source"  # source | test | config
    distance: int = 0  # 0 = directly implicated, 1+ = reached via dependents

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactAnalysis:
    """The estimated blast radius of an issue."""

    issue: str
    affected_files: list[ImpactedFile] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    potentially_affected_behavior: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def paths(self) -> list[str]:
        return [item.path for item in self.affected_files]

    @property
    def source_paths(self) -> list[str]:
        return [item.path for item in self.affected_files if item.kind != "test"]

    def explain(self) -> str:
        """Human-readable rendering, used by the CLI and the final report."""
        lines = [f"Blast radius for: {self.issue}", ""]
        lines.append(f"Affected files ({len(self.affected_files)}):")
        for item in self.affected_files:
            reason = "; ".join(item.reasons[:3]) or "keyword match"
            lines.append(f"  - {item.path}  [{item.kind}, score {item.score}] <- {reason}")
        lines.append("")
        lines.append(f"Affected components: {', '.join(self.affected_components) or '(none identified)'}")
        lines.append(
            f"Potentially affected behavior: {', '.join(self.potentially_affected_behavior) or '(none identified)'}"
        )
        if self.uncertainties:
            lines.append("")
            lines.append("Uncertainties:")
            lines.extend(f"  - {item}" for item in self.uncertainties)
        lines.append("")
        lines.append(f"Confidence: {self.confidence}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "affected_files": [item.to_dict() for item in self.affected_files],
            "affected_components": self.affected_components,
            "potentially_affected_behavior": self.potentially_affected_behavior,
            "related_tests": self.related_tests,
            "uncertainties": self.uncertainties,
            "confidence": self.confidence,
        }


def module_name_for(relative_path: str) -> str:
    """Best-effort dotted module name for a Python file inside the checkout.

    ``src/auth/jwt.py`` -> ``auth.jwt`` (a leading ``src``/``lib`` is dropped,
    since it is a layout convention rather than part of the import path).
    """
    path = Path(relative_path)
    parts = list(path.with_suffix("").parts)
    if parts and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def extract_imports(text: str) -> set[str]:
    """Return the dotted module names imported by a Python source string."""
    modules: set[str] = set()
    for match in _IMPORT_RE.finditer(text):
        raw = match.group("from") or match.group("import") or ""
        for chunk in raw.split(","):
            name = chunk.strip().lstrip(".")
            if name:
                modules.add(name)
    return modules


def build_import_index(
    repo_analysis: RepositoryAnalysis, paths: Iterable[str]
) -> dict[str, set[str]]:
    """Map every Python file to the set of modules it imports.

    Only Python files are indexed; other languages contribute name-based signals
    instead of a dependency graph.
    """
    index: dict[str, set[str]] = {}
    for relative in paths:
        if not relative.endswith(".py"):
            continue
        text = read_text_safely(repo_analysis.root / relative)
        if text:
            index[relative] = extract_imports(text)
    return index


def _importers_of(module: str, import_index: dict[str, set[str]]) -> list[str]:
    """Files whose imports resolve to ``module`` (or a submodule of it)."""
    if not module:
        return []
    tail = module.rsplit(".", 1)[-1]
    found = []
    for path, imported in import_index.items():
        for name in imported:
            if name == module or name.startswith(module + ".") or name.endswith("." + tail) or name == tail:
                found.append(path)
                break
    return found


def _classify(relative: str, repo_analysis: RepositoryAnalysis) -> str:
    if relative in set(repo_analysis.test_files):
        return "test"
    if relative in set(repo_analysis.config_files):
        return "config"
    return "source"


def _component_for(relative: str) -> str:
    """The component label for a path: its owning package directory, or its stem."""
    parts = Path(relative).parts
    meaningful = [part for part in parts[:-1] if part not in {"src", "lib", "tests", "test"}]
    return meaningful[-1] if meaningful else Path(relative).stem


def analyze_impact(
    issue_analysis: IssueAnalysis,
    repo_analysis: RepositoryAnalysis,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_files: int = DEFAULT_MAX_FILES,
) -> ImpactAnalysis:
    """Estimate the blast radius of ``issue_analysis`` against ``repo_analysis``.

    Signals used, in decreasing weight:

    1. files the issue named explicitly, or whose name/path matches its keywords
       (seeded from :func:`repopilot.analysis.issue.analyze_issue`);
    2. Python files that import a seed file's module (reverse dependency edges,
       followed up to ``max_depth`` hops, with a decaying score);
    3. tests that import or name a seed module.
    """
    impact = ImpactAnalysis(issue=issue_analysis.issue)
    collected: dict[str, ImpactedFile] = {}

    def add(path: str, score: float, reason: str, distance: int) -> None:
        existing = collected.get(path)
        if existing is None:
            collected[path] = ImpactedFile(
                path=path,
                score=round(score, 2),
                reasons=[reason],
                kind=_classify(path, repo_analysis),
                distance=distance,
            )
        else:
            existing.score = round(existing.score + score, 2)
            existing.distance = min(existing.distance, distance)
            if reason not in existing.reasons:
                existing.reasons.append(reason)

    # (1) Seeds: the issue analyzer's top candidates.
    seeds = issue_analysis.candidate_files[: max(3, max_files // 4)]
    for lead in seeds:
        add(lead.path, lead.score, lead.reasons[0] if lead.reasons else "matched issue keywords", 0)

    if not seeds:
        impact.uncertainties.append(
            "no seed file could be identified from the issue text; the blast radius below is unreliable"
        )

    # (2) Reverse dependencies, breadth-first, with a decaying score.
    searchable = list(repo_analysis.source_files) + list(repo_analysis.test_files)
    import_index = build_import_index(repo_analysis, searchable)

    frontier = [lead.path for lead in seeds]
    seen = set(frontier)
    for depth in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for path in frontier:
            module = module_name_for(path)
            for importer in _importers_of(module, import_index):
                if importer == path:
                    continue
                add(importer, 2.0 / depth, f"imports '{module}' (depth {depth})", depth)
                if importer not in seen:
                    seen.add(importer)
                    next_frontier.append(importer)
        frontier = next_frontier
        if not frontier:
            break

    # (3) Tests that the issue analyzer already liked.
    for lead in issue_analysis.candidate_tests[:5]:
        add(lead.path, lead.score * 0.5, lead.reasons[0] if lead.reasons else "test matches issue keywords", 1)

    ranked = sorted(collected.values(), key=lambda item: (-item.score, item.distance, item.path))
    impact.affected_files = ranked[:max_files]
    if len(ranked) > max_files:
        impact.uncertainties.append(
            f"{len(ranked) - max_files} further files matched weakly and were dropped "
            "to keep the blast radius reviewable"
        )

    impact.related_tests = [item.path for item in impact.affected_files if item.kind == "test"]
    impact.affected_components = sorted(
        {_component_for(item.path) for item in impact.affected_files if item.kind == "source"}
    )
    impact.potentially_affected_behavior = _behavior_notes(impact, issue_analysis, repo_analysis)
    impact.confidence = _confidence(impact, issue_analysis)

    if not impact.related_tests:
        impact.uncertainties.append("no existing test covers the affected files; verification will need a new test")
    return impact


def _behavior_notes(
    impact: ImpactAnalysis, issue_analysis: IssueAnalysis, repo_analysis: RepositoryAnalysis
) -> list[str]:
    """Plain-language notes about what could move if these files change."""
    notes: list[str] = []
    for subsystem in issue_analysis.subsystems:
        notes.append(f"{subsystem} behaviour")
    entry_points = set(repo_analysis.entry_points)
    for item in impact.affected_files:
        if item.path in entry_points:
            notes.append(f"application entry point {item.path}")
    if impact.related_tests:
        notes.append(f"{len(impact.related_tests)} existing test file(s) covering these paths")
    indirect = [item for item in impact.affected_files if item.distance > 0 and item.kind == "source"]
    if indirect:
        notes.append(f"{len(indirect)} downstream module(s) that import the changed code")
    ordered: dict[str, None] = {}
    for note in notes:
        ordered.setdefault(note, None)
    return list(ordered)


def _confidence(impact: ImpactAnalysis, issue_analysis: IssueAnalysis) -> float:
    """Blunt 0-1 confidence. Never returns 1.0 - this is always an estimate."""
    if not impact.affected_files:
        return 0.0
    score = issue_analysis.confidence
    if impact.related_tests:
        score += 0.1
    if len(impact.affected_files) > 15:
        score -= 0.15  # a very wide radius means we failed to narrow anything down
    return round(max(0.0, min(0.85, score)), 2)


def unexpected_changes(changed_files: Iterable[str], impact: ImpactAnalysis) -> list[str]:
    """Changed files that fall outside the predicted blast radius.

    Used by the final report to warn a reviewer that the agent touched something
    nobody predicted - not necessarily wrong, but always worth a human look.
    """
    expected_dirs = {str(Path(path).parent) for path in impact.paths}
    expected = set(impact.paths)
    unexpected = []
    for path in changed_files:
        if path in expected:
            continue
        if str(Path(path).parent) in expected_dirs:
            continue
        unexpected.append(path)
    return sorted(unexpected)
