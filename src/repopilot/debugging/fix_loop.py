"""
Phase 11 - the bounded verification / repair loop.

    change -> targeted tests -> pass? -> broader tests -> report
                             \\-> fail? -> failure analysis -> bounded fix -> re-test

The safeguards are the point of this module, not an afterthought. An agent that
can edit code and re-run tests forever is a liability, so the loop stops on:

* ``max_attempts`` repair iterations (hard cap, from config);
* a wall-clock budget;
* the same failure signature twice in a row (the fix is not converging);
* a failure classified as environmental (not the agent's to repair).

Both the "run the tests" and the "attempt a fix" steps are injected callables,
so the loop's control flow is fully testable without an LLM or a real test suite.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from repopilot.debugging.failure_analyzer import FailureAnalysis, analyze_failure
from repopilot.testing.verification import TestResult

#: Runs the tests and returns a structured result.
TestRunnerFn = Callable[[], TestResult]
#: Attempts one repair given the latest failure analysis; returns a short note on
#: what it did (typically the agent's submission text).
FixFn = Callable[[FailureAnalysis, TestResult], str]

STOP_REASONS = (
    "tests_passed",
    "max_attempts_reached",
    "repeated_identical_failure",
    "environmental_failure",
    "unrepairable_failure",
    "time_budget_exhausted",
    "fix_step_failed",
)


@dataclass
class FixAttempt:
    """One iteration through the loop."""

    attempt: int
    failure: dict[str, Any]
    action: str
    result_summary: str
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixLoopOutcome:
    """The result of the whole repair loop."""

    verified: bool
    attempts: list[FixAttempt] = field(default_factory=list)
    stop_reason: str = ""
    final_result: TestResult | None = None
    final_analysis: FailureAnalysis | None = None
    elapsed: float = 0.0

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "stop_reason": self.stop_reason,
            "attempt_count": self.attempt_count,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "final_result": self.final_result.to_dict() if self.final_result else None,
            "final_analysis": self.final_analysis.to_dict() if self.final_analysis else None,
            "elapsed": round(self.elapsed, 2),
        }


def failure_signature(result: TestResult) -> str:
    """A stable fingerprint of a failure, used to detect a loop going nowhere.

    Based on the failing test names plus the exit code - not on the full output,
    which contains timings and paths that change between otherwise identical runs.
    """
    payload = f"{result.exit_code}|{sorted(result.failures)}|{result.failed}|{result.errors}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def run_fix_loop(
    run_tests: TestRunnerFn,
    attempt_fix: FixFn,
    *,
    max_attempts: int = 3,
    changed_files: list[str] | None = None,
    issue: str = "",
    time_budget: float | None = None,
) -> FixLoopOutcome:
    """Test, and if it fails, analyse and repair - up to ``max_attempts`` times.

    Returns a :class:`FixLoopOutcome` recording every attempt and the exact
    reason the loop stopped, so the final report can be audited rather than
    trusted.
    """
    started = time.monotonic()
    outcome = FixLoopOutcome(verified=False)
    changed_files = changed_files or []
    previous_signature: str | None = None

    result = run_tests()
    outcome.final_result = result

    while True:
        if result.success:
            outcome.verified = True
            outcome.stop_reason = "tests_passed"
            break

        analysis = analyze_failure(result, changed_files=changed_files, issue=issue)
        outcome.final_analysis = analysis

        if analysis.is_environmental:
            outcome.stop_reason = "environmental_failure"
            break
        if not analysis.repairable:
            outcome.stop_reason = "unrepairable_failure"
            break

        signature = failure_signature(result)
        if signature == previous_signature:
            outcome.stop_reason = "repeated_identical_failure"
            break
        previous_signature = signature

        if len(outcome.attempts) >= max_attempts:
            outcome.stop_reason = "max_attempts_reached"
            break
        if time_budget is not None and (time.monotonic() - started) >= time_budget:
            outcome.stop_reason = "time_budget_exhausted"
            break

        attempt_number = len(outcome.attempts) + 1
        try:
            action = attempt_fix(analysis, result)
        except Exception as exc:  # a broken fix step must not take the run down
            outcome.attempts.append(
                FixAttempt(
                    attempt=attempt_number,
                    failure=analysis.to_dict(),
                    action=f"fix step raised {type(exc).__name__}: {exc}",
                    result_summary=result.summary(),
                    passed=False,
                )
            )
            outcome.stop_reason = "fix_step_failed"
            break

        result = run_tests()
        outcome.final_result = result
        outcome.attempts.append(
            FixAttempt(
                attempt=attempt_number,
                failure=analysis.to_dict(),
                action=action,
                result_summary=result.summary(),
                passed=result.success,
            )
        )

    outcome.elapsed = time.monotonic() - started
    return outcome
