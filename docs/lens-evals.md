# Run Moyai's Python agent in CI with Lens

Lens owns the regression dataset, scorers, and pass/fail gates. GitHub Actions runs the checked-out Moyai Python code, gives it each saved input, and sends the actual outputs and traces to Lens. No deployed Moyai URL or workspace password is needed.

```python
from lens import Lens
from evals.agent import MoyaiAgent

lens = Lens(base_url=LENS_BASE_URL, api_key=LENS_API_KEY)
with lens.evals.test("moyai-python-coding-regressions") as evaluation:
    for index, case in enumerate(evaluation.cases):
        agent = MoyaiAgent.from_env(workspace=work_dir / f"case-{index}")
        result = agent.run(input=case.input)
        evaluation.record(case, output=result.output, trace_id=result.trace_id)
    evaluation.assert_passed()
```

The SDK executes inside the runner. The runner calls the model gateway for inference and Lens for the dataset, trace ingestion, and scoring. Lens does not execute arbitrary Python or start another GitHub runner.

## 1. Save the coding regression set in Lens

Create `moyai-python-coding-regressions` on your Lens instance from the `input` and `expected` values in [coding_cases.json](../evals/coding_cases.json), then pin the dataset revision in the saved evaluation. Use `agent: "moyai"` and `environment: "lens-eval"`. Keep the saved cases single-turn; the Python coding suite rejects follow-up turns.

The four tasks cover stable deduplication, interval merging, iterable chunking, and strict boolean parsing. Each asks the agent to write a real `solution.py` and run its own tests. The test driver then runs independent checks from the versioned fixture, which the agent has not been shown. Lens stores prompts and expected behavior; executable verification code remains in this repository. Exact input matching prevents an edited or unrelated Lens case from silently using the wrong verifier.

The scored output is a JSON string:

```json
{
  "answer": "The agent's actual final response",
  "verification": {"passed": true, "detail": "4 independent checks passed\n"}
}
```

Configure the saved scorer to require `verification.passed` to be `true`, and judge the answer against the expected behavior. A tool-trace scorer can also require actual tool use. An agent assertion that its own tests passed is not the independent verification result.

## 2. Configure the runner

The workflow installs the pinned Lens SDK from GitHub and Moyai's existing Codex SDK runtime. Set the Lens origin as a repository variable and model/Lens credentials as repository secrets. The workflow maps them to these runtime variables:

| Variable | Purpose |
| --- | --- |
| `LENS_BASE_URL` | Lens origin, such as `https://litellm-lens.onrender.com` |
| `LENS_API_KEY` | Read saved evals and create evaluation runs |
| `LITELLM_TRACE_ENDPOINT` | Lens URL ending in `/v1/traces` |
| `LITELLM_TRACE_API_KEY` | Send agent, model, and tool spans |
| `LITELLM_API_BASE` | Model gateway origin |
| `LITELLM_API_KEY` | Model inference credential, separate from Lens |
| `AGENT_MODEL` | Model enabled on that gateway, compatible with the chosen harness |
| `LENS_VERSION` | Exact full SHA of the Moyai checkout being executed |

Optional: `MOYAI_EVAL_NAME` selects another compatible saved evaluation, `MOYAI_EVAL_HARNESS` selects `codex` (default) or `claude-agent-sdk`, and `MOYAI_EVAL_TIMEOUT` bounds one task (default 240 seconds). `LENS_REPORT_PATH` saves the Lens report for the workflow's PR comparison step.

Use a disposable container for agent execution. Native coding tools run shell commands; an empty directory alone is not a security sandbox. Restrict secret-bearing runs to trusted branches. The Python worker receives its model and trace credentials through a private pipe, forwards inference through Moyai's loopback broker, and gives the native harness a per-run capability instead of the model key. The case workspace starts empty, while broker state and context live in a separate temporary directory.

For a local disposable environment after configuring the variables above:

```sh
RUSTUP_TOOLCHAIN=1.99.0 uv sync --frozen --group eval
LENS_VERSION=$(git rev-parse HEAD) uv run --frozen --group eval pytest evals/test_moyai.py -v --tb=short
```

The optional eval group is needed for the Lens SDK. Normal tests under `tests/` never call a real model or Lens server.

## 3. Compare the base and head revisions on a PR

[The Lens workflow](../.github/workflows/lens-evals.yml) runs the same saved dataset and the same evaluation driver against separate base and head checkouts. Both use the same model and harness. Each execution records its actual source revision; the Python wrapper rejects an incorrect `LENS_VERSION` rather than attributing old code to a new commit.

Lens receives real outputs and linked agent/model/tool spans for every completed case. The driver waits for trace-delivery receipts before recording a successful result. Incomplete turns, pending tools, missing model/tool execution, and rejected trace delivery are errors, not passing cases. Independent case errors are recorded and the remaining cases still run.

The PR report should identify the dataset revision, source SHAs, scorer, case counts, before/after results, and uncertainty. A four-case smoke benchmark is useful for catching a regression but cannot establish broad agent reliability. Identical results should be reported as no measured change, not an improvement. A failed gate fails CI.

### What this test covers

The test uses Moyai's real `create_agent` / `run_conversation` seam, native Codex or Claude adapter, context journal, broker model handling, tool activity, and OTLP tracing. It is a single-turn coding-harness regression test. Its concise coding-system instructions are fixed by the evaluation driver.

It does **not** start the production Modal/Substrate/Lambda lifecycle, Temporal scheduling, Slack, multi-agent coordination, or the full production session prompt. Those still need their own integration tests. The separate [deployment readiness checker](../evals/preflight.py) remains available for testing a deployed Moyai service; it is not required by the Python-in-CI flow.

## Troubleshooting

- **Checkout mismatch:** make `LENS_VERSION` the revision actually checked out, for both base and head. Do not use a PR merge SHA while executing its head SHA.
- **Input mismatch:** sync the saved dataset revision with `coding_cases.json`. Never execute verification code returned by the server.
- **Model failure:** check the gateway credential, model name, Responses support for Codex, and runner network access. Provider failure is not evidence that the code passed.
- **Trace timeout:** check the Lens trace endpoint/key and receiver health. Configuration alone does not prove trace delivery.
- **No compatible baseline:** run the same saved dataset/scorer definition against the actual base revision before comparing the head.
- **Verification failure:** open the linked Lens trace and case output. Check the implementation created by the agent and the independent verification result.
