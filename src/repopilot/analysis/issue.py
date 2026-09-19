"""
Phase 3 - issue analysis.

Turns a free-text issue ("Fix authentication failure when JWT expires") into a
structured set of leads: keywords, candidate source files, candidate tests, and
a guess at the subsystem involved.

Two rules shape this module:

1. Everything here is deterministic static analysis. Finding out that a
   repository has ``src/auth/jwt.py`` should not cost an LLM call.
2. The result must never pretend to know more than it does. Facts that were
   *read off the repository* are kept separate from *inferred* candidates, and
   anything we could not resolve is recorded in ``uncertainties``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from repopilot.analysis.repository import RepositoryAnalysis, read_text_safely

#: Words that carry no signal about which file is relevant.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be been bug but by can cannot could did do does doesn for from
    get getting had has have how i if in into is issue it its just like make makes may
    more must need not of on only or our out please should so that the their then
    there these they this to try trying up use used using want was we what when where
    some
    which while who why will with would you your error errors fail fails failing failure
    fix fixed fixes problem broken expected actual currently instead happens
    """.split()
)

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_QUOTED_RE = re.compile(r"[`'\"]([A-Za-z_][\w./]{2,})[`'\"]")
_PATH_RE = re.compile(r"\b(?:[\w.\-]+/)+[\w.\-]+\.\w+\b")

#: Rough subsystem vocabulary. Used only to *suggest* a subsystem, never asserted.
SUBSYSTEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "authentication": ("auth", "login", "jwt", "token", "oauth", "session", "credential", "password"),
    "database": ("db", "database", "sql", "query", "orm", "migration", "schema"),
    "api": ("api", "endpoint", "route", "handler", "request", "response", "http"),
    "cli": ("cli", "command", "argparse", "typer", "click", "flag", "option"),
    "parsing": ("parse", "parser", "serialize", "deserialize", "json", "yaml", "encode", "decode"),
    "configuration": ("config", "setting", "environment", "env", "option", "yaml", "toml"),
    "caching": ("cache", "ttl", "expire", "invalidate", "memo"),
    "concurrency": ("thread", "async", "await", "lock", "race", "concurrent", "deadlock"),
    "logging": ("log", "logger", "logging", "trace", "verbose"),
}


@dataclass
class FileLead:
    """A candidate file, with the reasons it was surfaced.

    ``score`` is an internal relevance number, not a probability: it exists to
    rank leads, and is reported only so a human can see the ordering is not
    arbitrary.
    """

    path: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IssueAnalysis:
    """Structured leads extracted from an issue description.

    ``detected_facts`` are things read directly off the repository or the issue
    text (an explicit path, a quoted symbol). ``candidate_files`` are *guesses*.
    ``uncertainties`` records what RepoPilot could not determine.
    """

    issue: str
    keywords: list[str] = field(default_factory=list)
    detected_facts: list[str] = field(default_factory=list)
    explicit_paths: list[str] = field(default_factory=list)
    candidate_files: list[FileLead] = field(default_factory=list)
    candidate_tests: list[FileLead] = field(default_factory=list)
    subsystems: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def candidate_paths(self) -> list[str]:
        return [lead.path for lead in self.candidate_files]

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "keywords": self.keywords,
            "detected_facts": self.detected_facts,
            "explicit_paths": self.explicit_paths,
            "candidate_files": [lead.to_dict() for lead in self.candidate_files],
            "candidate_tests": [lead.to_dict() for lead in self.candidate_tests],
            "subsystems": self.subsystems,
            "uncertainties": self.uncertainties,
            "confidence": self.confidence,
        }


def extract_keywords(text: str, *, limit: int = 20) -> list[str]:
    """Pull ranked, de-duplicated identifier-ish keywords out of free text.

    CamelCase and snake_case names are also split, so ``validateToken`` yields
    ``validatetoken``, ``validate`` and ``token``.
    """
    expanded: list[str] = []
    for token in _TOKEN_RE.findall(text):
        expanded.append(token)
        expanded.extend(part for part in _CAMEL_SPLIT_RE.split(token) if len(part) > 2)
        expanded.extend(part for part in token.split("_") if len(part) > 2)

    seen: dict[str, None] = {}
    for token in expanded:
        lowered = token.lower()
        if lowered in STOPWORDS or len(lowered) < 3:
            continue
        seen.setdefault(lowered, None)
    return list(seen)[:limit]


def extract_explicit_paths(text: str) -> list[str]:
    """File paths the issue author wrote down explicitly - these are facts, not guesses."""
    found = list(_PATH_RE.findall(text))
    found += [match for match in _QUOTED_RE.findall(text) if "/" in match or "." in match]
    ordered: dict[str, None] = {}
    for item in found:
        ordered.setdefault(item, None)
    return list(ordered)


def _score_path(relative_path: str, keywords: list[str]) -> tuple[float, list[str]]:
    """Score a path purely on its name/directory components."""
    path = Path(relative_path)
    stem = path.stem.lower()
    parts = [part.lower() for part in path.parts]
    score = 0.0
    reasons: list[str] = []
    for keyword in keywords:
        if keyword == stem:
            score += 3.0
            reasons.append(f"filename matches keyword '{keyword}'")
        elif keyword in stem:
            score += 2.0
            reasons.append(f"filename contains keyword '{keyword}'")
        elif any(keyword in part for part in parts[:-1]):
            score += 1.0
            reasons.append(f"directory path mentions '{keyword}'")
    return score, reasons


def _score_contents(path: Path, keywords: list[str]) -> tuple[float, list[str]]:
    """Score a file on keyword occurrences in its text, capped so one huge file can't dominate."""
    text = read_text_safely(path).lower()
    if not text:
        return 0.0, []
    score = 0.0
    reasons: list[str] = []
    for keyword in keywords:
        occurrences = text.count(keyword)
        if occurrences:
            score += min(occurrences, 5) * 0.4
            reasons.append(f"mentions '{keyword}' {occurrences}x")
    return score, reasons


def detect_subsystems(keywords: list[str]) -> list[str]:
    """Suggest subsystem labels from the issue's vocabulary."""
    hits = [
        name
        for name, vocabulary in SUBSYSTEM_KEYWORDS.items()
        if any(word in keyword or keyword in word for keyword in keywords for word in vocabulary)
    ]
    return sorted(set(hits))


def analyze_issue(
    issue: str,
    repo_analysis: RepositoryAnalysis,
    *,
    max_candidates: int = 10,
    scan_contents: bool = True,
) -> IssueAnalysis:
    """Locate the parts of the repository an issue is most likely about.

    Parameters
    ----------
    issue: Free-text issue or task description.
    repo_analysis: The repository map from :func:`analyze_repository`.
    max_candidates: How many file leads to keep.
    scan_contents: Whether to grep file contents as well as paths. Turning this
        off makes analysis path-only (fast, weaker signal).
    """
    analysis = IssueAnalysis(issue=issue)
    analysis.keywords = extract_keywords(issue)
    analysis.explicit_paths = extract_explicit_paths(issue)
    analysis.subsystems = detect_subsystems(analysis.keywords)

    known_paths = set(repo_analysis.source_files) | set(repo_analysis.test_files) | set(repo_analysis.config_files)
    for mentioned in analysis.explicit_paths:
        matches = [path for path in known_paths if path == mentioned or path.endswith("/" + mentioned)]
        if matches:
            analysis.detected_facts.append(f"issue names an existing path: {matches[0]}")
        elif "/" in mentioned:
            analysis.uncertainties.append(f"issue mentions '{mentioned}', which does not exist in this checkout")

    if not analysis.keywords:
        analysis.uncertainties.append("no usable keywords could be extracted from the issue text")

    source_leads = _rank(repo_analysis, repo_analysis.source_files, analysis, scan_contents)
    test_leads = _rank(repo_analysis, repo_analysis.test_files, analysis, scan_contents)

    analysis.candidate_files = source_leads[:max_candidates]
    analysis.candidate_tests = test_leads[:max_candidates]
    analysis.confidence = _confidence(analysis)

    if not analysis.candidate_files:
        analysis.uncertainties.append("no source file matched the issue keywords; manual triage required")
    if not analysis.candidate_tests:
        analysis.uncertainties.append("no existing test looks related; a new regression test may be needed")
    return analysis


def _rank(
    repo_analysis: RepositoryAnalysis,
    paths: list[str],
    analysis: IssueAnalysis,
    scan_contents: bool,
) -> list[FileLead]:
    """Score and sort a list of repository paths against the issue keywords."""
    leads: list[FileLead] = []
    explicit = set(analysis.explicit_paths)
    for relative in paths:
        score, reasons = _score_path(relative, analysis.keywords)
        if relative in explicit or any(relative.endswith("/" + item) for item in explicit):
            score += 6.0
            reasons.insert(0, "explicitly named in the issue")
        if scan_contents and analysis.keywords:
            content_score, content_reasons = _score_contents(repo_analysis.root / relative, analysis.keywords)
            score += content_score
            reasons.extend(content_reasons[:3])
        if score > 0:
            leads.append(FileLead(path=relative, score=round(score, 2), reasons=reasons))
    leads.sort(key=lambda lead: (-lead.score, lead.path))
    return leads


def _confidence(analysis: IssueAnalysis) -> float:
    """A blunt 0-1 confidence figure, rounded, never claiming certainty.

    High only when the issue named a real path or the top candidate clearly
    outscores the rest; low when everything is a weak keyword match.
    """
    if not analysis.candidate_files:
        return 0.0
    top = analysis.candidate_files[0].score
    runner_up = analysis.candidate_files[1].score if len(analysis.candidate_files) > 1 else 0.0
    separation = (top - runner_up) / top if top else 0.0
    base = 0.75 if analysis.detected_facts else 0.35
    return round(min(0.9, base + 0.25 * separation), 2)
