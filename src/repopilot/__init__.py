"""
RepoPilot
=========

Verification-first, repository-level software engineering automation.

RepoPilot is built ON TOP OF mini-swe-agent (https://github.com/SWE-agent/mini-swe-agent),
which is MIT licensed by Kilian A. Lieret and Carlos E. Jimenez. See LICENSE.md and NOTICE.md
in the repository root.

Everything under `minisweagent/` is the original, unmodified upstream project (used here as a
library). Everything under `repopilot/` (this package) is original code written for the
RepoPilot project: the stages around the agent loop - repository ingestion and understanding,
issue and change-impact analysis, implementation planning, a human approval gate, test
selection, structured verification, failure analysis, a bounded repair loop, and an auditable
final report.

We do not claim any code under `minisweagent/` as our own.

Stage modules (each independently importable and unit-tested):

    repopilot.ingestion.github_repo   clone + validate a repository
    repopilot.analysis.repository     lightweight repository map
    repopilot.analysis.issue          issue -> candidate files/tests
    repopilot.analysis.impact         blast radius, with reasons
    repopilot.planning.planner        implementation plan
    repopilot.approval.workflow       human approval checkpoint
    repopilot.execution.agent         thin wrapper over mini-swe-agent
    repopilot.testing.test_selector   targeted test selection
    repopilot.testing.verification    structured test execution
    repopilot.debugging.failure_analyzer  failure classification
    repopilot.debugging.fix_loop      bounded repair loop
    repopilot.reporting.diff          what actually changed
    repopilot.reporting.report        the final, serializable report
    repopilot.pipeline                the stages wired together
"""

__version__ = "0.2.0"
