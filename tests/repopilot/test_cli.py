"""Tests for repopilot.cli.

These exercise the CLI surface only - argument wiring, exit codes, and output -
against a local git repository. They skip cleanly if typer is not installed.
"""

import json

import pytest

typer = pytest.importorskip("typer")
from typer.testing import CliRunner  # noqa: E402

from repopilot import cli  # noqa: E402

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
