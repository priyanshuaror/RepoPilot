"""
Phase 13 - the final report.

One serializable object that carries the whole run: repository, issue, analysis,
impact, plan, approval decision, execution record, test results, repair attempts,
diff summary and warnings.

This is the contract a future dashboard consumes - which is why every field is a
plain dict/list/str and nothing here holds a live object. ``to_markdown`` renders
the same data for a human.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Final statuses. VERIFIED is only ever set when tests actually ran and passed.
STATUS_VERIFIED = "VERIFIED"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_FAILED = "FAILED"
STATUS_REJECTED = "REJECTED"
STATUS_PLANNED = "PLANNED"

#: One-line explanation of each status, shown in the markdown verdict.
STATUS_MEANING: dict[str, str] = {
    STATUS_VERIFIED: "the change was made and the selected tests passed",
    STATUS_UNVERIFIED: "the change was made but no test run could confirm it",
    STATUS_FAILED: "tests ran and did not pass",
    STATUS_REJECTED: "the plan was not approved; the repository was not modified",
    STATUS_PLANNED: "a plan was produced but no implementation was attempted",
}


@dataclass
class RunReport:
    """The auditable record of one RepoPilot run."""

    run_id: str
    issue: str
    repository: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    repository_analysis: dict[str, Any] = field(default_factory=dict)
    issue_analysis: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    test_selection: dict[str, Any] = field(default_factory=dict)
    targeted_tests: dict[str, Any] = field(default_factory=dict)
    broader_tests: dict[str, Any] = field(default_factory=dict)
    repair: dict[str, Any] = field(default_factory=dict)
    changes: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    status: str = STATUS_UNVERIFIED
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "status": self.status,
            "issue": self.issue,
            "repository": self.repository,
            "config": self.config,
            "repository_analysis": self.repository_analysis,
            "issue_analysis": self.issue_analysis,
            "impact": self.impact,
            "plan": self.plan,
            "approval": self.approval,
            "execution": self.execution,
            "test_selection": self.test_selection,
            "targeted_tests": self.targeted_tests,
            "broader_tests": self.broader_tests,
            "repair": self.repair,
            "changes": self.changes,
            "warnings": self.warnings,
            "artifacts": self.artifacts,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def save(self, path: Path) -> Path:
        """Write ``report.json`` and return its path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def to_markdown(self) -> str:
        """The human-readable run summary.

        Ordered so a reviewer can answer, top to bottom: what was asked, what
        RepoPilot thought would be affected, what it planned, what it changed,
        and whether any of that was actually proven by a test.
        """
        sections = [
            self._header(),
            self._issue_section(),
            self._repository_section(),
            self._impact_section(),
            self._plan_section(),
            self._tests_section(),
            self._failures_section(),
            self._changes_section(),
            self._verdict_section(),
            self._artifacts_section(),
        ]
        return "\n".join(section for section in sections if section).rstrip() + "\n"

    # ------------------------------------------------------------------ parts

    def _header(self) -> str:
        return f"# RepoPilot Report\n\n**Run `{self.run_id}` - final status: {self.status}**\n"

    def _issue_section(self) -> str:
        lines = ["## 1. Issue", "", self.issue or "_(none given)_"]
        analysis = self.issue_analysis or {}
        if analysis.get("subsystems"):
            lines.append("")
            lines.append(f"Suggested subsystem(s): {', '.join(analysis['subsystems'])}")
        if analysis.get("keywords"):
            lines.append(f"Keywords: {', '.join(analysis['keywords'][:10])}")
        if analysis.get("uncertainties"):
            lines.append("")
            lines.append("What RepoPilot could not determine:")
            lines.extend(f"- {item}" for item in analysis["uncertainties"])
        return "\n".join(lines) + "\n"

    def _repository_section(self) -> str:
        repo = self.repository or {}
        analysis = self.repository_analysis or {}
        lines = [
            "## 2. Repository",
            "",
            f"- Source: {repo.get('name') or repo.get('source_url') or '(unknown)'}",
            f"- Branch: {repo.get('branch') or '(default)'}",
            f"- Commit: {repo.get('commit_sha') or '(unknown)'}",
        ]
        if analysis:
            languages = analysis.get("languages") or {}
            language_summary = ", ".join(f"{name} ({count})" for name, count in list(languages.items())[:4])
            lines += [
                f"- Languages: {language_summary or '(none detected)'}",
                f"- Source files: {len(analysis.get('source_files', []))}"
                f"   Test files: {len(analysis.get('test_files', []))}",
                f"- Package managers: {', '.join(analysis.get('package_managers', [])) or '(none detected)'}",
                f"- Detected test commands: {', '.join(analysis.get('test_commands', [])) or '(none detected)'}",
            ]
        return "\n".join(lines) + "\n"

    def _impact_section(self) -> str:
        impact = self.impact or {}
        files = impact.get("affected_files", [])
        lines = [
            "## 3. Predicted impact (blast radius)",
            "",
            f"{len(files)} file(s) predicted, confidence {impact.get('confidence', 0)} "
            "(an estimate from static analysis, never a guarantee).",
        ]
        if files:
            lines += ["", "| File | Kind | Why it is in scope |", "| --- | --- | --- |"]
            for item in files[:20]:
                reason = "; ".join(item.get("reasons", [])[:2]) or "keyword match"
                lines.append(f"| `{item['path']}` | {item.get('kind', 'source')} | {reason} |")
        else:
            lines.append("\n_No file could be linked to this issue._")

        direct = [item for item in files if item.get("distance", 0) == 0]
        indirect = [item for item in files if item.get("distance", 0) > 0]
        if indirect:
            lines += [
                "",
                f"**Dependencies:** {len(direct)} file(s) implicated directly; "
                f"{len(indirect)} reached through the import graph.",
            ]
        if impact.get("potentially_affected_behavior"):
            lines += ["", "**Behaviour that could move:** " + ", ".join(impact["potentially_affected_behavior"])]
        if impact.get("uncertainties"):
            lines += ["", "Caveats:"] + [f"- {item}" for item in impact["uncertainties"]]
        return "\n".join(lines) + "\n"

    def _plan_section(self) -> str:
        plan = self.plan or {}
        if not plan:
            return "## 4. Implementation plan\n\n_Not reached._\n"
        lines = ["## 4. Implementation plan", "", f"Approval: {self._approval_line()}", ""]
        if plan.get("summary"):
            lines += [plan["summary"], ""]
        for step in plan.get("steps", []):
            lines.append(f"{step['order']}. {step['action']}")
        if plan.get("risks"):
            lines += ["", "Risks identified before execution:"] + [f"- {risk}" for risk in plan["risks"]]
        return "\n".join(lines) + "\n"

    def _tests_section(self) -> str:
        selection = self.test_selection or {}
        lines = ["## 5. Verification", "", "### Tests selected", ""]
        chosen = selection.get("selected_tests", [])
        if chosen:
            for item in chosen[:15]:
                reason = "; ".join(item.get("reasons", [])[:2])
                lines.append(f"- `{item['path']}` - {reason}")
            lines += ["", f"Selection confidence: {selection.get('confidence', 0)}"]
        else:
            lines.append(f"_{selection.get('reason') or 'No targeted selection was made.'}_")

        lines += ["", "### Tests executed", ""]
        if self.targeted_tests:
            lines.append(f"- Targeted: `{self.targeted_tests.get('command', '')}`")
            lines.append(f"  - {self._test_line(self.targeted_tests)}")
        else:
            lines.append("- Targeted: not run")
        if self.broader_tests:
            lines.append(f"- Broader suite: `{self.broader_tests.get('command', '')}`")
            lines.append(f"  - {self._test_line(self.broader_tests)}")
        else:
            lines.append("- Broader suite: not run")
        return "\n".join(lines) + "\n"

    def _failures_section(self) -> str:
        repair = self.repair or {}
        analysis = repair.get("final_analysis") or {}
        attempts = repair.get("attempts", [])
        if not repair:
            return ""
        lines = ["### Failures and repair attempts", ""]
        if analysis:
            lines += [
                f"- Classified as: **{analysis.get('failure_type', 'unknown')}** "
                f"(hypothesis, confidence {analysis.get('confidence', 0)})",
                f"- Likely cause: {analysis.get('likely_cause', '')}",
            ]
            if analysis.get("failing_tests"):
                lines.append(f"- Failing tests: {', '.join(analysis['failing_tests'][:10])}")
            if analysis.get("suggested_fix"):
                lines.append(f"- Suggested direction: {analysis['suggested_fix']}")
        lines.append("")
        lines.append(f"Repair attempts: {repair.get('attempt_count', 0)} (cap enforced by `max_fix_attempts`)")
        for attempt in attempts:
            verdict = "passed" if attempt.get("passed") else "still failing"
            lines.append(f"- Attempt {attempt['attempt']}: {attempt.get('action', '')} -> {verdict}")
        if repair.get("stop_reason"):
            lines.append(f"\nLoop stopped because: `{repair['stop_reason']}`")
        return "\n".join(lines) + "\n"

    def _changes_section(self) -> str:
        changes = self.changes or {}
        lines = ["## 6. Changes", ""]
        if not changes:
            return "\n".join(lines + ["_Not reached._"]) + "\n"

        stats = changes.get("stats", {})
        lines.append(
            f"{stats.get('files_changed', 0)} file(s) changed, "
            f"+{stats.get('insertions', 0)} / -{stats.get('deletions', 0)}"
        )
        for label, key in (("Modified", "modified_files"), ("Created", "created_files"), ("Deleted", "deleted_files")):
            entries = changes.get(key) or []
            if entries:
                lines += ["", f"**{label}:**"] + [f"- `{path}`" for path in entries]
        if not changes.get("all_files"):
            lines.append("\n_The agent produced no changes._")

        unexpected = changes.get("unexpected_files") or []
        lines += ["", "### Out-of-scope changes", ""]
        if unexpected:
            lines.append("These files were changed but were **not** in the predicted blast radius:")
            lines += [f"- `{path}`" for path in unexpected]
            lines.append("\nThat is not necessarily wrong, but it is worth a human look.")
        else:
            lines.append("None - every change landed inside the predicted impact area.")
        return "\n".join(lines) + "\n"

    def _verdict_section(self) -> str:
        lines = ["## 7. Verdict", "", f"**{self.status}** - {STATUS_MEANING.get(self.status, '')}", "", "Warnings:"]
        warnings = list(self.warnings) + list((self.changes or {}).get("warnings", []))
        if warnings:
            lines.extend(f"- {warning}" for warning in warnings)
        else:
            lines.append("- None")
        return "\n".join(lines) + "\n"

    def _artifacts_section(self) -> str:
        if not self.artifacts:
            return ""
        lines = ["## 8. Artifacts", ""]
        lines += [f"- {name}: `{path}`" for name, path in self.artifacts.items()]
        return "\n".join(lines) + "\n"

    def _approval_line(self) -> str:
        if not self.approval:
            return "not reached"
        approved = "Approved" if self.approval.get("approved") else "Rejected"
        mode = self.approval.get("mode", "unknown")
        note = " (no human reviewed this plan)" if mode == "auto" else ""
        return f"{approved} in {mode} mode{note}"

    @staticmethod
    def _test_line(result: dict[str, Any]) -> str:
        if not result:
            return "not run"
        if result.get("timed_out"):
            return "timed out"
        if not result.get("parsed"):
            return f"exit code {result.get('exit_code')} (output not recognised as pytest)"
        return (
            f"{result.get('passed', 0)} passed, {result.get('failed', 0)} failed, "
            f"{result.get('errors', 0)} errors, {result.get('skipped', 0)} skipped "
            f"in {result.get('duration', 0)}s"
        )


def determine_status(
    *,
    approved: bool,
    executed: bool,
    targeted_passed: bool | None,
    broader_passed: bool | None,
) -> str:
    """Decide the headline status, conservatively.

    VERIFIED requires that tests actually ran and passed. A run where nothing
    could be verified is UNVERIFIED, never VERIFIED-by-default - the whole point
    of RepoPilot is that "it probably works" is not a status.
    """
    if not approved:
        return STATUS_REJECTED
    if not executed:
        return STATUS_PLANNED
    if targeted_passed is None:
        return STATUS_UNVERIFIED
    if not targeted_passed:
        return STATUS_FAILED
    if broader_passed is False:
        return STATUS_FAILED
    return STATUS_VERIFIED
