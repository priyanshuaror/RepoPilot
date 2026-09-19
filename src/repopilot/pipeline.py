"""
The RepoPilot pipeline: the stages of a run, wired together.

    ingest -> understand repo -> analyse issue -> impact -> plan -> approval
          -> implement -> select tests -> verify -> analyse failures -> bounded repair
          -> diff -> report

Kept separate from ``cli.py`` on purpose. The CLI is a typer wrapper over these
functions; a future web dashboard would be a different wrapper over the same
ones. Nothing here prints, and nothing here depends on typer or rich.

Every stage returns a serializable object, and the pipeline can be stopped after
any stage (``analyze``/``plan`` on the CLI, or ``dry-run`` approval mode).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from repopilot.analysis.impact import ImpactAnalysis, analyze_impact
from repopilot.analysis.issue import IssueAnalysis, analyze_issue
from repopilot.analysis.repository import RepositoryAnalysis, analyze_repository
from repopilot.approval.workflow import ApprovalDecision, PromptFn, request_approval
from repopilot.config.settings import RepoPilotConfig
from repopilot.debugging.fix_loop import FixLoopOutcome, run_fix_loop
from repopilot.execution.agent import ExecutionRecord, build_agent, build_task_prompt, run_agent
from repopilot.ingestion.github_repo import ClonedRepo, ingest
from repopilot.planning.planner import ImplementationPlan, build_plan
from repopilot.reporting.diff import ChangeSummary, save_diff, summarize_changes
from repopilot.reporting.report import RunReport, determine_status
from repopilot.testing.test_selector import TestSelection, select_tests
from repopilot.testing.verification import TestResult, run_tests

#: Called after each stage with (stage_name, payload). The CLI uses this to print
#: progress; a dashboard could stream it over a websocket.
ProgressFn = Callable[[str, Any], None]


@dataclass
class AnalysisBundle:
    """Everything the read-only stages produce. No repository modification yet."""

    repo: ClonedRepo
    repository_analysis: RepositoryAnalysis
    issue_analysis: IssueAnalysis
    impact: ImpactAnalysis
    plan: ImplementationPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repo.to_dict(),
            "repository_analysis": self.repository_analysis.to_dict(),
            "issue_analysis": self.issue_analysis.to_dict(),
            "impact": self.impact.to_dict(),
            "plan": self.plan.to_dict() if self.plan else {},
        }


@dataclass
class PipelineResult:
    """The full outcome of a run, plus the report that will be written to disk."""

    report: RunReport
    bundle: AnalysisBundle | None = None
    approval: ApprovalDecision | None = None
    execution: ExecutionRecord | None = None
    changes: ChangeSummary | None = None
    repair: FixLoopOutcome | None = None
    warnings: list[str] = field(default_factory=list)


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _noop(stage: str, payload: Any) -> None:  # pragma: no cover - trivial
    return None


def analyze(
    repo_url: str,
    issue: str,
    config: RepoPilotConfig,
    *,
    workspace: Path,
    with_plan: bool = True,
    progress: ProgressFn | None = None,
) -> AnalysisBundle:
    """Run every read-only stage: ingest, understand, analyse, impact, (plan).

    This never modifies the target repository beyond cloning it into ``workspace``.
    """
    emit = progress or _noop

    repo = ingest(repo_url, workspace, branch=config.branch, depth=config.clone_depth, overwrite=True)
    emit("ingestion", repo)

    repository_analysis = analyze_repository(repo.local_path, ignored_dirs=config.ignored_dirs)
    emit("repository_analysis", repository_analysis)

    issue_analysis = analyze_issue(issue, repository_analysis)
    emit("issue_analysis", issue_analysis)

    impact = analyze_impact(issue_analysis, repository_analysis)
    emit("impact", impact)

    bundle = AnalysisBundle(
        repo=repo,
        repository_analysis=repository_analysis,
        issue_analysis=issue_analysis,
        impact=impact,
    )
    if with_plan:
        bundle.plan = build_plan(issue_analysis, repository_analysis, impact)
        emit("plan", bundle.plan)
    return bundle


def run(
    repo_url: str,
    issue: str,
    config: RepoPilotConfig,
    *,
    output_dir: Path,
    run_id: str | None = None,
    approval_prompt: PromptFn | None = None,
    agent_factory: Callable[[Path], Any] | None = None,
    test_runner: Callable[[str, Path], TestResult] | None = None,
    progress: ProgressFn | None = None,
) -> PipelineResult:
    """Execute a full RepoPilot run end to end.

    ``agent_factory`` and ``test_runner`` are injection points: the CLI passes
    real mini-swe-agent / subprocess implementations, tests pass fakes. That is
    what makes the whole pipeline unit-testable without an API key or a test
    suite to run.
    """
    emit = progress or _noop
    run_id = run_id or new_run_id()
    output_dir = Path(output_dir)
    workspace = output_dir / "repo"

    report = RunReport(run_id=run_id, issue=issue, config=config.to_dict())
    result = PipelineResult(report=report)

    bundle = analyze(repo_url, issue, config, workspace=workspace, progress=progress)
    result.bundle = bundle
    report.repository = bundle.repo.to_dict()
    report.repository_analysis = bundle.repository_analysis.to_dict()
    report.issue_analysis = bundle.issue_analysis.to_dict()
    report.impact = bundle.impact.to_dict()
    assert bundle.plan is not None
    report.plan = bundle.plan.to_dict()

    # ---- approval gate: nothing below this line runs without a decision ----
    decision = request_approval(bundle.plan, mode=config.approval_mode, prompt=approval_prompt)
    result.approval = decision
    report.approval = decision.to_dict()
    emit("approval", decision)

    if not decision.approved:
        report.status = determine_status(approved=False, executed=False, targeted_passed=None, broader_passed=None)
        _finalize(report, output_dir, result)
        return result

    # ---- implementation ----
    factory = agent_factory or (lambda path: build_agent(_agent_config(config), path, model_name=config.model_name))
    agent = factory(bundle.repo.local_path)
    task = build_task_prompt(issue, bundle.plan, bundle.impact, repo_path=str(bundle.repo.local_path))
    execution = run_agent(agent, task, bundle.repo.local_path, trajectory_path=output_dir / "trajectory.json")
    result.execution = execution
    report.execution = execution.to_dict()
    emit("execution", execution)
    if execution.error:
        result.warnings.append(f"agent run reported an error: {execution.error}")

    # ---- targeted verification + bounded repair ----
    selection = select_tests(
        execution.changed_files or bundle.impact.source_paths,
        bundle.repository_analysis,
        issue_keywords=bundle.issue_analysis.keywords,
    )
    report.test_selection = selection.to_dict()
    emit("test_selection", selection)

    base_command = (bundle.repository_analysis.test_commands or ["python -m pytest"])[0]
    targeted_command = selection.pytest_command(base=base_command)

    runner = test_runner or (
        lambda command, path: run_tests(command, path, timeout=config.test_timeout)
    )

    def _run_targeted() -> TestResult:
        return runner(targeted_command, bundle.repo.local_path)

    def _attempt_fix(analysis, failing: TestResult) -> str:
        """Hand the failure back to the agent for one bounded repair attempt."""
        repair_task = _repair_prompt(issue, analysis, failing)
        record = run_agent(factory(bundle.repo.local_path), repair_task, bundle.repo.local_path)
        return record.submission or record.exit_status or "agent attempted a fix"

    repair = run_fix_loop(
        _run_targeted,
        _attempt_fix,
        max_attempts=config.max_fix_attempts,
        changed_files=execution.changed_files,
        issue=issue,
        time_budget=config.run_timeout,
    )
    result.repair = repair
    report.repair = repair.to_dict()
    if repair.final_result:
        report.targeted_tests = repair.final_result.to_dict()
    emit("verification", repair)

    # ---- broader suite, only once the targeted tests are green ----
    broader_passed: bool | None = None
    if repair.verified and config.run_broader_tests:
        broader = runner(base_command, bundle.repo.local_path)
        report.broader_tests = broader.to_dict()
        broader_passed = broader.success
        emit("broader_tests", broader)

    # ---- diff + report ----
    changes = summarize_changes(bundle.repo.local_path, expected_paths=bundle.impact.paths)
    result.changes = changes
    report.changes = changes.to_dict()
    save_diff(bundle.repo.local_path, output_dir / "changes.diff")
    emit("diff", changes)

    report.status = determine_status(
        approved=True,
        executed=True,
        targeted_passed=repair.verified,
        broader_passed=broader_passed,
    )
    _finalize(report, output_dir, result)
    return result


def _agent_config(config: RepoPilotConfig) -> dict[str, Any]:
    """Load the mini-swe-agent blocks (agent/model/environment) from the YAML."""
    import yaml

    path = Path(config.agent_config_file)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _repair_prompt(issue: str, analysis, failing: TestResult) -> str:
    """The task text for one repair attempt. Deliberately narrow in scope."""
    return (
        f"## Repair attempt\n\nThe change intended to resolve this issue:\n\n{issue}\n\n"
        f"...did not pass its tests.\n\n"
        f"Command: `{failing.command}`\n"
        f"Result: {failing.summary()}\n"
        f"Failing tests: {', '.join(failing.failures) or '(not identified)'}\n\n"
        f"Classified failure type: {analysis.failure_type} "
        f"(hypothesis, confidence {analysis.confidence})\n"
        f"Likely cause: {analysis.likely_cause}\n"
        f"Suggested direction: {analysis.suggested_fix}\n\n"
        "Fix the underlying problem. Do not delete, skip, or weaken tests to make them pass, "
        "and do not widen the change beyond what the failure requires.\n\n"
        f"Test output (truncated):\n{failing.output[-4000:]}"
    )


def _finalize(report: RunReport, output_dir: Path, result: PipelineResult) -> None:
    """Attach warnings and artifact paths, then write report.json."""
    report.warnings.extend(result.warnings)
    report.artifacts = {
        "report_json": str(output_dir / "report.json"),
        "summary_markdown": str(output_dir / "report.md"),
        "diff": str(output_dir / "changes.diff"),
        "trajectory": str(output_dir / "trajectory.json"),
    }
    report.save(output_dir / "report.json")
    (output_dir / "report.md").write_text(report.to_markdown(), encoding="utf-8")
