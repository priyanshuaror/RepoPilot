"""
RepoPilot's command line interface.

Four commands, matching how the workflow is actually used:

    repopilot analyze --repo <URL> --issue "..."   # read-only: repo map + blast radius
    repopilot plan    --repo <URL> --issue "..."   # analysis + implementation plan
    repopilot run     --repo <URL> --issue "..."   # end to end, with the approval gate
    repopilot report  --run-id <ID>                # re-print a previous run's report

All of the logic lives in :mod:`repopilot.pipeline`; this module only parses
arguments and prints. That split is what lets a future web dashboard reuse the
pipeline without inheriting a CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from repopilot import pipeline
from repopilot.config.settings import DEFAULT_CONFIG_FILE, RepoPilotConfig, load_config
from repopilot.ingestion.github_repo import CloneError

console = Console(highlight=False)
app = typer.Typer(
    rich_markup_mode="rich",
    add_completion=False,
    help="Verification-first repository-level automation.",
)

DEFAULT_RUNS_DIR = Path.home() / ".repopilot" / "runs"

RepoOption = typer.Option(..., "--repo", "-r", help="Repository URL, e.g. https://github.com/psf/requests")
IssueOption = typer.Option(..., "--issue", "-i", help="Issue / task description to resolve")
BranchOption = typer.Option(None, "--branch", "-b", help="Branch to check out (default: the repo's default branch)")
ModelOption = typer.Option(None, "--model", "-m", help="Model name, e.g. anthropic/claude-sonnet-4-5-20250929")
ConfigOption = typer.Option(DEFAULT_CONFIG_FILE, "--config", "-c", help="Path to a RepoPilot YAML config")
OutputOption = typer.Option(None, "--output-dir", "-o", help="Where to write run artifacts")


def _validate_issue(issue: str) -> str:
    """Reject an empty or whitespace-only issue before anything is cloned."""
    cleaned = (issue or "").strip()
    if not cleaned:
        console.print("[bold red]Invalid issue:[/bold red] the issue description cannot be empty.")
        raise typer.Exit(code=2)
    if len(cleaned) < 8:
        console.print(
            "[bold red]Invalid issue:[/bold red] the description is too short to analyse "
            f"({len(cleaned)} characters). Describe the problem in a sentence."
        )
        raise typer.Exit(code=2)
    return cleaned


def _config(config_path: Path, **overrides: Any) -> RepoPilotConfig:
    """Load configuration, failing loudly on a config file the user named but that is missing.

    A missing *default* config falls back to built-in defaults; a missing
    *explicitly requested* one is a user error and must not be silently ignored.
    """
    path = Path(config_path)
    if path != Path(DEFAULT_CONFIG_FILE) and not path.is_file():
        console.print(f"[bold red]Configuration error:[/bold red] no config file at {path}")
        raise typer.Exit(code=2)
    try:
        return load_config(path, **overrides)
    except (ValueError, OSError) as exc:
        console.print(f"[bold red]Configuration error:[/bold red] {exc}")
        raise typer.Exit(code=2)
    except Exception as exc:  # malformed YAML
        console.print(f"[bold red]Configuration error:[/bold red] could not parse {path}: {exc}")
        raise typer.Exit(code=2)


def _warn_if_no_test_command(repo_analysis) -> None:
    """Tell the user up front when this repository has no runnable test command.

    RepoPilot is verification-first, so a repository it cannot test is a
    materially weaker run - the user should know before, not after.
    """
    if not repo_analysis.test_commands:
        console.print(
            "[bold yellow]Warning:[/bold yellow] no test command could be detected for this "
            "repository. Verification will fall back to `python -m pytest`, which may not work "
            "here. Set `repopilot.test_commands` in your config to fix this."
        )


def _run_dir(output_dir: Path | None, run_id: str) -> Path:
    path = Path(output_dir) if output_dir else DEFAULT_RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _progress(stage: str, payload: Any) -> None:
    console.print(f"[dim]->[/dim] [bold]{stage}[/bold]")


@app.command()
def analyze(
    repo: str = RepoOption,
    issue: str = IssueOption,
    branch: str | None = BranchOption,
    config_path: Path = ConfigOption,
    output_dir: Path | None = OutputOption,
    as_json: bool = typer.Option(False, "--json", help="Print the analysis as JSON instead of prose"),
) -> None:
    """Clone and analyse a repository against an issue. Never modifies anything."""
    issue = _validate_issue(issue)
    settings = _config(config_path, branch=branch)
    run_id = pipeline.new_run_id()
    directory = _run_dir(output_dir, run_id)

    try:
        bundle = pipeline.analyze(
            repo, issue, settings, workspace=directory / "repo", with_plan=False, progress=_progress
        )
    except CloneError as exc:
        console.print(f"[bold red]Ingestion failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if as_json:
        console.print_json(json.dumps(bundle.to_dict(), default=str))
        return

    repo_analysis = bundle.repository_analysis
    console.print(f"\n[bold]{bundle.repo.name}[/bold] @ {bundle.repo.commit_sha} ({bundle.repo.branch or 'default'})")
    console.print(f"Languages: {', '.join(repo_analysis.languages) or '(none detected)'}")
    console.print(f"Source files: {len(repo_analysis.source_files)}   Tests: {len(repo_analysis.test_files)}")
    console.print(f"Test commands: {', '.join(repo_analysis.test_commands) or '(none detected)'}")
    _warn_if_no_test_command(repo_analysis)
    console.print("")
    console.print(bundle.impact.explain())


@app.command()
def plan(
    repo: str = RepoOption,
    issue: str = IssueOption,
    branch: str | None = BranchOption,
    config_path: Path = ConfigOption,
    output_dir: Path | None = OutputOption,
) -> None:
    """Produce an implementation plan for an issue, without implementing it."""
    issue = _validate_issue(issue)
    settings = _config(config_path, branch=branch)
    run_id = pipeline.new_run_id()
    directory = _run_dir(output_dir, run_id)

    try:
        bundle = pipeline.analyze(repo, issue, settings, workspace=directory / "repo", progress=_progress)
    except CloneError as exc:
        console.print(f"[bold red]Ingestion failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    assert bundle.plan is not None
    _warn_if_no_test_command(bundle.repository_analysis)
    plan_path = directory / "plan.md"
    plan_path.write_text(bundle.plan.to_markdown(), encoding="utf-8")
    console.print("")
    console.print(bundle.plan.to_markdown())
    console.print(f"\nPlan saved to [bold]{plan_path}[/bold]")


@app.command()
def run(
    repo: str = RepoOption,
    issue: str = IssueOption,
    branch: str | None = BranchOption,
    model: str | None = ModelOption,
    config_path: Path = ConfigOption,
    output_dir: Path | None = OutputOption,
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the plan without prompting (recorded as unreviewed)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Stop after planning; never modify the repository"),
    max_fix_attempts: int | None = typer.Option(None, "--max-fix-attempts", help="Cap on bounded repair iterations"),
) -> None:
    """Run the full workflow: ingest, analyse, plan, approve, implement, verify, report."""
    issue = _validate_issue(issue)
    if dry_run and yes:
        console.print("[bold red]Invalid arguments:[/bold red] --dry-run and --yes are mutually exclusive.")
        raise typer.Exit(code=2)
    approval_mode = "dry-run" if dry_run else ("auto" if yes else "interactive")
    settings = _config(
        config_path,
        branch=branch,
        model_name=model,
        approval_mode=approval_mode,
        max_fix_attempts=max_fix_attempts,
    )
    run_id = pipeline.new_run_id()
    directory = _run_dir(output_dir, run_id)

    console.print(f"[bold green]RepoPilot run {run_id}[/bold green] -> {directory}")
    try:
        result = pipeline.run(repo, issue, settings, output_dir=directory, run_id=run_id, progress=_progress)
    except CloneError as exc:
        console.print(f"[bold red]Ingestion failed:[/bold red] {exc}")
        raise typer.Exit(code=1)

    console.print("")
    console.print(result.report.to_markdown())
    if result.report.status not in {"VERIFIED", "PLANNED", "REJECTED"}:
        raise typer.Exit(code=1)


@app.command()
def report(
    run_id: str = typer.Option(..., "--run-id", help="Run id, i.e. the name of the run directory"),
    runs_dir: Path = typer.Option(DEFAULT_RUNS_DIR, "--runs-dir", help="Directory holding run folders"),
    as_json: bool = typer.Option(False, "--json", help="Print the raw report JSON"),
) -> None:
    """Print the report of a previous run."""
    report_path = Path(runs_dir) / run_id / "report.json"
    if not report_path.is_file():
        console.print(f"[bold red]No report found at[/bold red] {report_path}")
        raise typer.Exit(code=1)

    data = json.loads(report_path.read_text(encoding="utf-8"))
    if as_json:
        console.print_json(json.dumps(data))
        return

    summary_path = report_path.with_name("report.md")
    if summary_path.is_file():
        console.print(summary_path.read_text(encoding="utf-8"))
    else:
        console.print_json(json.dumps(data))


if __name__ == "__main__":
    app()
