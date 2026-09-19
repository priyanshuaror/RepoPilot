"""
Parses raw pytest console output into a small structured summary.

mini-swe-agent (and RepoPilot) don't have a special "run tests" tool - the agent
just runs `pytest ...` as an ordinary bash command, and the raw stdout comes back
as plain text. This module turns that wall of text into something a report or
dashboard can actually use: how many tests passed/failed/errored, and which ones
failed by name.

This is deliberately a plain-text (regex) parser, not a wrapper around pytest's
own `--json-report` plugin: it needs to work on whatever the *agent* happened to
run, in whatever environment it happened to be, without requiring a special
pytest plugin to be pre-installed in every repo RepoPilot might ever touch.
Treat it as a best-effort summary, not a byte-exact one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from repopilot.testing.trajectory_scan import extract_command_runs

# Matches counts in pytest's final summary line, e.g. in:
#   "===== 2 failed, 3 passed, 1 error in 0.42s ====="
#   "===== 5 passed in 0.10s ====="
#   "no tests ran in 0.01s"
_COUNT_RE = re.compile(r"(?P<count>\d+)\s+(?P<label>passed|failed|error(?:s)?|skipped|xfailed|xpassed)")

# Matches lines pytest prints for each failing/erroring test, e.g.:
#   "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
#   "ERROR tests/test_setup.py::test_x - fixture 'db' not found"
_FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<node_id>\S+)", re.MULTILINE)

_NO_TESTS_RE = re.compile(r"no tests ran|collected 0 items", re.IGNORECASE)


@dataclass
class PytestResult:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failing_tests: list[str] = field(default_factory=list)
    ran_no_tests: bool = False
    raw_summary_line: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def all_passed(self) -> bool:
        """True only if tests actually ran and none of them failed or errored."""
        return not self.ran_no_tests and self.total > 0 and self.failed == 0 and self.errors == 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "total": self.total,
            "all_passed": self.all_passed,
            "failing_tests": self.failing_tests,
            "ran_no_tests": self.ran_no_tests,
            "raw_summary_line": self.raw_summary_line,
        }


def looks_like_pytest_output(text: str) -> bool:
    """Heuristic: is this text plausibly pytest console output at all?"""
    lowered = text.lower()
    return "test session starts" in lowered or "pytest" in lowered or bool(_NO_TESTS_RE.search(text))


def parse_pytest_output(output: str) -> PytestResult:
    """Parse raw pytest stdout/stderr text into a `PytestResult`."""
    result = PytestResult()

    if _NO_TESTS_RE.search(output):
        result.ran_no_tests = True

    result.failing_tests = _FAILED_LINE_RE.findall(output)

    # Prefer the LAST line that both contains a count keyword and looks like a
    # "=====...=====" summary banner, since pytest can print more than one
    # (e.g. a per-file summary followed by the final one).
    summary_lines = [line for line in output.splitlines() if _COUNT_RE.search(line) and "=" in line]
    summary_line = summary_lines[-1] if summary_lines else ""
    result.raw_summary_line = summary_line.strip()

    search_target = summary_line or output  # fallback for unusual/minimal output
    for match in _COUNT_RE.finditer(search_target):
        count = int(match.group("count"))
        label = match.group("label")
        if label == "passed":
            result.passed = count
        elif label == "failed":
            result.failed = count
        elif label.startswith("error"):
            result.errors = count
        elif label == "skipped":
            result.skipped = count

    return result


def find_pytest_runs(messages: list[dict]) -> list[dict]:
    """Scan an agent's trajectory messages for pytest invocations and parse each one.

    Returns one dict per pytest command found, in the order it was run, each
    combining the command itself with its parsed `PytestResult`. This is what
    RepoPilot's CLI attaches to `report.json` as `"test_runs"`.
    """
    test_runs = []
    for command_run in extract_command_runs(messages):
        if "pytest" not in command_run.command.lower():
            continue
        parsed = parse_pytest_output(command_run.output)
        test_runs.append({"command": command_run.command, **parsed.to_dict()})
    return test_runs
