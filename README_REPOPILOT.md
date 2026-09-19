# RepoPilot

A repository-level software engineering agent that estimates the impact of a change before making it, asks a human to approve the plan, and refuses to call the result verified unless tests actually passed.

Built on top of [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent).

---

## Problem

Pointing an LLM at a repository and asking it to fix an issue is easy. Trusting the result is not. Five things tend to be missing:

**Understanding repository structure.** An agent that has to rediscover the layout on every run wastes tokens on `ls` and `find`, and still misses the test runner.

**Determining change impact.** Editing `auth/jwt.py` is rarely a local act. What imports it? What tests cover it? Which behaviour moves? Without an answer *before* the edit, a reviewer has no basis for deciding whether to let the agent proceed.

**Selecting relevant tests.** Running the whole suite first is slow, and worse, it buries the one signal that matters: did *this* change do what it was supposed to?

**Verifying changes.** "I made the change and it looks correct" is not verification. Neither is a green suite that never exercised the changed line.

**Diagnosing failures.** A missing dependency, a syntax error and a genuinely wrong assertion all look like "tests failed" and call for completely different responses. An agent that treats them identically will happily patch code to work around a broken network.

RepoPilot's design goal, stated once so the rest of this README can be judged against it:

> **Verification-first repository-level automation, with explicit impact analysis and human approval.**

---

## What RepoPilot Does

```
Issue
  → Repository Scan
  → Issue Analysis
  → Impact Analysis
  → Implementation Plan
  → Human Approval
  → Code Changes
  → Relevant Tests
  → Verification
  → Failure Analysis
  → Repair Loop
  → Final Report
```

Nothing in the target repository is modified before the approval step. Everything after it is recorded.

---

## Key Features

| Feature | What it does |
| --- | --- |
| **Repository ingestion** | Clones a URL or local path at a chosen branch, records the commit SHA, validates the checkout, manages the workspace |
| **Repository analysis** | One filesystem pass: languages, source/test/config files, package managers, detected test commands, entry points |
| **Issue analysis** | Extracts keywords and explicitly named paths; ranks candidate files and tests; keeps *detected facts* separate from *inferred candidates* and records what it could not determine |
| **Change-impact analysis** | The blast radius: seed files from the issue, expanded through the Python import graph, with a stated reason attached to every file included |
| **Implementation planning** | Ordered steps, tests to run, risks and assumptions — machine-readable and human-readable |
| **Human approval** | The gate between planning and modification. Injectable prompt, so the same workflow serves a CLI today and a dashboard later |
| **mini-SWE-agent execution** | The unmodified upstream agent loop, handed the approved plan and the predicted blast radius as context |
| **Relevant test selection** | Picks tests that import or mirror the changed files, with a reason per test and a confidence figure |
| **Test verification** | Structured execution: exit code, duration, pass/fail/error counts, failing test names, timeout handling |
| **Failure analysis** | Classifies a failing run into one of ten categories as an explicit hypothesis, and detects environmental failures it should not try to repair |
| **Bounded repair loop** | Caps on attempts and wall-clock time; stops on repeated-identical failures and on environmental ones |
| **Diff analysis** | Modified, created and deleted files, insertion/deletion counts, and the patch itself |
| **Unexpected-change detection** | Flags files changed outside the predicted blast radius — not an error, but always a reviewer's business |
| **JSON / Markdown reporting** | One machine-readable report and one readable summary from the same data |

---

## Architecture

```
                      ┌──────────────────────────┐
   GitHub URL ───────▶│  ingestion/github_repo   │  clone, validate, record SHA
   + issue text       └────────────┬─────────────┘
                                   ▼
                      ┌──────────────────────────┐
                      │  analysis/repository     │  languages, tests, commands
                      └────────────┬─────────────┘
                                   ▼
                      ┌──────────────────────────┐
                      │  analysis/issue          │  facts | candidates | unknowns
                      └────────────┬─────────────┘
                                   ▼
                      ┌──────────────────────────┐
                      │  analysis/impact         │  blast radius + reasons
                      └────────────┬─────────────┘
                                   ▼
                      ┌──────────────────────────┐
                      │  planning/planner        │  steps, tests, risks
                      └────────────┬─────────────┘
                                   ▼
                      ╔══════════════════════════╗
                      ║  approval/workflow       ║  ◀── HUMAN GATE
                      ╚════════════╦═════════════╝      nothing is modified above
                       rejected ◀──╫──▶ approved         this line
                                   ▼
                      ┌──────────────────────────┐
                      │  execution/agent         │──▶ minisweagent (UNMODIFIED)
                      └────────────┬─────────────┘     reason → bash → observe
                                   ▼
                      ┌──────────────────────────┐
                      │  testing/test_selector   │  targeted tests first
                      └────────────┬─────────────┘
                                   ▼
                      ┌──────────────────────────┐
                      │  testing/verification    │──┐
                      └────────────┬─────────────┘  │
                            pass   │   fail         │
                                   │                │
                      ┌────────────▼─────────────┐  │
                      │ debugging/failure_analyzer│ │
                      └────────────┬─────────────┘  │
                                   ▼                │
                      ┌──────────────────────────┐  │
                      │  debugging/fix_loop      │──┘ bounded: attempts, time,
                      └────────────┬─────────────┘    repeated failures
                                   ▼
                      ┌──────────────────────────┐
                      │  reporting/diff + report │  JSON + Markdown
                      └──────────────────────────┘

   pipeline.py orchestrates all of the above.   cli.py is a thin wrapper over it.
```

Every stage returns a serializable dataclass, and every stage is importable on its own. `pipeline.run()` takes `agent_factory` and `test_runner` parameters, which is how the suite tests the whole workflow without an API key — and how the demo runs offline.

The analysis stages are **deterministic static analysis, not LLM calls**. Discovering that a repository has a `tests/` directory should not cost an API request. The model is spent on the part that needs judgement: writing the code.

---

## Example

Run it yourself — no API key needed:

```bash
python examples/run_demo.py
```

The fixture repository has a real bug: `reserve()` checks that there is enough stock but never checks that the quantity is positive, so reserving `-10` widgets *creates* stock. Abridged output:

```
## 3. Predicted impact (blast radius)

7 file(s) predicted, confidence 0.52 (an estimate from static analysis, never a guarantee).

| File                          | Kind   | Why it is in scope                          |
| ----------------------------- | ------ | ------------------------------------------- |
| `src/inventory/stock.py`      | source | filename matches keyword 'stock'            |
| `src/inventory/orders.py`     | source | imports 'inventory.stock' (depth 1)         |
| `tests/test_stock.py`         | test   | imports 'inventory.stock' (depth 1)         |

**Dependencies:** 4 file(s) implicated directly; 3 reached through the import graph.

## 5. Verification

### Tests selected
- `tests/test_stock.py` - references 'stock' from src/inventory/stock.py; filename mirrors it

### Tests executed
- Targeted: `python -m pytest tests/test_stock.py ... -q`
  - 6 passed, 0 failed, 0 errors

## 6. Changes
2 file(s) changed, +21 / -2

### Out-of-scope changes
None - every change landed inside the predicted impact area.

## 7. Verdict
**VERIFIED** - the change was made and the selected tests passed
```

`python examples/run_demo.py --fail-first` scripts a broken first attempt, so you can watch failure analysis classify it and the repair loop spend one of its two attempts. See [`examples/README.md`](examples/README.md).

---

## Installation

```bash
git clone https://github.com/priyanshuaror/RepoPilot
cd RepoPilot
pip install -e ".[dev]"
```

Requires Python 3.10+ and git on `PATH`.

Set a model API key in the environment for real runs (never in a config file):

```bash
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY / OPENROUTER_API_KEY
```

On Windows PowerShell: `$env:ANTHROPIC_API_KEY = "..."`

The analysis-only commands (`analyze`, `plan`) and the demo need no key at all.

---

## Usage

```bash
# Read-only. Clone, map the repository, print the blast radius. Modifies nothing.
repopilot analyze --repo https://github.com/example/project \
                  --issue "Expired JWTs are still accepted by require_auth"

# Analysis plus an implementation plan, written to plan.md.
repopilot plan --repo https://github.com/example/project \
               --issue "Expired JWTs are still accepted by require_auth"

# The full workflow, with the approval prompt.
repopilot run --repo https://github.com/example/project \
              --issue "Expired JWTs are still accepted by require_auth"

# Re-print a previous run's report.
repopilot report --run-id 20260101-120000
```

Flags on `run`: `--yes` (approve without prompting — recorded as unreviewed), `--dry-run` (stop after planning), `--branch`, `--model`, `--max-fix-attempts`, `--output-dir`, `--config`.

Exit codes: `0` success, `1` the run failed, `2` your input was wrong (empty issue, missing config file, conflicting flags).

Each run writes a directory containing `report.json`, `report.md`, `changes.diff` and `trajectory.json`.

---

## Configuration

`src/repopilot/config/repopilot.yaml` holds two things: RepoPilot's own settings under `repopilot:`, and the `agent:` / `model:` / `environment:` blocks passed to mini-swe-agent unchanged.

```yaml
repopilot:
  approval_mode: interactive   # interactive | auto | dry-run
  max_fix_attempts: 3          # hard cap on repair iterations
  test_timeout: 900            # seconds, per test command
  run_timeout: 3600            # soft budget for the verification phase
  clone_depth: 1               # null for a full clone
  run_broader_tests: true      # run the full suite after targeted tests pass
  test_commands: []            # empty => detect from the repository
  ignored_dirs: [.git, __pycache__, node_modules, .venv, dist, build]
```

Use your own file with `--config path/to/repopilot.yaml`. A `--config` path that does not exist is an error, not a silent fallback.

**Secrets are never read from configuration.** API keys come from the environment only; `RepoPilotConfig` has no field that could hold one, and there is a test asserting that.

---

## Testing

```bash
python -m pytest tests/repopilot -v   # RepoPilot's own suite
python -m pytest -v                   # including the upstream mini-swe-agent tests
```

The suite is deterministic and hermetic: **no network, no GitHub API, no LLM calls**. Repositories under test are real local git checkouts built by fixtures; the agent and the test runner are injected fakes, which is exactly what `pipeline.run(agent_factory=..., test_runner=...)` exists for.

Coverage by area: repository analysis, issue analysis, impact analysis, planner, approval workflow, test selection, pytest parsing, trajectory scanning, structured verification, failure classification, the repair loop, diff and change summary, report generation, configuration, the CLI, the demo, and edge cases (empty repository, repository with no tests, unavailable test command, timeout, out-of-scope changes, broader-suite regression).

Subprocess tests use `python -c` rather than shell built-ins like `ls`, so they pass on Windows as well as Linux and macOS.

---

## Safety / Execution Boundaries

RepoPilot executes AI-generated shell commands. Stated plainly:

**Execution is NOT sandboxed by default.** Commands run through mini-swe-agent's *local* environment, as the current user, on the host machine.

What actually constrains a run:

- **Working directory.** The environment's `cwd` is pinned in Python to the ingested checkout (`execution/agent.py`), not left to a config file. Commands start inside the clone, never the directory you invoked RepoPilot from.
- **Approval modes.** `interactive` requires a human `y` before anything is modified. `auto` approves without asking and *records in the report that no human reviewed the plan*. `dry-run` never approves: analysis and planning only, repository untouched.
- **Dry-run behaviour.** In `dry-run`, no agent is ever constructed. There is a test asserting the agent factory is not called.
- **Timeouts.** `test_timeout` kills a single test command; a timeout is recorded as a failed result, never mistaken for a pass. `run_timeout` bounds the verification phase.
- **Repair-loop limits.** `max_fix_attempts` is a hard cap. The loop also stops on a repeated identical failure signature (the fix is not converging) and on a failure classified as environmental (not the agent's to repair).
- **Conservative status.** `VERIFIED` requires that tests ran and passed. No test run means `UNVERIFIED`, never `VERIFIED` by default.
- **Logging.** Every command the agent ran, with its output and return code, is in `trajectory.json` and in the report's execution record.

**Docker sandboxing is not implemented.** mini-swe-agent ships a Docker environment and RepoPilot's execution layer is structured to accept one, but it is not wired up and is not the default. Do not treat a RepoPilot run as isolated. Run against repositories you trust, ideally in a VM or container you control.

---

## Project Structure

```
src/repopilot/
├── __init__.py
├── cli.py                      # analyze / plan / run / report
├── pipeline.py                 # stage orchestration (no typer dependency)
├── analysis/
│   ├── repository.py           # repository map
│   ├── issue.py                # issue → candidate files/tests
│   └── impact.py               # blast radius + import graph
├── approval/
│   └── workflow.py             # human gate, injectable prompt
├── config/
│   ├── settings.py             # RepoPilotConfig, YAML loader
│   └── repopilot.yaml          # settings + mini-swe-agent prompts
├── debugging/
│   ├── failure_analyzer.py     # failure classification
│   └── fix_loop.py             # bounded repair loop
├── execution/
│   └── agent.py                # thin wrapper over mini-swe-agent
├── ingestion/
│   └── github_repo.py          # clone, validate, commit tracking
├── planning/
│   └── planner.py              # implementation plan
├── reporting/
│   ├── diff.py                 # change summary, out-of-scope detection
│   └── report.py               # JSON + Markdown report
└── testing/
    ├── test_selector.py        # relevant test selection
    ├── verification.py         # structured test execution
    ├── pytest_parser.py        # pytest output → structured counts
    └── trajectory_scan.py      # command/output pairs from a trajectory

tests/repopilot/                # the test suite
examples/                       # offline demo: fixture repo, issue, runner
src/minisweagent/               # UNMODIFIED upstream project
```

---

## Relationship to mini-SWE-agent

RepoPilot is built **on top of** [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) by Kilian A. Lieret and Carlos E. Jimenez, MIT licensed.

Everything under `src/minisweagent/` is the upstream project, used as a library. It is **not** modified, renamed, or claimed as original work. It provides the agent loop, the model integrations, and the execution environments.

Everything under `src/repopilot/`, `tests/repopilot/` and `examples/` is written for this project: the stages *around* the loop. RepoPilot does not reimplement the loop, and the upstream `LICENSE.md` is preserved unchanged.

[`NOTICE.md`](NOTICE.md) has a file-by-file breakdown of what is original and what is inherited.

---

## Roadmap

**Implemented**

- [x] Repository ingestion (clone, branch, commit tracking, validation)
- [x] Repository analysis (languages, tests, package managers, test commands)
- [x] Issue analysis separating facts, candidates and unknowns
- [x] Change-impact analysis with per-file justification
- [x] Implementation planner
- [x] Human approval checkpoint with three modes
- [x] mini-SWE-agent execution with plan + blast radius as context
- [x] Relevant test selection
- [x] Structured test execution and pytest output parsing
- [x] Failure classification with environmental-failure detection
- [x] Bounded repair loop
- [x] Diff analysis and out-of-scope change detection
- [x] JSON and Markdown reports
- [x] Deterministic offline demo

**Partially implemented**

- [ ] **Non-Python impact analysis.** The import graph is Python-only. Other languages fall back to path, name and keyword signals, which is weaker and labelled as such in the confidence figure.
- [ ] **Failure classification.** Ten categories, pattern-based. It is a hypothesis with a confidence number, not a diagnosis, and it says so.
- [ ] **pytest parsing.** A text parser over console output, not a pytest plugin. Best-effort on whatever the agent happened to run.

**Future work**

- [ ] Docker execution environment as the default
- [ ] Web dashboard over `report.json`
- [ ] Import-graph impact analysis for JavaScript/TypeScript
- [ ] Direct GitHub issue ingestion (issue number instead of pasted text)
- [ ] Benchmark evaluation against a public dataset

No benchmark numbers are claimed anywhere in this README, because none have been run.
