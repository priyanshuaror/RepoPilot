"""
Phase 9 - structured test execution.

Runs a test command inside the checkout and turns the result into a single
structured object: command, exit code, duration, counts, and the names of the
tests that failed. pytest output is parsed for the counts; for any other runner
the exit code is still authoritative, and the counts are simply unknown rather
than invented.

The subprocess call is injectable (``runner``) so the whole verification path is
testable without actually running a test suite.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from repopilot.testing.pytest_parser import looks_like_pytest_output, parse_pytest_output

#: A runner takes (command, cwd, timeout) and returns (exit_code, output).
RunnerFn = Callable[[str, Path, int], "tuple[int, str]"]

DEFAULT_TIMEOUT = 900
#: Output beyond this many characters is truncated before it reaches the report.
MAX_OUTPUT_CHARS = 200_000


@dataclass
class TestResult:
    """The outcome of one test command.

    ``__test__ = False`` tells pytest not to try to collect this class as a test
    suite. Without it, importing ``TestResult`` into a test module produces a
    ``PytestCollectionWarning`` (pytest collects anything named ``Test*``, then
    complains it has an ``__init__``). It is a marker attribute only - it is not
    annotated, so it is not a dataclass field, and nothing about the public
    behaviour of this class changes.
    """

    __test__ = False

    command: str
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0
    output: str = ""
    failures: list[str] = field(default_factory=list)
    timed_out: bool = False
    parsed: bool = False  # True when we understood the runner's output

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def success(self) -> bool:
        """Exit code is authoritative; parsed counts are a cross-check."""
        if self.timed_out:
            return False
        if self.exit_code != 0:
            return False
        return self.failed == 0 and self.errors == 0

    def summary(self) -> str:
        if self.timed_out:
            return f"TIMEOUT after {self.duration:.1f}s: {self.command}"
        if not self.parsed:
            return f"exit {self.exit_code} in {self.duration:.1f}s: {self.command}"
        return (
            f"{self.passed} passed, {self.failed} failed, {self.errors} errors, "
            f"{self.skipped} skipped in {self.duration:.1f}s"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total"] = self.total
        data["success"] = self.success
        data["summary"] = self.summary()
        return data


def subprocess_runner(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    """Default runner: run ``command`` through the shell inside ``cwd``.

    Note the safety boundary: ``cwd`` is always the ingested checkout, so a test
    command cannot accidentally run against the host project directory. This is
    *not* a sandbox - see the Safety section of the README.
    """
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def run_tests(
    command: str,
    repo_path: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    runner: RunnerFn | None = None,
) -> TestResult:
    """Run one test command and return a structured :class:`TestResult`."""
    runner = runner or subprocess_runner
    started = time.monotonic()
    try:
        exit_code, output = runner(command, Path(repo_path), timeout)
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code, timed_out = -1, True
        output = f"Command timed out after {timeout}s.\n{exc.stdout or ''}\n{exc.stderr or ''}"
    duration = time.monotonic() - started

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... [output truncated by RepoPilot] ..."

    result = TestResult(
        command=command,
        exit_code=exit_code,
        duration=round(duration, 3),
        output=output,
        timed_out=timed_out,
    )

    if looks_like_pytest_output(output):
        parsed = parse_pytest_output(output)
        result.passed = parsed.passed
        result.failed = parsed.failed
        result.errors = parsed.errors
        result.skipped = parsed.skipped
        result.failures = parsed.failing_tests
        result.parsed = True
    return result


def verify(
    commands: list[str],
    repo_path: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    runner: RunnerFn | None = None,
    stop_on_failure: bool = True,
) -> list[TestResult]:
    """Run several commands in order, stopping at the first failure by default."""
    results: list[TestResult] = []
    for command in commands:
        result = run_tests(command, repo_path, timeout=timeout, runner=runner)
        results.append(result)
        if stop_on_failure and not result.success:
            break
    return results
