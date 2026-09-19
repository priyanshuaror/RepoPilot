# Notice

RepoPilot is built **on top of** [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent),
created by Kilian A. Lieret and Carlos E. Jimenez, and distributed under the MIT License
(see `LICENSE.md`, unchanged from upstream).

## What is original upstream code

Everything under `src/minisweagent/` is the mini-swe-agent project, used here as a library.
It has **not** been modified, renamed, or claimed as original work. It provides the core
agent loop (reason -> bash tool call -> observe -> repeat), the model integrations
(litellm/openrouter/portkey), and the local/docker/singularity execution environments.

`pyproject.toml` is upstream's, with two additive changes: the `repopilot` console script
and the inclusion of `repopilot*` in the packaged modules.

## What is original RepoPilot code

Everything under `src/repopilot/` and `tests/repopilot/` is written for this project.
RepoPilot contributes the stages *around* the upstream agent loop - it does not reimplement
the loop itself.

### Ingestion and understanding

- `ingestion/github_repo.py` - clones and validates a live repository + branch, tracks the
  checked-out commit, and manages the workspace directory.
- `analysis/repository.py` - one-pass repository map: languages, source/test/config files,
  package managers, detected test commands, entry points, important directories.

### Reasoning about the change

- `analysis/issue.py` - turns a free-text issue into keywords, explicitly named paths, and
  ranked candidate files/tests, keeping *detected facts* separate from *inferred candidates*
  and recording what it could not determine.
- `analysis/impact.py` - change impact analysis: the estimated blast radius, expanded through
  a Python import graph, with a stated reason attached to every file included.
- `planning/planner.py` - a structured implementation plan (steps, tests, risks, assumptions)
  that is both machine-readable and human-readable.

### Control and execution

- `approval/workflow.py` - the human approval checkpoint. Nothing is modified before a
  recorded decision; the prompt is injectable so a web dashboard can reuse the workflow.
- `execution/agent.py` - a thin wrapper that builds the task prompt from the approved plan
  and runs the **unmodified** mini-swe-agent agent inside the checkout.
- `config/settings.py`, `config/repopilot.yaml` - RepoPilot's settings and its own
  system/instance prompts. No secrets: API keys are read from the environment.
- `pipeline.py` - the stages wired together, with injection points for the agent and the
  test runner so the whole workflow is unit-testable.
- `cli.py` - the `repopilot` command (`analyze` / `plan` / `run` / `report`).

### Verification

- `testing/trajectory_scan.py` - pairs every bash command the agent ran with its output, via
  the `tool_call_id` mechanism mini-swe-agent uses internally.
- `testing/pytest_parser.py` - parses raw pytest console output into structured counts and
  failing-test names.
- `testing/test_selector.py` - picks the tests relevant to the changed files, with reasons.
- `testing/verification.py` - structured test execution: exit code, duration, counts, timeout
  handling, truncated output.
- `debugging/failure_analyzer.py` - classifies a failing run (syntax, import, assertion,
  dependency, configuration, environment, ...) as an explicit hypothesis, never a certainty.
- `debugging/fix_loop.py` - the bounded repair loop, with caps on attempts and wall-clock
  time, and stops on repeated-identical and environmental failures.

### Reporting

- `reporting/diff.py` - what the agent actually changed: modified/created/deleted files,
  diff statistics, the patch itself, and warnings for changes outside the predicted impact area.
- `reporting/report.py` - the final serializable run report plus its human-readable summary.

See the README's RepoPilot section for the workflow, limitations and roadmap.
