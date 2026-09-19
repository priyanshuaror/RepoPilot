"""
A deterministic, offline end-to-end RepoPilot demo.

    python examples/run_demo.py

Every stage of the real pipeline runs for real - ingestion, repository analysis,
issue analysis, impact analysis, planning, approval, execution, test selection,
verification, failure analysis, the repair loop, diff and report. The single
thing that is stubbed is the *model*: instead of asking an LLM to write the fix,
a `ScriptedAgent` applies a pre-written patch through the same interface
mini-swe-agent's agent exposes (``run()``, ``messages``, ``save()``).

That boundary is deliberate. Swapping the agent is exactly what
``pipeline.run(agent_factory=...)`` is for in production, so the demo exercises
the real architecture rather than a parallel "demo mode" implementation. No API
key, no network, and no dependency on any particular machine: the fixture
repository in ``examples/demo_repo/`` is copied to a temporary directory and
turned into a git repository at runtime.

Flags:
    --keep        leave the run directory on disk and print its path
    --fail-first  make the first attempt fail, to show failure analysis and the
                  bounded repair loop doing their job
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
if str(REPO_ROOT / "src") not in sys.path:  # allow running from a plain checkout
    sys.path.insert(0, str(REPO_ROOT / "src"))

from repopilot.config.settings import RepoPilotConfig  # noqa: E402
from repopilot.pipeline import run  # noqa: E402

DEMO_REPO = EXAMPLES_DIR / "demo_repo"
DEMO_ISSUE = (EXAMPLES_DIR / "demo_issue.md").read_text(encoding="utf-8")

#: The fix a real agent would be asked to write. Held here so the demo is
#: byte-for-byte reproducible on every machine and in CI.
FIXED_STOCK = '''"""Stock levels for the warehouse."""


class OutOfStockError(Exception):
    """Raised when a reservation cannot be satisfied."""


def available(item, inventory):
    """Units of `item` currently on the shelf."""
    return inventory.get(item, 0)


def reserve(item, quantity, inventory):
    """Reserve `quantity` units of `item`, returning the updated inventory.

    Raises
    ------
    ValueError: if `quantity` is not strictly positive.
    OutOfStockError: if there is not enough stock.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if available(item, inventory) < quantity:
        raise OutOfStockError(f"only {available(item, inventory)} units of {item} left")
    updated = dict(inventory)
    updated[item] = updated.get(item, 0) - quantity
    return updated
'''

REGRESSION_TEST = '''import pytest

from inventory.orders import place_order
from inventory.stock import reserve


@pytest.mark.parametrize("quantity", [0, -1, -10])
def test_reserve_rejects_non_positive_quantity(quantity):
    with pytest.raises(ValueError):
        reserve("widget", quantity, {"widget": 5})


def test_place_order_surfaces_the_rejection():
    with pytest.raises(ValueError):
        place_order("widget", -10, {"widget": 5})
'''

#: A deliberately broken first attempt, used by --fail-first.
BROKEN_STOCK = FIXED_STOCK.replace("if quantity <= 0:", "if quantity < 0:")


class ScriptedAgent:
    """Stands in for a mini-swe-agent agent, applying a fixed patch.

    Implements the same three things ``repopilot.execution.agent`` uses from a
    real agent: ``run(task, **kwargs)`` returning an exit status and submission,
    a ``messages`` list in mini-swe-agent's trajectory shape, and ``save(path)``.
    """

    def __init__(self, repo_path: Path, edits: dict[str, str], label: str) -> None:
        self.repo_path = Path(repo_path)
        self.edits = edits
        self.label = label
        self.messages: list[dict] = []

    def run(self, task: str, **kwargs) -> dict:
        commands = []
        for relative, content in self.edits.items():
            path = self.repo_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            commands.append(f"edit {relative}")
        self.messages = _trajectory(commands)
        return {"exit_status": "Submitted", "submission": self.label}

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.messages, indent=2), encoding="utf-8")


def _trajectory(commands: list[str]) -> list[dict]:
    """Build messages in the shape mini-swe-agent's tool-calling models produce."""
    messages: list[dict] = []
    for index, command in enumerate(commands):
        call_id = f"call_{index}"
        messages.append(
            {"role": "assistant", "extra": {"actions": [{"command": command, "tool_call_id": call_id}]}}
        )
        messages.append(
            {"role": "tool", "tool_call_id": call_id, "extra": {"raw_output": "ok", "returncode": 0}}
        )
    return messages


def prepare_demo_repository(destination: Path) -> Path:
    """Copy the fixture project to ``destination`` and make it a git repository.

    The fixture is not stored as a git repository (a nested ``.git`` inside this
    project would be confusing), so it is initialised here at runtime.
    """
    destination = Path(destination)
    shutil.copytree(DEMO_REPO, destination)
    git = lambda *args: subprocess.run(["git", *args], cwd=destination, check=True, capture_output=True)  # noqa: E731
    git("init")
    git("config", "user.email", "demo@example.com")
    git("config", "user.name", "RepoPilot demo")
    git("add", "-A")
    git("commit", "-m", "initial commit")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline RepoPilot demo.")
    parser.add_argument("--keep", action="store_true", help="keep the run directory and print its path")
    parser.add_argument(
        "--fail-first",
        action="store_true",
        help="make the first attempt fail, to demonstrate failure analysis and the repair loop",
    )
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="repopilot-demo-"))
    source = prepare_demo_repository(workdir / "source")
    output_dir = workdir / "run"

    attempts = {"n": 0}

    def agent_factory(repo_path: Path) -> ScriptedAgent:
        attempts["n"] += 1
        first = attempts["n"] == 1
        if args.fail_first and first:
            return ScriptedAgent(
                repo_path,
                {"src/inventory/stock.py": BROKEN_STOCK, "tests/test_negative_quantity.py": REGRESSION_TEST},
                "guarded against negative quantities",
            )
        return ScriptedAgent(
            repo_path,
            {"src/inventory/stock.py": FIXED_STOCK, "tests/test_negative_quantity.py": REGRESSION_TEST},
            "rejected non-positive quantities and added a regression test",
        )

    config = RepoPilotConfig(approval_mode="auto", max_fix_attempts=2, run_broader_tests=True)

    print("=" * 78)
    print("RepoPilot demo - no API key, no network, no LLM call")
    print(f"Fixture repository: {source}")
    print("=" * 78)

    result = run(
        str(source),
        DEMO_ISSUE.strip(),
        config,
        output_dir=output_dir,
        run_id="demo",
        agent_factory=agent_factory,
        progress=lambda stage, payload: print(f"  -> {stage}"),
    )

    print()
    print(result.report.to_markdown())

    if args.keep:
        print(f"\nRun directory kept at: {output_dir}")
        print("  report.json / report.md / changes.diff / trajectory.json")
    else:
        shutil.rmtree(workdir, ignore_errors=True)

    return 0 if result.report.status == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
