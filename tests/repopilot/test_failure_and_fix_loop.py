"""Tests for repopilot.debugging.failure_analyzer and repopilot.debugging.fix_loop."""

import json

from repopilot.debugging.failure_analyzer import analyze_failure, classify_failure, looks_environmental
from repopilot.debugging.fix_loop import failure_signature, run_fix_loop
from repopilot.testing.verification import TestResult


def _result(output: str, *, exit_code: int = 1, failures=None, timed_out: bool = False) -> TestResult:
    return TestResult(
        command="python -m pytest",
        exit_code=exit_code,
        output=output,
        failures=failures or [],
        failed=len(failures or []),
        timed_out=timed_out,
        parsed=True,
    )


# ------------------------------------------------------------- classification


def test_classifies_syntax_error():
    kind, _, evidence = classify_failure('  File "src/auth/jwt.py", line 3\n    SyntaxError: invalid syntax\n')
    assert kind == "syntax_error"
    assert evidence


def test_classifies_import_error():
    kind, _, _ = classify_failure("ModuleNotFoundError: No module named 'auth'")
    assert kind == "import_error"


def test_classifies_assertion_failure():
    kind, _, _ = classify_failure("E       AssertionError: assert True is False")
    assert kind == "assertion_failure"


def test_classifies_collection_error():
    kind, _, _ = classify_failure("ERROR collecting tests/test_auth.py\nfixture 'db' not found")
    assert kind == "collection_error"


def test_unclassifiable_output_says_unknown():
    kind, description, _ = classify_failure("something went sideways")
    assert kind == "unknown"
    assert "could not be classified" in description


def test_environment_failures_are_recognised():
    assert looks_environmental("ERROR: Could not resolve host: pypi.org") is True
    assert looks_environmental("AssertionError: assert 1 == 2") is False


# ------------------------------------------------------------------- analysis


def test_analysis_links_failure_to_changed_files():
    output = 'File "src/auth/jwt.py", line 9\n    AssertionError: assert True is False'
    analysis = analyze_failure(_result(output), changed_files=["src/auth/jwt.py"])

    assert analysis.failure_type == "assertion_failure"
    assert "src/auth/jwt.py" in analysis.relevant_files
    assert "changed" in analysis.likely_cause


def test_analysis_notes_when_changed_files_are_absent_from_the_output():
    analysis = analyze_failure(
        _result("AssertionError: assert 1 == 2"), changed_files=["src/auth/jwt.py"], issue="x"
    )
    assert any("none of the changed files" in item for item in analysis.evidence)


def test_environmental_failure_is_marked_unrepairable_in_spirit():
    analysis = analyze_failure(_result("ERROR: Could not resolve host: pypi.org"))
    assert analysis.is_environmental is True
    assert analysis.repairable is False
    assert "stop rather than patch" in analysis.suggested_fix


def test_unknown_failure_is_not_repairable():
    assert analyze_failure(_result("mysterious")).repairable is False


def test_confidence_never_claims_certainty():
    analysis = analyze_failure(
        _result('File "src/auth/jwt.py", line 1\nAssertionError', failures=["tests/test_auth.py::test_x"]),
        changed_files=["src/auth/jwt.py"],
    )
    assert 0.0 < analysis.confidence <= 0.85


def test_timeout_result_classified_as_timeout():
    analysis = analyze_failure(_result("", exit_code=-1, timed_out=True))
    assert analysis.failure_type == "timeout"


def test_analysis_is_json_serializable():
    json.dumps(analyze_failure(_result("AssertionError")).to_dict())


# ------------------------------------------------------------------- fix loop


def test_loop_stops_immediately_when_tests_pass():
    calls = []

    outcome = run_fix_loop(
        lambda: TestResult(command="pytest", exit_code=0, passed=3, parsed=True),
        lambda analysis, result: calls.append("fix") or "fixed",
    )

    assert outcome.verified is True
    assert outcome.stop_reason == "tests_passed"
    assert outcome.attempt_count == 0
    assert calls == []


def test_loop_repairs_then_succeeds():
    results = [
        _result('File "src/auth/jwt.py", line 1\nAssertionError', failures=["t::a"]),
        TestResult(command="pytest", exit_code=0, passed=3, parsed=True),
    ]

    outcome = run_fix_loop(
        lambda: results.pop(0),
        lambda analysis, result: "patched jwt.py",
        changed_files=["src/auth/jwt.py"],
    )

    assert outcome.verified is True
    assert outcome.attempt_count == 1
    assert outcome.attempts[0].action == "patched jwt.py"


def test_loop_respects_max_attempts():
    counter = {"n": 0}

    def failing():
        counter["n"] += 1
        return _result("AssertionError: assert 1 == 2", failures=[f"t::a{counter['n']}"])

    outcome = run_fix_loop(failing, lambda analysis, result: "tried", max_attempts=2)

    assert outcome.verified is False
    assert outcome.attempt_count == 2
    assert outcome.stop_reason == "max_attempts_reached"


def test_loop_stops_on_repeated_identical_failure():
    identical = _result("AssertionError: assert 1 == 2", failures=["t::a"])

    outcome = run_fix_loop(lambda: identical, lambda analysis, result: "tried", max_attempts=5)

    assert outcome.stop_reason == "repeated_identical_failure"
    assert outcome.attempt_count == 1


def test_loop_stops_on_environmental_failure_without_attempting_a_fix():
    attempts = []

    outcome = run_fix_loop(
        lambda: _result("ERROR: Could not resolve host: pypi.org"),
        lambda analysis, result: attempts.append(1) or "tried",
    )

    assert outcome.stop_reason == "environmental_failure"
    assert attempts == []


def test_loop_stops_on_unclassifiable_failure():
    outcome = run_fix_loop(lambda: _result("???"), lambda analysis, result: "tried")
    assert outcome.stop_reason == "unrepairable_failure"


def test_loop_survives_a_crashing_fix_step():
    def boom(analysis, result):
        raise RuntimeError("agent exploded")

    outcome = run_fix_loop(lambda: _result("AssertionError: x", failures=["t::a"]), boom)

    assert outcome.stop_reason == "fix_step_failed"
    assert "agent exploded" in outcome.attempts[0].action


def test_zero_max_attempts_means_no_repairs():
    outcome = run_fix_loop(
        lambda: _result("AssertionError: x", failures=["t::a"]),
        lambda analysis, result: "tried",
        max_attempts=0,
    )
    assert outcome.attempt_count == 0
    assert outcome.stop_reason == "max_attempts_reached"


def test_failure_signature_is_stable_and_discriminating():
    a = _result("timing differs 0.12s", failures=["t::a"])
    b = _result("timing differs 9.99s", failures=["t::a"])
    c = _result("x", failures=["t::b"])

    assert failure_signature(a) == failure_signature(b)
    assert failure_signature(a) != failure_signature(c)


def test_outcome_is_json_serializable():
    outcome = run_fix_loop(lambda: TestResult(command="pytest", exit_code=0, passed=1, parsed=True), lambda a, r: "x")
    json.dumps(outcome.to_dict())
