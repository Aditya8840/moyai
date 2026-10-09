# Independent review of the regression experiments

Reviewed mutations.py, offline.py, actual.py, tests/test_lens_regression_lab.py, the original four prompt specifications, and the worker error path. Did not touch the active Docker experiment or CI

## Confirmed result and its permitted claim

An independent rerun of the real versioned verifier produced **8 of 12 handcrafted incorrect outputs detected with current checks; 12 of 12 with additional checks; 4 of 4 correct control implementations passed both**. The independent artifact is `offline-independent-review.json`

This is a valid sensitivity measurement for those twelve exact handcrafted outputs. The extra checks were selected after observing those gaps. The result proves that these specific holes can be repaired, not a 100% regression detection rate for Moyai, a measured model downgrade, an SDK regression, or unbiased held-out performance. `offline.py:37-48` and README already state this limitation clearly. Preserve that wording in the HTML and do not title the measurement simply "Lens regression recall"

The offline run invokes `verify_solution` from the current driver, but does not invoke an agent, model, Lens API or the Lens judge. A live Lens upload of the same outputs is a separate measurement and must identify which scorer configuration was used

## The extra checks follow the original requests

| Previously surviving mutation | Added check | Why it belongs to the original specification |
| --- | --- | --- |
| Stringify deduplication keys | Distinguish 1, string 1, tuple (1,) and None/string None | Prompt says duplicate hashable values, not duplicate string representations; the values are hashable and distinct |
| Drop zero-width intervals | Preserve [1,1] and [3,3] | Prompt explicitly permits start <= end, including equal endpoints |
| Flatten chunk size one | Return [[1],[2],[3]] when size=1 | Prompt requires a list of lists of at most size elements; positive size one is valid |
| Accept numeric aliases | Reject +1, -0, 1.0 and 0.0 | Prompt permits exactly six trimmed, case-insensitive tokens and rejects every other string |

Negative interval endpoints, chunk size larger than the iterable, and size three are also consistent with the existing contracts. No newly demanded product behavior was smuggled into these checks

The controls are four known-good implementations, not a representative sample of valid implementations. Before broad claims, use independent implementation variants and a separately authored held-out fault set. Freeze that set before adapting checks to its failures

## High-priority classification issue in the real runner

`actual.py:109-112` labels every `AgentRunError` an execution_error and assigns both verifier outcomes False. That exception does not establish an agent-quality failure

`evals/agent_worker.py:157-162` wraps every unexpected exception as a failed worker result, with only its type in a safe message. `evals/agent.py:117-119` then raises AgentRunError for that result. Known AgentRunError paths also include a broker that never became ready, missing required spans, Lens trace acknowledgement timeout, and a combined execution/trace deadline. Thus SDK installation errors, provider authorization/network failures, local resource failures and Lens ingestion failures can all enter the quality-failure bucket

The separate generic-exception infrastructure branch in `actual.py:113-117` does not recover those categories, because the worker already converted them into AgentRunError. A one-model-call mutant may truly fail its agent task, but the generic exception label alone does not prove that causation

For the current HTML, show these rows as **execution did not complete, cause requires diagnosis**, keep correctness **not scored**, and do not count them as caught quality regressions unless trace/log evidence identifies the intended mutation as the reason. Count wrong artifacts from completed runs separately

The production repair should use a typed worker outcome such as completed, agent_limit_reached, agent_incomplete, provider_error, broker_error, trace_delivery_error and configuration_error. Include safe trace identity and delivery state whenever available. Unknown errors remain inconclusive, not evidence of lower model intelligence

## Further measurement limits

`max-turns-1` is a deliberate budget restriction. It tests sensitivity to insufficient execution budget, not an SDK-version swap. An alternate model with the same harness is a legitimate controlled model experiment when the gateway supports that model/harness combination. An alternate harness on an incompatible model is a compatibility failure, not a quality measurement

`actual.py:91-93` runs all baseline cases before the next variant and uses one default repeat. This is a pilot, not a stable comparative rate. Repeat paired cases, alternate or randomize variant order using a recorded seed, and include unchanged baseline repeats to measure false alarms. Record model configuration, installed SDK versions, prompt, tool and fixture fingerprints, source SHA and runtime image

Private solution and answer capture is appropriately separated from the public report. The worker exports normal traces, while the real runner correctly states that its local verifier outcomes are not Lens scorer verdicts. Preserve that distinction in every result card

## Smallest fail-closed correction in the current Moyai test

Keep recording the actual output and trace with `evaluation.record`, even when local hidden checks fail. Collect deterministic verification failures separately while continuing the remaining cases. Finish the Lens run and write the report artifact first. Then fail pytest if either the Lens gate failed **or any independent verifier failed**

This immediately ensures a bad artifact cannot make CI succeed just because an LLM judge disagreed. It preserves the root trace and all completed case evidence. It does not yet guarantee that the Lens case badge itself is deterministic: the current API cannot record an exclusive error together with output/trace, and a local pytest assertion is not a server-side case verdict

The durable Lens change is a typed runner check result attached alongside output/trace, with a deterministic required-check scorer. For example, a `workspace_tests` check carries passed=false and a bounded safe explanation. The judge remains an additional scorer for subjective behavior. Failed runner checks must veto a passing case; missing required checks must remain unscored or failed, never silently pass. Add SDK/API contract tests proving a false check plus successful root trace still fails and still has its trace link

Avoid using `record_error` for a completed run whose generated files fail verification: the current error union excludes the linked output/trace and throws away the very evidence the user needs to debug it

## Smallest runtime and baseline parity correction

Create a frozen environment inside each base/head checkout from that checkout's own lockfile. Run each with its own interpreter. A shared immutable verifier bundle and dataset revision may be used for comparison, but do not copy candidate SDK dependencies or runtime configuration into the baseline

Read model and harness from the same versioned runtime configuration that production uses, or an explicitly captured deployment manifest. Keep a fixed-model smoke lane separately labeled if desired. For a model/SDK change, the candidate manifest must contain that actual change; the baseline retains its original configuration. Record resolved values, not merely requested environment variables

The candidate should receive the exact baseline_run_id produced by its own before execution, with Lens verifying the expected base commit. Newest compatible main is useful for browsing historical runs but is insufficient for a controlled paired regression experiment under concurrent PRs

## Tests and follow-up evidence

The current tests meaningfully verify the observed mutation matrix and correct controls. They do not validate error classification, and `test_should_change_one_agent_configuration_value_per_variant` only checks a few fields despite its broad name. Strengthen that assertion with dataclass equality against an expected single-field replacement

Add behavioral tests that distinguish a deterministic wrong solution, an agent budget stop, a gateway rejection and a trace-delivery timeout. The latter two must not increase a reported quality-regression detection count. Preserve each completed trace when the independent verifier fails

At this initial review stage, no CI change had been made; subsequent corrections are recorded below

## Classification correction applied after review

The source runner now has `classify_execution_error`: ambiguous worker failures have current_passed/stronger_passed=null, verification_status=not_run and causality=unverified. Known broker-startup and Lens-delivery errors are infrastructure_error; generic incomplete executions remain execution_error. A low iteration limit alone is never treated as proof that it caused a failure. Completed runs with failed independent verification still have False verdicts

Summary counts now separate deterministic current_failed/stronger_failed from not_scored. The configuration test compares each variant to an exact single-field dataclass replacement

Nine focused tests passed with `<test-environment>/bin/python -m pytest tests/test_lens_regression_lab.py -q`. The system Python had no pytest installed, so that existing test environment was used. The active Docker experiment had its own earlier file copy and was not touched. Its raw report must be preserved; apply the classifier to a separate interpreted copy if it contains the earlier False-on-error fields

## Publisher hardening and runtime fidelity correction

`publish.py` now rejects unsafe/non-lab names before API calls, validates every selected variant before any remote write, forces baseline-first execution, requires a passing finished baseline for a candidate, and verifies the candidate's returned baseline_run_id. A mismatched result is retained as evidence and rejected as a paired comparison, including on resume

Reports use atomic replacement and a local exclusive writer lock. Existing reports must match the eval name, source definition, dataset revision, scorers and gate. Resume retains earlier variants, polls already-scoring runs without reuploading, and reuses the creation idempotency key if acknowledgement was lost. Timeout preserves progress without trying to read unfinished case results. The publisher reads the primary evaluation but only writes the isolated lab definition and runs

Fourteen publisher tests passed, with no live API calls. Tests cover partial evidence, production/path-escape names, baseline ordering and failure, exact baseline pairing, metadata drift, interrupted creation and uploads, scoring resume, retained prior runs and polling timeout

The CI base now runs uv with its own explicit `.venv` and frozen lockfile instead of the candidate interpreter. The eval factory uses actual Settings model/harness selection with only the supplied configuration, preventing unrelated process or dotenv settings from changing an experiment. Explicit eval/deployment harness overrides remain supported. The fixed Codex override was removed from CI

The CI model remains explicitly selected by MOYAI_EVAL_MODEL. This is still a controlled-model lane and does not automatically detect deployment-only model configuration changes. That limit is documented in the workflow and eval wrapper

Fifty-two focused runtime, existing Python integration and publisher tests passed. Workflow YAML parsed with Ruby's YAML library and git diff check passed. actionlint was unavailable. No GitHub workflow or live API was triggered by this review

The original configuration probe now reads the pinned pre-fix ffcc83e snapshot, so its 0/3 historical result remains reproducible after source corrections. Its JSON separately records audited revision and current checkout revision. Existing active-container result records need explicit LENS_VERSION=ffcc83e when publishing; a later local commit must not be substituted for the code that actually executed


## Final independent review (2026-10-09)

The combined configuration, harness, Python eval, runtime selection, regression lab and publisher suite completed with **366 passed, 15 skipped** in 68.50 seconds. Skips are existing optional harness/CLI availability checks. One existing Starlette/httpx deprecation warning appeared. This used the existing Python 3.12 test environment, not a fresh native SDK build.

Added three historical-repository fixture checks and reran the lab file: **12 passed**. The parameterized controls execute the actual imported historical Settings implementation, verify immutable config.py hashes and confirm the broken PR178 parent fails four provider-routing checks (12/16), while its known fixed commit passes all sixteen. The tests also verify the agent workspace does not receive the external grader or known solution, and fixture preparation refuses to overwrite existing work. Both historical revisions are ancestors of this checkout; CI's full Git fetch retains them. Across the combined run plus new checks, 369 unique checks passed.

The workflow now runs all three new test files during its no-model validation step. Reviewed the baseline command: it installs from the base checkout's own frozen uv.lock into the base's own .venv, while HEAD's held-out fixtures and driver are copied deliberately so both sides face the same assertions. No production app or sandbox source is modified by this branch. YAML parsing and git diff --check passed; actionlint is not installed.

The strengthened assertions preserve the original task contracts. The offline evidence still reports the pinned original checks at 8/12 and strengthened checks at 12/12, with all four known-correct controls passing both. These are selected, post-hoc deterministic bad-output probes, not model-derived population recall.

No blocking implementation defect was found in this final review. The independent verifier now fails the Python test even if the remote report says success, after saving output, trace and report. The pinned Lens SDK caches an already finished report, so context-manager cleanup does not overwrite those results. This is a **pytest/CI fail-closed guarantee**, not a new deterministic Lens server scorer: server case badges and the PR summary still follow configured remote scorers. Deployment-only model changes remain outside this explicitly controlled-model lane. The base environment install can be slower, and an incompatible old lockfile fails visibly rather than silently using candidate dependencies.
