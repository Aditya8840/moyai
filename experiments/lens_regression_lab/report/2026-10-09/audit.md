# Moyai regression audit

Audited Moyai `ffcc83e054d45242a8a1a6465d1f775761ad6452` in `<moyai-checkout>` and Lens `e3d9d749d3ed604c092ce804a871a735b9a40931` in `<lens-checkout>`. No production changes or secret reads

The current named suite genuinely runs a native agent, model calls, tools, independent file verification and trace delivery. It is a four-case coding smoke test, with important limits on its ability to catch changes to actual production behavior

## Measured configuration blind spot

`experiments/lens_regression_lab/config_probe.py` executed the actual Settings methods extracted from the source and the real `MoyaiAgent.from_env` method. The parent verified that `openai/gpt-6.1-sol` is the current GitHub eval model variable. The probe reads the fixed Codex harness directly from the workflow

| Experiment | Production selection using actual code | Effective eval | Change reflected |
| --- | --- | --- | --- |
| Baseline | GPT-6.1 Sol / Codex | GPT-6.1 Sol / Codex | Matches |
| Model family and SDK change | Claude Opus 5.5 / Claude Agent SDK | GPT-6.1 Sol / Codex | No |
| Model change within OpenAI | GPT-6 Astra / Codex | GPT-6.1 Sol / Codex | No |
| Explicit harness change | GPT-6.1 Sol / Tool Loop | GPT-6.1 Sol / Codex | No |

**0 of 3 production configuration mutations were reflected in the eval configuration.** Evidence: `config-probe.json`. This is a measured configuration coverage result, not a model-quality result. It does not claim any listed alternative is worse

The probe also includes an explicitly labeled stand-in import-path demonstration. When both base and head use the candidate installed dependency path, both import the candidate artifact. These are stand-in modules, not vendor SDK executions

## Findings with code evidence

### 1. Separate eval model and SDK configuration can bypass production changes

Moyai `.github/workflows/lens-evals.yml:28-31` selects a separate model variable and fixes `MOYAI_EVAL_HARNESS: codex`. `evals/agent.py:61-77` takes this eval-only configuration. `evals/agent_worker.py:92-104` passes it directly to `create_agent`

Production selection is in `app/config.py:367-404`: OpenAI defaults to Codex, Anthropic to Claude Agent SDK, and an explicit deployment harness overrides the mapping. It would be inaccurate to say all production sessions use the raw Claude default. The problem is that the eval bypasses the production choice and uses a separately configured path

Minimum correction: a checked-in runtime configuration consumed by production and eval. Record resolved model, harness, SDK version, prompt hash and lockfile hash on every Lens run. For SDK/model changes, compare the candidate runtime configuration against the prior configuration. Keep a fixed-model component lane but label its narrower scope

### 2. Base and candidate share candidate SDK dependencies

Workflow lines 60-61 install dependencies once after checking out HEAD. Lines 72-73 make the base checkout and copy the HEAD eval driver and fixtures there. Lines 88-89 run base using `$GITHUB_WORKSPACE/.venv/bin/python`, HEAD's environment. Line 102 runs the candidate with that environment too

`pyproject.toml:32-34` pins native SDK versions; `uv.lock` pins their graph. Both sides can therefore run an upgraded or downgraded SDK even though their source SHAs differ

Minimum correction: install each side from its own frozen lockfile and capture actual imported package versions. Freeze the task definition and verifier for comparability without overwriting the runtime under test

### 3. Production context, prompt and repository workflows are absent

`evals/coding_cases.json:4-36` contains four small single-file algorithms in fresh workspaces. `evals/test_moyai.py:60-61` rejects followups. `evals/agent_worker.py:90-104` initializes empty context and substitutes a short coding prompt

The production prompt at `sandbox/agent.py:271-415` includes session search, model switching, persistent context, tool discovery, skills, delegation, repository checkout, publication and truthfulness. Production calls `run_goal_conversation` at line 433; the smoke suite does not. The latest audited commit adds Codex runtime reuse across messages, but a fresh single-turn agent cannot establish correct second-turn behavior

Minimum correction: seed a local fixture repo, ask for a bug fix, verify its final files and hidden tests, then issue a correction requiring saved context. Use the normal production entry point. Add controlled lost-context, skipped-tool and corrupted-tool-result mutations

### 4. Deterministic verification depends on the configured judge

`evals/test_moyai.py:69-71` serializes the independent verifier result into output JSON; it does not directly mark a failed verifier as a failed trial. Lens `src/worker/crates/evals/src/scorer/task_completed.rs:17-18` only checks that the root span exists and is not an error. Completion is not correctness

The parent verified the live primary suite is stricter than the defaults: revision 1, four cases, one trial, task_completed plus a moyai-judge rubric explicitly requiring verification.passed=true and supporting tool/final evidence. Its gate requires 100% case pass rate and 100% on each scorer. This audit does NOT claim current primary cases can pass failed verification; that needs an actual judge negative-control experiment

A separate live definition m2224 has only task_completed, three trials, and no absolute pass-rate minimum. That definition cannot establish output correctness from a successful root alone

Minimum correction: a typed deterministic check result from the trusted runner, distinct from model output, must fail a case without an LLM interpreting its boolean. Keep a judge for subjective behavior and final-answer honesty, calibrated against known good and bad outputs

### 5. Baseline is latest compatible main run, not the paired before run

Lens `src/worker/crates/storage-clickhouse/src/evals/records.rs:123-150` keys baselines by eval, agent, dataset revision, scorers and optional agent I/O. `src/worker/crates/storage-clickhouse/src/evals.rs:320-356` selects the newest completed compatible main run with matching trials/cases. It does not require the same GitHub workflow or requested BASE_SHA

Moyai workflow line 84 labels each PR's before run main; concurrency at line 11 is per PR. Concurrent PRs can publish different bases into the same pool. Lens `src/worker/crates/storage-clickhouse/src/evals/scoring.rs:174-183` indexes completed main runs regardless of gate pass status

This is a code-supported risk, not an observed production race. Minimum correction: candidate names its explicit baseline_run_id from before.json, Lens verifies that baseline source matches BASE_SHA, and reports show this pairing. Keep a separate accepted baseline for deployment-configuration changes

### 6. New trace-derived cases cannot simply join this suite

`evals/test_moyai.py:11-16` rejects any input not exactly matching one of the four versioned prompts, and followups are separately rejected. This correctly avoids executing server-provided verification code, but means Add to dataset alone cannot expand this suite

Minimum correction: produce a reviewed replay recipe with repo snapshot or minimal fixture, user input and required history, safe tool substitutes, expected observable outcome, and a trusted verifier identifier. Generate a draft case plus a code change, then prove an injected regression fails. For users with no traces, draft cases from README, tests, tool contracts and explicit critical workflows, run them locally, and save the resulting first traces

## Gate and confidence interpretation

Default Lens Gate has regression limits but no pass-rate minimum (`contract/src/eval.rs:87-96`). Regression checks require a baseline (`evals/src/gate.rs:16-59`); Python Report.assert_passed only checks gate.passed (`src/sdk/src/lens/models.py:155-157`). Thus a no-baseline default run can pass its gate with failed cases. The live primary suite overrides this with a strict absolute floor, so this is an onboarding/default-product risk rather than its current configuration

Four passing cases do not establish broad reliability. One trial per case does not measure stochastic failures. Repeats are not new task coverage. Report case pass rate, repeatability, reviewed behavioral coverage, mutation detection rate and false alarms on unchanged controls separately

## Minimal experiment sequence

| Experiment | Evidence required | Limit |
| --- | --- | --- |
| Config parity probe, completed | Actual selection functions and eval config; currently 0/3 changes reflected | Does not measure quality |
| Real current baseline | All existing cases execute and score in real Lens | Only smoke coverage |
| Deterministic negative controls | Wrong final files fail the live configured scorer | Not natural model regressions |
| SDK/model before-after pair | Separate frozen environments and selected configs, same tasks | Not all workflows |
| Repo and multi-turn mutants | Lost context, skipped tools, untested patches and corrupted results detected | Bounded fault pack |
| Unchanged repeat controls | Stable code does not create excessive false alarms | No promise of zero future flakiness |
| Production-derived replay | Sanitized observed failure reproducible; corrected agent passes | Needs environment, not just trace text |

Keep CI advisory until representative mutations are caught and repeated unchanged runs establish an acceptable false-alarm rate. Reproduce the configuration result with `python3 experiments/lens_regression_lab/config_probe.py --output /tmp/config-probe.json`. The probe makes no network calls and executes no agent
