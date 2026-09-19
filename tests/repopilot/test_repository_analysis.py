"""Tests for repopilot.analysis.repository."""

from repopilot.analysis.repository import (
    analyze_repository,
    is_test_path,
    iter_repository_files,
    read_text_safely,
)


def test_finds_source_and_test_files(sample_repo):
    analysis = analyze_repository(sample_repo)

    assert "src/auth/jwt.py" in analysis.source_files
    assert "tests/test_auth.py" in analysis.test_files
    assert "src/auth/jwt.py" not in analysis.test_files


def test_ignores_generated_directories(sample_repo):
    analysis = analyze_repository(sample_repo)

    assert not any(path.startswith("node_modules/") for path in analysis.source_files)
    assert not any("node_modules" in path for path in analysis.config_files)


def test_detects_language_and_package_manager(sample_repo):
    analysis = analyze_repository(sample_repo)

    assert analysis.primary_language == "Python"
    assert analysis.languages["Python"] >= 5
    assert "pip/pyproject" in analysis.package_managers


def test_detects_pytest_test_command(sample_repo):
    analysis = analyze_repository(sample_repo)
    assert "python -m pytest" in analysis.test_commands


def test_config_files_are_listed(sample_repo):
    analysis = analyze_repository(sample_repo)
    assert "pyproject.toml" in analysis.config_files


def test_important_directories_rank_source_tree(sample_repo):
    analysis = analyze_repository(sample_repo)
    assert any(name.startswith("src/") for name in analysis.important_directories)


def test_binary_files_are_skipped_not_counted_as_source(sample_repo):
    (sample_repo / "logo.png").write_bytes(b"\x89PNG\r\n")
    analysis = analyze_repository(sample_repo)

    assert "logo.png" not in analysis.source_files
    assert analysis.skipped_files >= 1


def test_read_text_safely_returns_empty_for_binary(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG")
    assert read_text_safely(path) == ""


def test_read_text_safely_respects_size_limit(tmp_path):
    path = tmp_path / "big.py"
    path.write_text("x = 1\n" * 1000)
    assert read_text_safely(path, max_bytes=10) == ""
    assert read_text_safely(path, max_bytes=100_000) != ""


def test_is_test_path():
    assert is_test_path("tests/test_auth.py") is True
    assert is_test_path("src/auth/test_helpers.py") is True
    assert is_test_path("app/user.spec.ts") is True
    assert is_test_path("src/auth/jwt.py") is False


def test_iter_repository_files_prunes_ignored(sample_repo):
    paths = {p.relative_to(sample_repo).as_posix() for p in iter_repository_files(sample_repo)}
    assert "src/auth/jwt.py" in paths
    assert not any(path.startswith("node_modules") for path in paths)


def test_empty_directory_is_handled(tmp_path):
    analysis = analyze_repository(tmp_path)
    assert analysis.source_files == []
    assert analysis.primary_language is None
    assert analysis.to_dict()["primary_language"] is None
