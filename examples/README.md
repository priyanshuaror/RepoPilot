# RepoPilot examples

A deterministic, offline walk-through of the whole RepoPilot workflow.

```bash
python examples/run_demo.py
```

No API key. No network. No LLM call. It runs on any machine with Python and git.

## What is in here

| Path | What it is |
| --- | --- |
| `demo_repo/` | A tiny warehouse-inventory package used as the target repository |
| `demo_issue.md` | The issue text fed into RepoPilot |
| `run_demo.py` | The demo runner |

`demo_repo/` is **not** stored as a git repository — a nested `.git` inside this
project would be confusing. The demo copies it to a temporary directory and runs
`git init` there, so your working tree is never touched.

## What is real and what is stubbed

Every stage of the production pipeline runs for real:

ingestion → repository analysis → issue analysis → impact analysis → planning →
approval → execution → test selection → test execution → failure analysis →
repair loop → diff → report.

The one substitution is the **model**. Instead of asking an LLM to write the fix,
`ScriptedAgent` applies a pre-written patch through the same three-method
interface `repopilot.execution.agent` uses against a real mini-swe-agent agent
(`run()`, `messages`, `save()`).

That substitution goes through `pipeline.run(agent_factory=...)` — the same
injection point the test suite uses — so the demo exercises the real
architecture rather than a parallel "demo mode" code path. Nothing in
`src/repopilot/` knows the demo exists.

## The scenario

`demo_repo` has a genuine bug. `inventory.stock.reserve()` checks that there is
*enough* stock but never checks that the requested quantity is positive, so
reserving a negative quantity increases the shelf count:

```python
>>> reserve("widget", -10, {"widget": 5})
{'widget': 15}
```

Watch for these in the output:

- **Impact analysis** picks `src/inventory/stock.py` from the issue text, then
  reaches `src/inventory/orders.py` through the import graph — and explains why
  each one is in scope. `src/inventory/reporting.py` is included only weakly, by
  directory.
- **Test selection** picks `tests/test_stock.py` first, because it imports the
  module being changed.
- **Out-of-scope detection** reports nothing, because the scripted patch stays
  inside the predicted blast radius.
- **Final status** is `VERIFIED` — and only because tests actually ran and passed.

## Flags

```bash
python examples/run_demo.py --keep        # keep the run directory; prints its path
python examples/run_demo.py --fail-first  # first attempt is broken
```

`--fail-first` is the interesting one. The first patch uses `quantity < 0`
instead of `quantity <= 0`, so the regression test for `quantity == 0` fails.
You then see failure analysis classify it as an assertion failure, the bounded
repair loop spend one of its two attempts, and the re-run pass.

## Requirements

The demo shells out to `python -m pytest` inside the fixture repository, so
pytest must be installed in the environment you run it from:

```bash
pip install -e ".[dev]"
```

Without pytest the pipeline still completes every analysis stage, but the
verification step cannot run and the demo exits `FAILED` with an `import_error`
classification — which is, at least, the system correctly refusing to call
something verified when it was not.

## Exit codes

`0` when the run ends `VERIFIED`, `1` otherwise. That makes the demo usable as a
smoke test in CI.
