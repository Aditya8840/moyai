# Pinned Hermes compatibility fixes

The Modal image applies these patches to `Settings.hermes_revision` after copying
the sandbox files. Both runners also refresh the patch files on snapshot restore;
`hermes_compat.py` installs them before importing Hermes. Already patched runtimes
are unchanged. `git apply --check` fails on incompatible upstream changes before
any model or tool runs; it must not silently run the unpatched runtime. A change
to any patch invalidates the image layer used by new sandboxes. Currently active
turns keep their installed runtime until the next sandbox launch.

- `hermes-steering.patch` backports the runtime hunks of
  [NousResearch/hermes-agent@40aa839](https://github.com/NousResearch/hermes-agent/commit/40aa83919e999082badd1df6fafad8e85691b171)
  by FroRaut. After a response arrives, refunded restarts start counting from
  zero. Repeated corrections separated by tool progress no longer exhaust a
  turn-wide limit of three. Consecutive cancellations/fallback restarts with no
  response still stop at the existing limit; provider retries, iteration and
  time budgets are unchanged.
- `hermes-stop-reason.patch` keeps a known exit reason when the transcript ends
  at a tool result. That last transcript role describes where a turn stopped,
  not why it stopped. Unknown exits still get a visible failure response.

When advancing the Hermes pin, remove patches already included upstream and
adapt the remaining patches explicitly. Run the real-runtime regression below;
the unpatched reproduction expectation should be removed once the new pin fixes
the original issue. Do not raise the retry limit or auto-replay a failed turn.

From the Moyai repository, with the pinned Hermes checkout and its Python
interpreter (the same environment used by `test_tool_discovery.py`):

```sh
HERMES_TEST_SOURCE=/path/to/hermes-agent \
HERMES_TEST_PYTHON=/path/to/hermes-env/bin/python \
uv run pytest -q -s tests/test_hermes_steering.py
```

The test patches a private checkout. It exercises the real conversation loop,
Moyai's correction delivery and goal boundary, real file writes, and returned
conversation history. Only model responses/cancellations are scripted. It
reproduces the unpatched failure, then verifies six corrections without a manual
continue, no duplicate tools, bounded failure retries and explicit stop.

For a local browser view of the actual test output, keep those two environment
variables set and run `uv run python scripts/steering_runtime_demo.py`, then open
<http://127.0.0.1:8830>. The page runs the tests on click; it does not connect to
production or substitute precomputed results.
