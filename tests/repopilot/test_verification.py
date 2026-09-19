"""Tests for repopilot.testing.verification.

The subprocess call is injected, so these tests never actually run a test suite -
except for one end-to-end case that runs a trivial real command.
"""

import json
import subprocess

from repopilot.testing.verification import TestResult, run_tests, subprocess_runner, verify

PASSING_OUTPUT = """
============================= test session starts ==============================
collected 3 items

tests/test_auth.py ...                                                   [100%]

============================== 3 passed in 0.05s ===============================
"""

FAILING_OUTPUT = """
============================= test session starts ==============================
collected 3 items

tests/test_auth.py ..F                                                   [100%]

=========================== short test summary info ============================
FAILED tests/test_auth.py::test_is_expired - assert True is False
========================= 1 failed, 2 passed in 0.06s ==========================
"""


def _runner(exit_code: int, output: str):
    def run(command, cwd, timeout):
        return exit_code, output

    return run


def test_successful_run_is_parsed(tmp_path):
    result = run_tests("python -m pytest", tmp_path, runner=_runner(0, PASSING_OUTPUT))

    assert result.success is True
    assert result.passed == 3
    assert result.failed == 0
    assert result.parsed is True
    assert result.total == 3


def test_failing_run_captures_failing_test_names(tmp_path):
    result = run_tests("python -m pytest", tmp_path, runner=_runner(1, FAILING_OUTPUT))

    assert result.success is False
    assert result.failed == 1
    assert result.failures == ["tests/test_auth.py::test_is_expired"]


def test_nonzero_exit_overrides_a_clean_looking_summary(tmp_path):
    """A crash after the summary line must not be reported as success."""
    result = run_tests("python -m pytest", tmp_path, runner=_runner(2, PASSING_OUTPUT))
    assert result.success is False


def test_unparseable_output_still_uses_the_exit_code(tmp_path):
    result = run_tests("make check", tmp_path, runner=_runner(0, "Everything built fine.\n"))

    assert result.parsed is False
    assert result.success is True
    assert "exit 0" in result.summary()


def test_timeout_is_recorded_not_raised(tmp_path):
    def raiser(command, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    result = run_tests("python -m pytest", tmp_path, timeout=5, runner=raiser)

    assert result.timed_out is True
    assert result.success is False
    assert "TIMEOUT" in result.summary()


def test_huge_output_is_truncated(tmp_path):
    result = run_tests("python -m pytest", tmp_path, runner=_runner(0, "x" * 300_000))
    assert "truncated by RepoPilot" in result.output
    assert len(result.output) < 300_000


def test_duration_is_recorded(tmp_path):
    result = run_tests("python -m pytest", tmp_path, runner=_runner(0, PASSING_OUTPUT))
    assert result.duration >= 0.0


def test_verify_stops_at_first_failure(tmp_path):
    results = verify(["one", "two", "three"], tmp_path, runner=_runner(1, FAILING_OUTPUT))
    assert len(results) == 1


def test_verify_runs_all_when_passing(tmp_path):
    results = verify(["one", "two"], tmp_path, runner=_runner(0, PASSING_OUTPUT))
    assert [r.command for r in results] == ["one", "two"]


def test_subprocess_runner_runs_in_the_given_directory(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    exit_code, output = subprocess_runner(
        'python -c "import os; print(os.listdir())"',
        tmp_path,
        30,
    )

    assert exit_code == 0
    assert "marker.txt" in output


def test_result_is_json_serializable():
    json.dumps(TestResult(command="pytest", exit_code=0).to_dict())
