"""Tests for repopilot.testing.pytest_parser.parse_pytest_output.

Each test uses a short, realistic block of text shaped like actual pytest
console output, rather than mocking pytest itself - this is what the agent's
bash tool call would actually return as `output`.
"""

from repopilot.testing.pytest_parser import looks_like_pytest_output, parse_pytest_output

ALL_PASSING = """
============================= test session starts ==============================
collected 5 items

tests/test_math.py .....                                                  [100%]

============================== 5 passed in 0.12s ==============================
"""

SOME_FAILING = """
============================= test session starts ==============================
collected 5 items

tests/test_math.py ...FF                                                  [100%]

=================================== FAILURES ====================================
___________________________________ test_add ____________________________________
    def test_add():
>       assert add(2, 2) == 5
E       assert 4 == 5

tests/test_math.py:12: AssertionError
___________________________________ test_sub _____________________________________
    def test_sub():
>       assert subtract(5, 2) == 4
E       assert 3 == 4

tests/test_math.py:20: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_add - assert 4 == 5
FAILED tests/test_math.py::test_sub - assert 3 == 4
========================= 2 failed, 3 passed in 0.18s ===========================
"""

WITH_ERROR = """
============================= test session starts ==============================
collected 3 items

tests/test_db.py EE.                                                      [100%]

=========================== short test summary info ============================
ERROR tests/test_db.py::test_read - fixture 'db_connection' not found
ERROR tests/test_db.py::test_write - fixture 'db_connection' not found
============================ 1 passed, 2 errors in 0.05s =========================
"""

NO_TESTS_COLLECTED = """
============================= test session starts ==============================
collected 0 items

============================ no tests ran in 0.01s ==============================
"""

WITH_SKIPPED = """
============================= test session starts ==============================
collected 5 items

tests/test_math.py ....s                                                  [100%]

======================= 1 skipped, 4 passed in 0.09s ==========================
"""


def test_all_passing():
    result = parse_pytest_output(ALL_PASSING)
    assert result.passed == 5
    assert result.failed == 0
    assert result.errors == 0
    assert result.total == 5
    assert result.all_passed is True
    assert result.failing_tests == []


def test_some_failing():
    result = parse_pytest_output(SOME_FAILING)
    assert result.passed == 3
    assert result.failed == 2
    assert result.total == 5
    assert result.all_passed is False
    assert result.failing_tests == [
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_sub",
    ]


def test_with_error():
    result = parse_pytest_output(WITH_ERROR)
    assert result.passed == 1
    assert result.errors == 2
    assert result.all_passed is False
    assert len(result.failing_tests) == 2


def test_no_tests_collected():
    result = parse_pytest_output(NO_TESTS_COLLECTED)
    assert result.ran_no_tests is True
    assert result.total == 0
    assert result.all_passed is False  # ran_no_tests should never count as "passing"


def test_with_skipped():
    result = parse_pytest_output(WITH_SKIPPED)
    assert result.skipped == 1
    assert result.passed == 4
    assert result.total == 5
    assert result.all_passed is True  # skipped tests don't count as failures


def test_looks_like_pytest_output():
    assert looks_like_pytest_output(ALL_PASSING) is True
    assert looks_like_pytest_output("total 24\ndrwxr-xr-x ...") is False


def test_to_dict_is_json_serializable():
    import json

    result = parse_pytest_output(SOME_FAILING)
    json.dumps(result.to_dict())  # should not raise
