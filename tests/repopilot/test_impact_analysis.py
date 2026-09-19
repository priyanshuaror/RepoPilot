"""Tests for repopilot.analysis.impact - the blast-radius estimator."""

from repopilot.analysis.impact import (
    analyze_impact,
    extract_imports,
    module_name_for,
    unexpected_changes,
)
from repopilot.analysis.issue import analyze_issue
from repopilot.analysis.repository import analyze_repository

ISSUE = "Fix JWT expiration handling - expired tokens are still accepted by require_auth."


def _impact(sample_repo, issue=ISSUE):
    repo = analyze_repository(sample_repo)
    return analyze_impact(analyze_issue(issue, repo), repo)


def test_module_name_for_strips_src_and_init():
    assert module_name_for("src/auth/jwt.py") == "auth.jwt"
    assert module_name_for("src/auth/__init__.py") == "auth"
    assert module_name_for("pkg/mod.py") == "pkg.mod"


def test_extract_imports():
    imports = extract_imports("from auth.jwt import is_expired\nimport os, sys\n")
    assert "auth.jwt" in imports
    assert "os" in imports
    assert "sys" in imports


def test_identifies_the_seed_file(sample_repo):
    impact = _impact(sample_repo)
    assert "src/auth/jwt.py" in impact.paths


def test_follows_reverse_dependencies(sample_repo):
    """middleware/auth.py imports auth.jwt, so it is in the blast radius."""
    impact = _impact(sample_repo)
    assert "src/middleware/auth.py" in impact.paths


def test_includes_covering_tests(sample_repo):
    impact = _impact(sample_repo)
    assert "tests/test_auth.py" in impact.related_tests


def test_does_not_include_the_whole_repository(sample_repo):
    impact = _impact(sample_repo)
    assert "src/billing/invoice.py" not in impact.paths
    assert "tests/test_billing.py" not in impact.paths


def test_every_affected_file_explains_itself(sample_repo):
    impact = _impact(sample_repo)
    assert impact.affected_files
    for item in impact.affected_files:
        assert item.reasons, f"{item.path} has no justification"


def test_components_are_named(sample_repo):
    impact = _impact(sample_repo)
    assert "auth" in impact.affected_components


def test_seed_file_is_at_distance_zero(sample_repo):
    impact = _impact(sample_repo)
    by_path = {item.path: item for item in impact.affected_files}
    assert by_path["src/auth/jwt.py"].distance == 0


def test_import_edges_are_recorded_as_reasons(sample_repo):
    """middleware/auth.py must carry the dependency edge that pulled it in."""
    impact = _impact(sample_repo)
    by_path = {item.path: item for item in impact.affected_files}
    reasons = by_path["src/middleware/auth.py"].reasons
    assert any("imports 'auth.jwt'" in reason for reason in reasons)


def test_confidence_is_never_certain(sample_repo):
    impact = _impact(sample_repo)
    assert 0.0 <= impact.confidence <= 0.85


def test_unidentifiable_issue_says_so(sample_repo):
    impact = _impact(sample_repo, issue="zzzqqq wobblefrotz")
    assert impact.affected_files == []
    assert any("no seed file" in item for item in impact.uncertainties)


def test_explain_mentions_files_and_confidence(sample_repo):
    text = _impact(sample_repo).explain()
    assert "src/auth/jwt.py" in text
    assert "Confidence:" in text


def test_unexpected_changes_flags_outside_files(sample_repo):
    impact = _impact(sample_repo)
    unexpected = unexpected_changes(["src/auth/jwt.py", "tools/unrelated_module.py"], impact)

    assert unexpected == ["tools/unrelated_module.py"]


def test_unexpected_changes_allows_siblings_in_predicted_dirs(sample_repo):
    impact = _impact(sample_repo)
    assert unexpected_changes(["src/auth/refresh.py"], impact) == []


def test_impact_is_json_serializable(sample_repo):
    import json

    json.dumps(_impact(sample_repo).to_dict())
