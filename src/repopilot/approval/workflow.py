"""
Phase 6 - the human approval checkpoint.

Nothing in the repository is modified until a plan has been approved. The gate
is deliberately a plain function over a plan plus a "how do we ask?" callable,
so the same workflow serves the CLI today and a web dashboard later: a dashboard
just supplies a different ``prompt`` implementation (or calls
:func:`record_decision` directly with the answer a user clicked).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from repopilot.planning.planner import ImplementationPlan

#: Signature of an approval prompt: takes the rendered plan, returns yes/no.
PromptFn = Callable[[str], bool]


@dataclass
class ApprovalDecision:
    """The outcome of the approval gate - recorded in the final report."""

    approved: bool
    mode: str
    reason: str = ""
    decided_at: float = 0.0
    decided_by: str = "human"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalRejected(RuntimeError):
    """Raised when a caller demands approval and the human declined."""


def record_decision(approved: bool, *, mode: str, reason: str = "", decided_by: str = "human") -> ApprovalDecision:
    """Build a timestamped decision. A dashboard can call this directly."""
    return ApprovalDecision(
        approved=approved,
        mode=mode,
        reason=reason,
        decided_at=time.time(),
        decided_by=decided_by,
    )


def console_prompt(rendered_plan: str) -> bool:
    """Default interactive prompt: print the plan, ask for y/N on stdin."""
    print(rendered_plan)
    answer = input("\nApprove implementation? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def request_approval(
    plan: ImplementationPlan,
    *,
    mode: str = "interactive",
    prompt: PromptFn | None = None,
) -> ApprovalDecision:
    """Ask for permission to implement ``plan``.

    Modes
    -----
    ``interactive``: show the plan and ask a human (via ``prompt``).
    ``auto``: approve without asking - for CI or batch runs. Recorded as such,
        so a report never implies a human looked at it when none did.
    ``dry-run``: never approve; RepoPilot stops after planning and the repository
        is left untouched.
    """
    if mode == "auto":
        return record_decision(
            True,
            mode=mode,
            reason="approval_mode=auto (no human reviewed this plan)",
            decided_by="config",
        )
    if mode == "dry-run":
        return record_decision(
            False,
            mode=mode,
            reason="approval_mode=dry-run: analysis only, no modification",
            decided_by="config",
        )
    if mode != "interactive":
        raise ValueError(f"Unknown approval mode: {mode!r}")

    ask = prompt or console_prompt
    approved = bool(ask(plan.to_markdown()))
    return record_decision(
        approved,
        mode=mode,
        reason="approved at the interactive checkpoint" if approved else "rejected at the interactive checkpoint",
    )


def require_approval(
    plan: ImplementationPlan, *, mode: str = "interactive", prompt: PromptFn | None = None
) -> ApprovalDecision:
    """Like :func:`request_approval`, but raises :class:`ApprovalRejected` on a no."""
    decision = request_approval(plan, mode=mode, prompt=prompt)
    if not decision.approved:
        raise ApprovalRejected(decision.reason)
    return decision
