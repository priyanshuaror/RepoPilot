"""Tests for repopilot.cli.

These exercise the CLI surface - argument wiring, validation, exit codes and
output - against a local git repository. typer is a declared dependency of this
project, so these tests are never skipped; if the import fails, the environment
is broken and the suite should say so.

Exit-code convention: 1 = the run failed (bad repository, missing report),
2 = the user's input was wrong (bad issue, bad config, conflicting flags).
"""

import json

from typer.testing import CliRunner

from repopilot import cli

runner = CliRunner()
ISSUE = "Fix JWT expiration handling"


def test_analyze_prints_the_blast_radius(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        ["analyze", "--repo", str(sample_git_repo), "--issue", ISSUE, "--output-dir", str(tmp_path / "out")],
    )

    assert result.exit_code == 0, result.output
    assert "src/auth/jwt.py" in result.output
    assert "Confidence:" in result.output


def test_analyze_json_output_is_parseable(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            str(sample_git_repo),
            "--issue",
            ISSUE,
            "--output-dir",
            str(tmp_path / "out"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "impact" in result.output


def test_plan_writes_plan_markdown(sample_git_repo, tmp_path):
    out = tmp_path / "out"
    result = runner.invoke(
        cli.app, ["plan", "--repo", str(sample_git_repo), "--issue", ISSUE, "--output-dir", str(out)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "plan.md").is_file()
    assert "implementation plan" in (out / "plan.md").read_text().lower()


def test_bad_repository_exits_nonzero(tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            "https://not-a-real-host.invalid/x.git",
            "--issue",
            ISSUE,
            "--output-dir",
            str(tmp_path / "o"),
        ],
    )

    assert result.exit_code == 1
    assert "Ingestion failed" in result.output


def test_dry_run_does_not_need_a_model(sample_git_repo, tmp_path):
    out = tmp_path / "out"
    result = runner.invoke(
        cli.app,
        ["run", "--repo", str(sample_git_repo), "--issue", ISSUE, "--output-dir", str(out), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "REJECTED" in result.output
    assert (out / "report.json").is_file()


def test_report_command_prints_a_saved_run(tmp_path):
    runs_dir = tmp_path / "runs"
    (runs_dir / "abc").mkdir(parents=True)
    (runs_dir / "abc" / "report.json").write_text(json.dumps({"run_id": "abc", "status": "VERIFIED"}))
    (runs_dir / "abc" / "report.md").write_text("# RepoPilot Report\n\nFinal status: VERIFIED\n")

    result = runner.invoke(cli.app, ["report", "--run-id", "abc", "--runs-dir", str(runs_dir)])

    assert result.exit_code == 0, result.output
    assert "VERIFIED" in result.output


def test_report_command_fails_for_unknown_run(tmp_path):
    result = runner.invoke(cli.app, ["report", "--run-id", "nope", "--runs-dir", str(tmp_path)])
    assert result.exit_code == 1


def test_help_lists_every_command():
    result = runner.invoke(cli.app, ["--help"])
    for command in ("analyze", "plan", "run", "report"):
        assert command in result.output


# ------------------------------------------------------------ input validation


def test_empty_issue_is_rejected(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        ["analyze", "--repo", str(sample_git_repo), "--issue", "   ", "--output-dir", str(tmp_path / "o")],
    )

    assert result.exit_code == 2
    assert "cannot be empty" in result.output


def test_too_short_issue_is_rejected(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        ["analyze", "--repo", str(sample_git_repo), "--issue", "bug", "--output-dir", str(tmp_path / "o")],
    )

    assert result.exit_code == 2
    assert "too short" in result.output


def test_missing_issue_argument_is_a_usage_error(sample_git_repo):
    result = runner.invoke(cli.app, ["analyze", "--repo", str(sample_git_repo)])
    assert result.exit_code != 0


def test_unknown_command_is_a_usage_error():
    result = runner.invoke(cli.app, ["teleport", "--repo", "x"])
    assert result.exit_code != 0


def test_unknown_flag_is_a_usage_error(sample_git_repo):
    result = runner.invoke(
        cli.app, ["analyze", "--repo", str(sample_git_repo), "--issue", ISSUE, "--turbo"]
    )
    assert result.exit_code != 0


def test_invalid_local_repository_path_fails_cleanly(tmp_path):
    """A directory that exists but is not a git repository must not crash."""
    plain = tmp_path / "not_a_repo"
    plain.mkdir()

    result = runner.invoke(
        cli.app,
        ["analyze", "--repo", str(plain), "--issue", ISSUE, "--output-dir", str(tmp_path / "o")],
    )

    assert result.exit_code == 1
    assert "Ingestion failed" in result.output


# ----------------------------------------------------------------- config


def test_missing_named_config_file_is_an_error(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            str(sample_git_repo),
            "--issue",
            ISSUE,
            "--config",
            str(tmp_path / "absent.yaml"),
            "--output-dir",
            str(tmp_path / "o"),
        ],
    )

    assert result.exit_code == 2
    assert "no config file at" in result.output


def test_malformed_config_file_is_an_error(sample_git_repo, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("repopilot:\n  approval_mode: [unclosed\n")

    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            str(sample_git_repo),
            "--issue",
            ISSUE,
            "--config",
            str(bad),
            "--output-dir",
            str(tmp_path / "o"),
        ],
    )

    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_invalid_approval_mode_in_config_is_an_error(sample_git_repo, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("repopilot:\n  approval_mode: whenever\n")

    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            str(sample_git_repo),
            "--issue",
            ISSUE,
            "--config",
            str(bad),
            "--output-dir",
            str(tmp_path / "o"),
        ],
    )

    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_dry_run_and_yes_are_mutually_exclusive(sample_git_repo, tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--repo",
            str(sample_git_repo),
            "--issue",
            ISSUE,
            "--output-dir",
            str(tmp_path / "o"),
            "--dry-run",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


# --------------------------------------------------------- test-command warning


def test_repository_without_a_test_command_is_flagged(repo_without_tests, tmp_path):
    """A repo RepoPilot cannot verify must say so before the user relies on it."""
    result = runner.invoke(
        cli.app,
        [
            "analyze",
            "--repo",
            str(repo_without_tests),
            "--issue",
            "Fix the login helper",
            "--output-dir",
            str(tmp_path / "o"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "no test command could be detected" in result.output
