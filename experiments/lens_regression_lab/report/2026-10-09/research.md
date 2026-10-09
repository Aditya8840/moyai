# Making Lens useful for Moyai regressions

Evidence captured 2026-10-09 from Moyai commit `ffcc83e054d45242a8a1a6465d1f775761ad6452` in `<moyai-checkout>`

## What the current Lens suite proves, and what it does not

The current suite exercises real model inference, real native file/terminal tools, independent Python verification, and acknowledged trace export. It is more than a mocked smoke test. However, its four tasks are isolated single-file utility functions: stable deduplication, interval merging, iterable chunking, and boolean parsing. They do not sample Moyai's long-running, repository-wide, multi-turn engineering work

The most consequential blind spot is configuration fidelity. `.github/workflows/lens-evals.yml` sets `MOYAI_EVAL_HARNESS: codex` and one `AGENT_MODEL` repository variable for both base and candidate. `evals/agent.py` also defaults to Codex. Thus changes to production's automatic model/harness selection are bypassed. The workflow compares the checkout source, but its fixed runtime overrides are not the production defaults it claims to protect

The eval worker calls the real harness with a short, special-purpose system message. Production builds a much larger system message in `sandbox/agent.py`, includes saved history and goal handling, and calls `run_goal_conversation`. The eval worker uses `conversation_history=[]`, rejects follow-up cases, disables memory review, and starts with an empty workspace. A production prompt regression or a failure spanning correction, compaction, restart, or subagent handoff can remain invisible even if all four functions pass

The workflow installs the head checkout's environment once, then runs both checkouts with the same `.venv`. It copies the head eval driver into the base worktree. Sharing the driver is useful to hold grading constant; sharing the candidate SDK/dependency environment means an SDK-version downgrade or upgrade is not independently compared against the base dependency lock. This must be displayed as a limitation until each candidate carries its own runtime image

Sources: [workflow](https://github.com/BerriAI/moyai/blob/ffcc83e054d45242a8a1a6465d1f775761ad6452/.github/workflows/lens-evals.yml), [case fixture](https://github.com/BerriAI/moyai/blob/ffcc83e054d45242a8a1a6465d1f775761ad6452/evals/coding_cases.json), [worker](https://github.com/BerriAI/moyai/blob/ffcc83e054d45242a8a1a6465d1f775761ad6452/evals/agent_worker.py), [production agent entry](https://github.com/BerriAI/moyai/blob/ffcc83e054d45242a8a1a6465d1f775761ad6452/sandbox/agent.py)

## Historical failures that can become regression cases

These are repository and PR records, not a claim that a particular SDK or model caused the user's entire reported quality decline. Live before/after quality attribution requires the separate experiments

| Evidence | What changed or broke | Candidate reusable eval |
| --- | --- | --- |
| [PR 113](https://github.com/BerriAI/moyai/pull/113), `5da2eb7` | New sessions changed from Hermes to native Claude Agent SDK, while saved sessions retained their runtime | Run the same repository task through resolved old/new defaults, holding prompt and tools constant; verify fresh and resumed sessions |
| [PR 163](https://github.com/BerriAI/moyai/pull/163), `4387138` | Codex changed from the LiteLLM harness binding to native `openai-codex==0.161.0`; automatic model pairings were introduced | Test actual runtime selection plus complete agent behavior, with each revision's own dependency lock |
| [PR 178](https://github.com/BerriAI/moyai/pull/178), `5fd2114` | Exact model-ID matching made Sol fall back to Claude Agent SDK. Provider-prefix selection fixed it | Resolve user aliases through `Settings.default_harness`, then assert the effective harness and execute a task. A forced Codex CI run cannot expose this path |
| [PR 183](https://github.com/BerriAI/moyai/pull/183), `a2f4f8d` | A yielded nested tool blocked the next model poll with HTTP 409 because the adapter waited for completion too early | Start a tool that can finish only after an admitted poll; require exactly one execution, one saved receipt, and a completed answer |
| [PR 185](https://github.com/BerriAI/moyai/pull/185), `6f05bdc` | Claude's default 1 MiB JSON message buffer rejected a large native image Read; a bounded 16 MiB buffer fixed it | Deliver a large image through the real native Read, verify the image reaches the model and that the receipt settles |
| [PR 232](https://github.com/BerriAI/moyai/pull/232), `f69eec2` | Models omitting assistant `phase` caused an answer to be sent as progress and repeated in final output | Replay missing-phase output and require one progress update and one final answer, with no repeated final content |
| [PR 235](https://github.com/BerriAI/moyai/pull/235), `d8d1a36` | Commands needing more than the old receipt grace period were lost after context rejection | Keep the same native command alive through compaction; require one execution, one completion receipt, zero unresolved work |

The PR 183 and 235 proof scripts run actual SDK/MCP/HTTP/SQLite plumbing with scripted loopback model replies. They measure runtime contracts reproducibly; they do not measure a model's reasoning quality. PR 183 additionally records a live replay, but that is the PR author's historical report and is not a fresh result of this investigation

## A cold start does not need production traces

Proposed flow: **connect repository → confirm the real agent entry point → run a verified starter suite → compare a change**

1. Inspect the agent entry point, selected model and SDK, tool contracts, tests, dependency lock, and recent regression-fix PRs. Produce a small visible coverage map: repository editing, test execution, tool recovery, follow-up corrections, long context, and runtime selection
2. Import existing regression tests as a fast contract lane. Preserve the real SDK and tool path where the existing test does so. Clearly tag scripted inference separately from live model inference
3. Mine accepted bug-fix PRs into repository tasks: checkout the parent, state the user-visible problem without the patch, let Moyai edit that checkout, and grade with held-out fail-to-pass checks plus pass-to-pass checks. Verify the historical parent fails and the accepted fix passes before publishing a task. Reject ambiguous, flaky, credential-dependent, or environment-dependent candidates
4. Generate a few missing scenarios from actual tool contracts and requirements, not arbitrary trivia. A person reviews the task and outcome checks. A generated task is only a candidate until a reference solution passes, a no-op fails, and a relevant broken version fails
5. Run the current agent repeatedly to establish stability. Keep hard but not yet reliable tasks in a capability lane; only stable successful tasks become regression protection
6. When traces become available, sample failures, explicit user corrections, model/SDK changes, long sessions, and unusual tool sequences. Dedupe by behavior. Recover a sanitized starting repository and environment; a trace alone is not a replayable task. Promote a trace-derived case only once outcome checks and reproduction are verified

For Moyai, the starter suite should combine a handful of repository tasks with the already available historical transport cases. Four function-writing prompts remain a cheap smoke lane. They should not be presented as broad agent quality coverage

## Proving the evaluator catches regressions

Use a mutation challenge before trusting a green check. Maintain isolated bad candidates with one purposeful defect each: lose a follow-up correction, choose the wrong SDK, drop a tool result, truncate context, stop before verification, or change the tested model. A mutation is detected only if the unchanged grader distinguishes it from a stable baseline on the relevant outcome. A startup/import failure is an invalid quality comparison, not evidence that a quality regression was caught

Track three separate numbers:

- **Outcome regressions:** cases that were reliably successful and became unsuccessful
- **Mutation detection:** valid deliberately degraded candidates caught, with the missed ones visible
- **Coverage and uncertainty:** behaviors covered, actual independent cases, repeats, unresolved environment errors, and interval estimates

Do not replace these with a cosmetic score out of five. Four easy passes can be entirely correct yet provide little evidence about a model or SDK migration

## Runtime and comparison contract

Each result should record the source SHA, SDK versions and lock hash, resolved model ID, effective harness, prompt hash, tool catalog/version, workspace fixture revision, dataset revision, grader revision, resource limits, duration, cost, and trace IDs. Run base and candidate in separate disposable images built from their respective locks; apply the same held-out grading bundle to both

Use two complementary comparisons: (a) actual deployment defaults, to catch product/configuration drift; (b) a controlled one-factor comparison, to explain whether the model, SDK, prompt, or code caused the change. Neither substitutes for the other

Pair base/candidate runs by task and environment, interleave their order, and repeat a predeclared number of trials. Show case-level win/loss/tie counts and paired success deltas. Repeats reduce uncertainty about a task's stochastic success rate; they are not new coverage. Keep the case as the unit when estimating uncertainty across a curated suite. Start with three repeats for triage, expand ambiguous/high-impact cases, and avoid a claim of statistical certainty from that starting budget

Separate agent failures from environment errors. Broken credentials, unavailable images, out-of-memory termination, or model endpoint errors need a visible **not evaluated** state plus diagnostics, rather than being silently counted as either success or model-quality failure. A deliberate resource-limit degradation is a different, explicitly labeled performance experiment

## Research supporting the design

- Anthropic recommends evaluating both final environment outcomes and trajectories, and distinguishes regression suites from capability suites. It emphasizes repeated trials, realistic failure-derived cases, clear graders, and combining deterministic, model, and human checks. Application here: grade the repository and saved tool outcomes, keep uncertain new tasks separate, and use traces to diagnose failures. [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- Anthropic measured a six-percentage-point Terminal-Bench swing between infrastructure configurations. CPU/RAM enforcement and timeouts can change apparent model scores. Application here: pin resource budgets and label infrastructure errors; do not attribute a container failure to a model downgrade. [Quantifying infrastructure noise](https://www.anthropic.com/engineering/infrastructure-noise)
- SWE-bench's evaluation harness applies generated patches to repositories and executes tests in containers. Its reporting distinguishes resolved, unresolved, incomplete, and infrastructure-affected instances. Application here: PR-derived tasks with verified initial states and independent outcome checks are a credible cold-start source; public benchmark scores are not evidence of Moyai's own regression coverage. [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/)
- Stryker defines detected, survived, uncovered, invalid, and pending mutations separately. Application here is an adaptation for agent behavior: show undetected degradations and exclude invalid experiments from a claimed detection rate. This is a proposed methodology, not a published guarantee about agent-eval sensitivity. [Mutation states and metrics](https://stryker-mutator.io/docs/mutation-testing-elements/mutant-states-and-metrics/)
- Peyrard et al. show that independently averaging scores can hide information from evaluating systems on the same instances. Their study concerns NLP evaluation, not this agent system. Application here: preserve task pairing and inspect per-case changes; the concrete repeat counts and gating policy above are proposed engineering choices. [Better than Average: Paired Evaluation of NLP Systems](https://arxiv.org/abs/2110.10746)

## Fresh local results

All three native regression controls below reproduced the degraded behavior and distinguished current behavior. A fourth configuration check reproduced the wrong SDK default. These are four selected, historical regression controls, not an estimate of general regression-detection probability. They have not been uploaded through Lens's eval API and do not prove that the existing Lens dataset already catches them

| Control | Degraded behavior measured | Current behavior measured | Scope |
| --- | --- | --- | --- |
| Yielded nested tool, adapter from `1500076` | HTTP 409; incomplete; one execution, zero saved receipts, one pending tool | Completed; three model requests; one execution, one saved receipt, zero pending tools | Actual Codex SDK/MCP/relay, scripted provider |
| Running command during compaction, adapter from `e70f696` | Incomplete; one started command, zero receipts, one pending tool | Completed; one native client, one compaction, one execution, one receipt, zero pending tools | Actual Codex SDK/terminal/relay, scripted provider |
| Large image at SDK default 1 MiB | `CLIJSONDecodeError`; task incomplete despite image reaching provider fixture | Completed with bounded 16 MiB configuration; 1,216,435-byte transcript frame accepted; one receipt, zero pending tools | Actual Claude SDK/native Read, scripted provider |
| Sol automatic SDK selection, actual pre-178 method | `openai/gpt-6.1-sol` resolves to `claude-agent-sdk` without explicit override | Same model resolves to `codex` | Actual historical routing method in current Settings context; no inference |

The three native controls exercised installed `openai-codex==0.161.0` / `claude-agent-sdk==0.2.163` and real local tool execution. Only inference was scripted. The lab wrapper supplied a report-only `transport_attempt=0` field to historical Codex classes so the current result serializer could read them; their execution methods were unchanged. No production source or credentials were changed

Evidence: [aggregate results](historical/results.json), [yield proof](historical/yield/codex-yield-proof.json), [compaction proof](historical/compaction/codex-yield-proof.json), [image proof](historical/claude-buffer.json), [routing proof](historical/model-routing.json), [reproducer](historical/run_probes.py)

Initial attempts failed because the host disk ran out of space. A compaction attempt also lost the current native process before any provider request during that storage incident. Those are excluded infrastructure attempts, preserved separately. After root cleaned its prior build cache and available disk exceeded 24 GiB, the same compaction command completed the valid before/after comparison
