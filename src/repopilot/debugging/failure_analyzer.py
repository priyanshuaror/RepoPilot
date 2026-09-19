"""
Phase 10 - failure analysis.

When a test run fails, this module classifies *why* before anything tries to fix
it. That matters for two reasons: a syntax error and an unrelated environment
failure call for completely different responses, and the fix loop must be able to
recognise a failure it cannot repair and stop instead of burning attempts.

Classification is pattern-based over the test output and is explicitly a
*hypothesis*. Nothing here reports certainty it does not have: ``confidence`` is
capped below 1.0 and ``likely_cause`` is always phrased as a candidate.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repopilot.testing.verification import TestResult

#: Failure categories, ordered by how specific they are. First match wins.
FAILURE_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("syntax_error", r"SyntaxError|IndentationError|TabError", "the code does not parse"),
    (
        "import_error",
        r"ModuleNotFoundError|ImportError|cannot import name|No module named",
        "a module or name could not be imported",
    ),
    (
        "dependency_error",
        r"No matching distribution|pip install|Could not find a version|Requirement already",
        "a package is missing from the environment",
    ),
    (
        "collection_error",
        r"ERROR collecting|fixture '[^']+' not found|INTERNALERROR",
        "pytest could not collect or set up the tests",
    ),
    (
        "type_error",
        r"TypeError|AttributeError: '\w+' object has no attribute",
        "a value had the wrong type or shape",
    ),
    ("assertion_failure", r"AssertionError|^E\s+assert", "the code ran but produced the wrong result"),
    (
        "configuration_error",
        r"KeyError: '[A-Z_]+'|environment variable|\.env|config(uration)? (file )?not found",
        "configuration or environment is incomplete",
    ),
    ("timeout", r"timed out|TimeoutExpired|Timeout", "the tests did not finish in time"),
    ("runtime_error", r"Traceback \(most recent call last\)", "the code raised at runtime"),
)

#: Signals that the failure has nothing to do with the change under test.
ENVIRONMENT_SIGNALS = (
    "no matching distribution",
    "connection refused",
    "temporary failure in name resolution",
    "network is unreachable",
    "permission denied",
    "disk quota exceeded",
    "could not resolve host",
)

_FILE_MENTION_RE = re.compile(r"[\w./\-]+\.py")


@dataclass
class FailureAnalysis:
    """A hypothesis about why a test run failed."""

    failure_type: str
    failing_tests: list[str] = field(default_factory=list)
    likely_cause: str = ""
    relevant_files: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    confidence: float = 0.0
    is_environmental: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def repairable(self) -> bool:
        """False for failures the agent has no business trying to patch around."""
        return not self.is_environmental and self.failure_type != "unknown"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["repairable"] = self.repairable
        return data


def _evidence_lines(output: str, pattern: str, limit: int = 3) -> list[str]:
    """The actual output lines that triggered a classification."""
    compiled = re.compile(pattern)
    return [line.strip() for line in output.splitlines() if compiled.search(line)][:limit]


def looks_environmental(output: str) -> bool:
    """True if the output suggests a broken environment rather than broken code."""
    lowered = output.lower()
    return any(signal in lowered for signal in ENVIRONMENT_SIGNALS)


def classify_failure(output: str) -> tuple[str, str, list[str]]:
    """Return ``(failure_type, description, evidence)`` for raw test output."""
    for name, pattern, description in FAILURE_PATTERNS:
        if re.search(pattern, output, re.MULTILINE):
            return name, description, _evidence_lines(output, pattern)
    return "unknown", "the failure mode could not be classified from the output", []


SUGGESTIONS: dict[str, str] = {
    "syntax_error": "Open the file named in the traceback and fix the syntax before re-running anything else.",
    "import_error": "Check the module path and any renamed symbols introduced by the change.",
    "dependency_error": "Install the missing package, or revert the code to avoid the new dependency.",
    "collection_error": "Fix the fixture or conftest problem; no assertions ran yet.",
    "type_error": "Check the types/shape of the values passed at the call site named in the traceback.",
    "assertion_failure": (
        "Compare expected vs actual in the failing assertion - "
        "either the fix or the test's expectation is wrong."
    ),
    "configuration_error": "Supply the missing configuration or environment value the test expects.",
    "timeout": (
        "Narrow the test selection or raise the timeout; "
        "do not 'fix' code on the strength of a timeout."
    ),
    "runtime_error": "Read the traceback bottom-up and fix the raising call.",
    "unknown": "Re-run the failing test in isolation with more verbosity to get a clearer signal.",
}


def analyze_failure(
    result: TestResult,
    *,
    changed_files: Iterable[str] = (),
    issue: str = "",
    trajectory_commands: Iterable[str] = (),
) -> FailureAnalysis:
    """Classify a failing :class:`TestResult` into a :class:`FailureAnalysis`.

    ``changed_files`` and ``trajectory_commands`` are used to decide whether the
    failure points at code the agent actually touched (which raises confidence)
    or at something unrelated (which lowers it, and is worth saying out loud).
    """
    output = result.output or ""
    if result.timed_out:
        failure_type, cause, evidence = "timeout", "the tests did not finish in time", []
    else:
        failure_type, cause, evidence = classify_failure(output)

    environmental = looks_environmental(output)
    mentioned = _FILE_MENTION_RE.findall(output)
    changed = list(changed_files)
    overlapping = [
        path
        for path in changed
        if any(path.endswith(name) or name.endswith(Path(path).name) for name in mentioned)
    ]

    relevant = overlapping or [name for name in dict.fromkeys(mentioned) if not name.startswith(("/usr", "/lib"))][:5]

    confidence = 0.25
    if failure_type != "unknown":
        confidence += 0.25
    if overlapping:
        confidence += 0.25
    if result.failures:
        confidence += 0.1
    if environmental:
        confidence = min(confidence, 0.5)

    analysis = FailureAnalysis(
        failure_type=failure_type,
        failing_tests=list(result.failures),
        likely_cause=cause + (" in code this run changed" if overlapping else ""),
        relevant_files=relevant,
        suggested_fix=SUGGESTIONS.get(failure_type, SUGGESTIONS["unknown"]),
        confidence=round(min(0.85, confidence), 2),
        is_environmental=environmental,
        evidence=evidence,
    )
    if environmental:
        analysis.suggested_fix = (
            "This looks like an environment/infrastructure failure, not a code defect. "
            "RepoPilot will stop rather than patch code to work around it."
        )
    if issue and not overlapping and changed:
        analysis.evidence.append("none of the changed files appear in the failure output")
    commands = list(trajectory_commands)
    if commands:
        analysis.evidence.append(f"{len(commands)} command(s) were executed before this failure")
    return analysis
