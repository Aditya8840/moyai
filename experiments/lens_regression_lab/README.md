# Moyai regression sensitivity lab

These experiments measure a test suite, not production quality. Offline probes do not call production. The optional publisher creates a separate experimental Lens eval; it never modifies the production eval or dataset. Branch-protection requirements are unchanged

## Measured report

Open [the interactive report](report/2026-10-09/index.html) in a browser, or serve it from this checkout:

```sh
python -m http.server 8768 --bind 127.0.0.1 --directory experiments/lens_regression_lab/report/2026-10-09
```

The report includes an implementation plan, a five-step developer journey, source evidence and real Lens result links. It distinguishes three measurements:

- The original checks detected 8 of 12 deliberately wrong implementations; the expanded checks detected 12 of 12. All four correct controls passed. These are post-hoc repair checks, not an estimate of general detection accuracy
- Real model/SDK executions: baseline 4/4, GPT-4.1 mini 4/4 and the alternate Claude SDK 4/4. A one-turn limit produced four incomplete runs; Lens reported four regressions against the exact baseline. Correctness remains unscored for incomplete runs
- Three historical native SDK failures were reproduced with scripted inference and distinguished from the working implementation. These are transport controls, not live model-quality comparisons

The source and SDK versions used by each experiment are recorded in its JSON proof. The original raw capture is retained alongside a separately interpreted report that leaves incomplete execution correctness unscored. Private answers, credentials, production prompts and per-production-trace identifiers are omitted

## Output mutations

Run without model credentials or network access:

```sh
python -m experiments.lens_regression_lab.offline --output /tmp/moyai-offline-results.json
```

The runner writes four known-correct solutions and twelve deliberately incorrect variants into a disposable workspace. It executes the original hidden verification code pinned in `fixtures/coding_cases_ffcc83e.json` using the production eval driver's `verify_solution`. It then repeats the checks with the strengthened production assertions in the current `evals/coding_cases.json`. Both hashes and explicit verifier labels are recorded; the legacy `current` result fields mean the original smoke checks

The known-bad outputs cover order, input mutation, mixed hashable types, touching endpoints, zero-width intervals, generators, remainder chunks, size one, whitespace, boolean truthiness, and unspecified numeric tokens

The additional assertions were designed after identifying these gaps. A higher score on these same mutations is repair evidence, not an unbiased estimate of future regression detection. Use separately designed held-out mutations and real model/SDK comparisons before claiming general sensitivity

## Real agent variants

Run only in a disposable container. The normal Moyai coding agent has shell and file tools; it must not run with access to a developer's workspace or unrelated credentials

Required environment is the same as `evals.agent.MoyaiAgent.from_env`: `LITELLM_API_BASE`, `LITELLM_API_KEY`, `AGENT_MODEL`, `LITELLM_TRACE_ENDPOINT`, `LITELLM_TRACE_API_KEY`, and `LENS_VERSION`. Set the version to the checked-out full commit SHA. The experiment also requires `MOYAI_REGRESSION_LAB_ISOLATED=1`

```sh
python -m experiments.lens_regression_lab.actual \
  --output /tmp/moyai-actual-results.json \
  --variants baseline,max-turns-1,alternate-model \
  --alternate-model openai/gpt-4.1-mini
```

Variants change one runtime value at a time with `dataclasses.replace`; production source remains untouched. The alternate model must exist on the configured gateway. `alternate-harness` with `--alternate-harness claude-agent-sdk` exercises a different harness while keeping the selected model unchanged; model/harness incompatibility is an execution or configuration failure, not proof of lower model intelligence

Use `--case stable-deduplication` for one case or `--repeats 3` for repeated trials. The default includes all four existing tasks, with prompts from the pinned original snapshot. Each case writes an incremental JSON result with runtime versions, actual model and harness, exact source revision, both verifier hashes and outcomes, elapsed time, and trace identity after acknowledged delivery

Execution errors are separate from completed answers that fail hidden checks. Unexpected infrastructure errors have only their exception type recorded. Credentials and arbitrary model output are omitted from the report. The runner does not return a Lens scorer verdict: upload results to an explicitly separate experimental eval before claiming end-to-end Lens scoring

For that upload, `--lens-records /private/path/results.json` optionally writes a mode-0600 file with the real answers, verification details, trace IDs, and generated solution files (bounded to one million characters). This file is private evidence, not a public report; inspect it before publishing or sending to an evaluation service. It is updated after every case so completed results survive a later interruption

## Tests

```sh
python -m pytest tests/test_lens_regression_lab.py -q
```

Tests verify the measured mutation matrix, the isolation guard, and that a variant changes only its intended model, harness, or model-call budget

## Historical repository task

The provider-routing task starts with the real source slice before Moyai PR #178. Its independent verifier imports the resulting Settings implementation and checks provider namespaces, aliases, display names, explicit overrides and rejected unknown models. The broken snapshot passes 12/16 checks; the accepted fix passes 16/16. The four failing assertions make this a meaningful negative control

```sh
python -m experiments.lens_regression_lab.repository_case controls --output /tmp/repository-controls.json
python -m experiments.lens_regression_lab.repository_case prepare --workspace /tmp/moyai-routing-task
python -m experiments.lens_regression_lab.repository_case verify --workspace /tmp/moyai-routing-task
```

Run the agent only inside a disposable environment with production-equivalent tools. In particular, Moyai's sandbox installs ripgrep; a container missing it is not a faithful comparison. The `run` subcommand receives the normal `evals.agent_worker` payload over stdin and writes a public report plus optional private results. Hidden checks live outside the task workspace, but this is not an adversarial security boundary: the current harness checkout and history also exist in the container. Inspect tool paths before interpreting a result

## Publish captured executions to Lens

Set `LENS_BASE_URL`, `LENS_API_KEY` and the actual tested `LENS_VERSION` without exposing their values in command arguments. Publish only a complete baseline and complete variants to an isolated name:

```sh
python -m experiments.lens_regression_lab.publish \
  --name moyai-regression-lab-my-experiment \
  --records /private/path/results.json \
  --output /tmp/lens-lab-results.json
```

This uses the versioned HTTP result API used by the SDK. The publisher requires one result per saved case, finishes a passing baseline first, verifies exact baseline pairing, and can resume with the same report after interruption. It fails visibly if Lens selects a different baseline. The existing source eval `moyai-python-coding-regressions` must exist; the script copies its dataset revision and scorer contract into the isolated eval

## What the accompanying CI fix guarantees

Independent file verification and incomplete executions now fail pytest even if the remote judge returns a passing report. Outputs, traces and the report are saved before that failure. Each checkout uses its own frozen dependencies, and SDK selection follows production Settings for the configured model

The server's case badge still follows its configured scorer. A deterministic required-check contract on Lens is a remaining server change. CI deliberately uses `MOYAI_EVAL_MODEL`, so model changes made only in deployment settings are not automatically tested. Four function tasks remain a smoke suite; they do not prove a model/SDK change is safe for broader repository work
