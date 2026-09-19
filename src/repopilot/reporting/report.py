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
        """The human-readable summary printed at the end of a run."""
        repo = self.repository
        changes = self.changes or {}
        lines = [
            "# RepoPilot Report",
            "",
            f"**Run:** {self.run_id}",
            f"**Issue:** {self.issue}",
            f"**Repository:** {repo.get('name') or repo.get('source_url') or '(unknown)'}",
            f"**Branch:** {repo.get('branch') or '(default)'}",
            f"**Commit:** {repo.get('commit_sha') or '(unknown)'}",
            "",
            f"**Impact:** {len(self.impact.get('affected_files', []))} file(s) predicted",
            f"**Plan:** {self._approval_line()}",
            "",
            "## Files changed",
        ]
        all_files = changes.get("all_files") or []
        if all_files:
            lines.extend(f"- `{path}`" for path in all_files)
            stats = changes.get("stats", {})
            lines.append("")
            lines.append(
                f"_{stats.get('files_changed', 0)} file(s), "
                f"+{stats.get('insertions', 0)} / -{stats.get('deletions', 0)}_"
            )
        else:
            lines.append("- (none)")

        lines += ["", "## Verification", f"- Targeted tests: {self._test_line(self.targeted_tests)}"]
        lines.append(f"- Broader tests: {self._test_line(self.broader_tests)}")
        lines.append(f"- Repair attempts: {self.repair.get('attempt_count', 0)}")
        if self.repair.get("stop_reason"):
            lines.append(f"- Loop stopped because: {self.repair['stop_reason']}")

        lines += ["", f"## Final status: {self.status}", "", "## Warnings"]
        warnings = list(self.warnings) + list(changes.get("warnings", []))
        if warnings:
            lines.extend(f"- {warning}" for warning in warnings)
        else:
            lines.append("- None")

        if self.artifacts:
            lines += ["", "## Artifacts"] + [f"- {name}: `{path}`" for name, path in self.artifacts.items()]
        return "\n".join(lines)

    def _approval_line(self) -> str:
        if not self.approval:
            return "not reached"
        approved = "Approved" if self.approval.get("approved") else "Rejected"
        return f"{approved} ({self.approval.get('mode', 'unknown')} mode)"

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
            f"{result.get('errors', 0)} errors"
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
