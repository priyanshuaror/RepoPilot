"""
Phase 5 - implementation planning.

Converts the analysis stages into a concrete, reviewable plan: what will be
changed, in what order, and which tests will be used to prove it worked.

The plan is both machine-readable (``to_dict`` - this is what a future dashboard
and the approval API consume) and human-readable (``to_markdown`` - this is what
the reviewer actually reads before typing "y").

The plan is built deterministically from the analysis results. An LLM is not
needed to say "run the tests that cover the files you are about to edit", and
making the planner deterministic keeps it testable without API calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from repopilot.analysis.impact import ImpactAnalysis
from repopilot.analysis.issue import IssueAnalysis
from repopilot.analysis.repository import RepositoryAnalysis


@dataclass
class PlanStep:
    """One ordered step of the plan."""

    order: int
    action: str
    rationale: str = ""
    targets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImplementationPlan:
    """A reviewable proposal for how an issue will be addressed."""

    issue: str
    summary: str
    affected_files: list[str] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)
    tests_to_run: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["steps"] = [step.to_dict() for step in self.steps]
        return data

    def to_markdown(self) -> str:
        """Render the plan the way a reviewer sees it at the approval prompt."""
        lines = ["# RepoPilot implementation plan", "", f"**Issue:** {self.issue}", "", self.summary, ""]
        lines.append("## Files expected to change")
        if self.affected_files:
            lines.extend(f"- `{path}`" for path in self.affected_files)
        else:
            lines.append("- (none identified - RepoPilot could not narrow this down)")
        lines += ["", "## Components", ", ".join(self.affected_components) or "(none identified)", "", "## Steps"]
        for step in self.steps:
            suffix = f" ({', '.join(step.targets)})" if step.targets else ""
            lines.append(f"{step.order}. {step.action}{suffix}")
            if step.rationale:
                lines.append(f"   - _why:_ {step.rationale}")
        lines += ["", "## Verification"]
        if self.tests_to_run:
            lines.extend(f"- `{command}`" for command in self.tests_to_run)
        else:
            lines.append("- (no test command detected)")
        if self.risks:
            lines += ["", "## Risks"] + [f"- {risk}" for risk in self.risks]
        if self.assumptions:
            lines += ["", "## Assumptions"] + [f"- {item}" for item in self.assumptions]
        lines += ["", f"_Planner confidence: {self.confidence} (estimate, not a guarantee)_"]
        return "\n".join(lines)


def build_plan(
    issue_analysis: IssueAnalysis,
    repo_analysis: RepositoryAnalysis,
    impact: ImpactAnalysis,
    *,
    max_files: int = 10,
) -> ImplementationPlan:
    """Assemble an :class:`ImplementationPlan` from the three analysis stages."""
    source_paths = impact.source_paths[:max_files]
    test_paths = impact.related_tests[:max_files]

    summary = _summary(issue_analysis, impact, source_paths)
    plan = ImplementationPlan(
        issue=issue_analysis.issue,
        summary=summary,
        affected_files=source_paths + test_paths,
        affected_components=impact.affected_components,
        confidence=impact.confidence,
    )

    order = 1
    if source_paths:
        plan.steps.append(
            PlanStep(
                order,
                "Read the implicated files before editing them",
                "confirm the analysis is right",
                source_paths[:5],
            )
        )
        order += 1
    plan.steps.append(
        PlanStep(
            order,
            "Run the covering tests to capture the current state" if test_paths else "Reproduce the reported behaviour",
            "a fix that was never observed failing cannot be shown to work",
            test_paths[:5],
        )
    )
    order += 1
    plan.steps.append(
        PlanStep(order, "Implement the change in the implicated source files", summary, source_paths[:5])
    )
    order += 1
    plan.steps.append(
        PlanStep(
            order,
            "Add or update a regression test covering the reported behaviour",
            "verification-first: the fix must be provable by a test, not by assertion",
            test_paths[:3],
        )
    )
    order += 1
    plan.steps.append(PlanStep(order, "Run the targeted tests", "fast feedback on the specific change", test_paths[:5]))
    order += 1
    plan.steps.append(
        PlanStep(order, "Run the broader test suite", "catch regressions outside the predicted blast radius")
    )

    plan.tests_to_run = repo_analysis.test_commands or ["python -m pytest"]
    plan.risks = _risks(impact, repo_analysis)
    plan.assumptions = _assumptions(issue_analysis, impact, repo_analysis)
    return plan


def _summary(issue_analysis: IssueAnalysis, impact: ImpactAnalysis, source_paths: list[str]) -> str:
    components = ", ".join(impact.affected_components[:4]) or "an unidentified component"
    if not source_paths:
        return (
            "RepoPilot could not confidently locate the code behind this issue. The agent will need to "
            "explore the repository first; treat the steps below as a starting point, not a diagnosis."
        )
    return (
        f"Address the reported issue in {components}, centred on `{source_paths[0]}`. "
        f"{len(source_paths)} source file(s) are inside the predicted blast radius."
    )


def _risks(impact: ImpactAnalysis, repo_analysis: RepositoryAnalysis) -> list[str]:
    risks: list[str] = []
    indirect = [item for item in impact.affected_files if item.distance > 0 and item.kind == "source"]
    if indirect:
        risks.append(f"{len(indirect)} module(s) import the code being changed and may regress")
    if not impact.related_tests:
        risks.append("no existing test covers the affected code, so a regression here would be silent")
    if not repo_analysis.test_commands:
        risks.append("no test command could be detected for this repository")
    if impact.confidence < 0.4:
        risks.append(f"low impact-analysis confidence ({impact.confidence}); the blast radius may be wrong")
    if len(impact.affected_files) > 15:
        risks.append("the blast radius is wide, which usually means the issue description was under-specified")
    return risks


def _assumptions(
    issue_analysis: IssueAnalysis, impact: ImpactAnalysis, repo_analysis: RepositoryAnalysis
) -> list[str]:
    assumptions: list[str] = []
    if repo_analysis.primary_language:
        assumptions.append(f"the change is in {repo_analysis.primary_language} code")
    if repo_analysis.test_commands:
        assumptions.append(f"tests are run with `{repo_analysis.test_commands[0]}`")
    else:
        assumptions.append("no test runner was detected; one will have to be found at execution time")
    if issue_analysis.subsystems:
        assumptions.append(f"the issue concerns the {', '.join(issue_analysis.subsystems)} subsystem(s)")
    assumptions.extend(issue_analysis.uncertainties)
    assumptions.extend(impact.uncertainties)
    return assumptions
