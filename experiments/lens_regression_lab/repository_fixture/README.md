# Historical repository task

This cold-start task comes from the actual fix in [Moyai PR 178](https://github.com/BerriAI/moyai/pull/178). It needs no production traces

`repository_case.prepare` copies fourteen tracked source, test, and documentation files from the fix's parent commit into a fresh workspace. The agent gets the bug description and original repository slice. It does not receive the known fix or the external verification script

The grader imports the actual `Settings` class from the resulting workspace in a separate process. Sixteen checks cover model aliases, future catalog entries, exact provider namespace matching, unknown model rejection, and explicit harness settings from constructor, environment, and dotenv sources

The unmodified broken revision passes 12 of 16 checks and fails the task. The unmodified merged fix passes all 16 checks. These control results validate the fixture; only a subsequent real model run measures whether Moyai can solve it

```sh
python -m experiments.lens_regression_lab.repository_case controls \
  --output /tmp/repository-controls.json
```

The real execution helper uses `evals.agent_worker.execute` with the current checked-out Moyai harness. The historical files are task input, not a substitution of the tested agent's source. The ordinary worker configuration arrives on stdin so credentials are absent from the command line. Its `workspace` must be empty, and its `state` must be a fresh location. Run it only in a disposable container and impose an outer process deadline

```sh
python -m experiments.lens_regression_lab.repository_case run \
  --output /tmp/repository-result.json \
  --lens-records /tmp/repository-private.json
```

The report distinguishes an incomplete execution from a completed patch that fails verification. Generated answers and changed files remain in the optional mode-0600 private record; the public report contains provenance, filenames, check outcomes, runtime identity, and trace references
# Pinned source snapshots

`broken.json` and `fixed.json` contain the exact UTF-8 contents of the fourteen
files listed in `repository_case.FILES`, copied from the full historical commit
IDs in that module. The loader checks each bundle's SHA-256, revision and file
inventory before writing a task workspace. CI does not need Git history or
network access to prepare or grade these tasks.

Regenerate only deliberately, using `git show <revision>:<path>` from Moyai's full
history, JSON keys in the order `revision`, `files`, file order from `FILES`,
`json.dumps(..., indent=2)` and a trailing newline. Update the bundle hash only
after comparing every file against the stated source commit.
