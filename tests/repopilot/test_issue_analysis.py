"""Tests for repopilot.analysis.issue."""

from repopilot.analysis.issue import (
    analyze_issue,
    detect_subsystems,
    extract_explicit_paths,
    extract_keywords,
)
from repopilot.analysis.repository import analyze_repository

ISSUE = "Fix authentication failure when the JWT expires - require_auth still lets requests through."


def test_extract_keywords_drops_stopwords_and_splits_names():
    keywords = extract_keywords("Fix the validateToken helper when jwt_expiry is wrong")

    assert "validatetoken" in keywords
    assert "validate" in keywords
    assert "expiry" in keywords
    assert "the" not in keywords
    assert "fix" not in keywords  # 'fix' carries no location signal


def test_extract_explicit_paths():
    paths = extract_explicit_paths("The bug is in src/auth/jwt.py, see also `settings.py`")
    assert "src/auth/jwt.py" in paths
    assert "settings.py" in paths


def test_detect_subsystems():
    assert "authentication" in detect_subsystems(["jwt", "token"])
    assert detect_subsystems(["quokka"]) == []


def test_candidate_files_rank_the_right_module(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue(ISSUE, repo)

    assert analysis.candidate_paths, "expected at least one candidate"
    assert "src/auth/jwt.py" in analysis.candidate_paths[:3]
    assert "src/billing/invoice.py" not in analysis.candidate_paths[:2]


def test_candidate_tests_are_separate_from_source(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue(ISSUE, repo)

    assert all(path.startswith("tests/") for path in [lead.path for lead in analysis.candidate_tests])


def test_explicitly_named_path_becomes_a_detected_fact(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue("Broken expiry logic in src/auth/jwt.py", repo)

    assert any("src/auth/jwt.py" in fact for fact in analysis.detected_facts)
    assert analysis.candidate_paths[0] == "src/auth/jwt.py"


def test_nonexistent_path_is_recorded_as_uncertainty(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue("Broken logic in src/nope/missing.py", repo)

    assert any("does not exist" in item for item in analysis.uncertainties)


def test_unmatchable_issue_reports_low_confidence_not_a_guess(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue("zzzqqq wobblefrotz", repo)

    assert analysis.candidate_files == []
    assert analysis.confidence == 0.0
    assert any("manual triage" in item for item in analysis.uncertainties)


def test_every_candidate_carries_a_reason(sample_repo):
    repo = analyze_repository(sample_repo)
    analysis = analyze_issue(ISSUE, repo)

    assert all(lead.reasons for lead in analysis.candidate_files)


def test_result_is_json_serializable(sample_repo):
    import json

    repo = analyze_repository(sample_repo)
    json.dumps(analyze_issue(ISSUE, repo).to_dict())
