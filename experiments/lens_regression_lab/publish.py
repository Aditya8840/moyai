"""Score captured real-agent runs through Lens without changing the production eval.

Uses the same versioned result API as the SDK. The input file is private and may
contain agent output; the public report contains only scores, metadata and links.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen


def publish(args, api, *, version='', clock=time.monotonic, emit=print):
    """Publish only complete isolated variants and preserve resumable evidence."""
    import fcntl
    import re

    if not re.fullmatch(r'moyai-regression-lab-[a-z0-9][a-z0-9_-]*', args.name):
        raise ValueError('Only an isolated regression-lab eval may be written.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.with_suffix(args.output.suffix + '.lock').open('a') as lock:
        # Independent variant publishers must not overwrite each other's report.
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _publish(args, api, version=version, clock=clock, emit=emit)


def _publish(args, api, *, version, clock, emit):
    source = api('GET', '/lens/evals/moyai-python-coding-regressions')
    spec = source['spec']
    cases = api('GET', f"/lens/datasets/{spec['dataset_id']}/revisions/{spec['revision']}/cases")['cases']
    by_input = {case['messages'][0]['content']: case['id'] for case in cases}
    if not cases or len(by_input) != len(cases) or spec.get('trials', 1) != 1:
        raise ValueError('This publisher requires unique saved inputs and one trial per case.')
    records = json.loads(args.records.read_text())
    variants = list(dict.fromkeys(args.variant or [row['variant'] for row in records]))
    if not variants:
        raise ValueError('No variants were selected.')
    # A complete baseline always finishes before any candidate, regardless of
    # CLI or record order. Partial evidence must cause no remote writes.
    variants.sort(key=lambda variant: variant != 'baseline')
    selected = [row for row in records if row['variant'] in variants]
    for variant in variants:
        rows = [row for row in selected if row['variant'] == variant]
        if len(rows) != len(cases) or {row['input'] for row in rows} != set(by_input):
            raise ValueError(f'Each variant must contain exactly {len(cases)} saved cases once.')
        versions = {row.get('metadata', {}).get('source_revision') or version for row in rows}
        if len(versions) != 1 or not next(iter(versions)):
            raise ValueError('Each variant needs one explicit source revision.')
        for row in rows:
            if row['status'] == 'completed':
                if (not isinstance(row.get('answer'), str) or not row['answer'].strip()
                        or not row.get('trace_id') or not isinstance(row.get('verification', {}).get('passed'), bool)):
                    raise ValueError('Completed evidence needs an answer, trace and boolean verification result.')
            elif row['status'] not in {'execution_error', 'infrastructure_error'}:
                raise ValueError('Only completed or explicitly failed executions may be published.')

    identity = {'eval': args.name, 'source_eval': source['name'],
                'dataset_id': spec['dataset_id'], 'revision': spec['revision'],
                'scorers': spec['scorers'], 'gate': spec['gate']}
    report = {**identity,
              'note': 'Execution errors fail the Lens check but are not automatically evidence of model-quality degradation.',
              'runs': []}
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if any(previous.get(key) != value for key, value in identity.items()):
            raise ValueError('Existing report belongs to another eval or scorer/dataset configuration.')
        report['runs'] = previous['runs']

    def passing_baseline():
        baselines = [item for item in report['runs'] if item['variant'] == 'baseline']
        if not baselines:
            return None
        run = baselines[-1]['run']
        return run if run['status'] == 'done' and run.get('summary', {}).get('gate', {}).get('passed') is True else None

    if 'baseline' not in variants and passing_baseline() is None:
        raise ValueError('Publish and finish a passing baseline before publishing a candidate.')

    def save():
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(args.output)

    if not args.output.exists():
        api('PUT', '/lens/evals/' + args.name, spec)
        save()

    baseline_for_candidates = passing_baseline()
    for variant in variants:
        baseline = baseline_for_candidates if variant != 'baseline' else None
        if variant != 'baseline' and baseline is None:
            raise ValueError('The baseline did not pass; the candidate cannot be a controlled comparison.')
        rows = [row for row in selected if row['variant'] == variant]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        existing = next((run for run in report['runs'] if run['evidence_sha256'] == digest), None)
        if existing and existing['run']['status'] in ('done', 'failed') and 'cases' in existing:
            if variant == 'baseline':
                run = existing['run']
                baseline_for_candidates = (run if run['status'] == 'done'
                                           and run.get('summary', {}).get('gate', {}).get('passed') is True else None)
            elif (existing['run']['status'] == 'done'
                  and existing['run'].get('summary', {}).get('baseline_run_id') != baseline['id']):
                raise ValueError('Saved candidate used a different baseline; do not interpret it as a paired result.')
            continue
        request = {key: spec[key] for key in ('agent', 'dataset_id', 'revision', 'scorers', 'gate', 'timeout_per_trial_ms')}
        request.update(eval=args.name, version=rows[0].get('metadata', {}).get('source_revision') or version,
                       branch='main' if variant == 'baseline' else 'lab-' + variant,
                       trials=1, ci_url='')
        run = (api('GET', f"/lens/evals/runs/{existing['run']['id']}") if existing else
               api('POST', '/lens/evals/runs', request, {'Idempotency-Key': args.name + '-' + digest}))
        record = existing or {'variant': variant, 'evidence_sha256': digest, 'run': run}
        record['run'] = run
        if not existing:
            report['runs'].append(record)
        if baseline is not None:
            record['expected_baseline_run_id'] = baseline['id']
        save()
        if run['status'] == 'running':
            for row in rows:
                if row['status'] == 'completed':
                    result = {'trace': {'attribute': 'trace_id', 'value': row['trace_id']},
                              'output': json.dumps({'answer': row['answer'], 'verification': row['verification']})}
                else:
                    result = {'error': {'type': 'LabExecutionError',
                                        'message': row.get('error', row.get('error_type', 'Execution incomplete'))}}
                api('PUT', f"/lens/evals/runs/{run['id']}/results/{by_input[row['input']]}/0", result)
            run = api('POST', f"/lens/evals/runs/{run['id']}/finish")
            record['run'] = run
            save()
        deadline = clock() + 600
        while run['status'] not in ('done', 'failed') and clock() < deadline:
            run = api('GET', f"/lens/evals/runs/{run['id']}?wait=30")
            record['run'] = run
            save()
        if run['status'] not in ('done', 'failed'):
            raise TimeoutError('Lens scoring remains in progress; reuse this report to resume.')
        record['cases'] = api('GET', f"/lens/evals/runs/{run['id']}/cases")
        if variant == 'baseline':
            baseline_for_candidates = (run if run['status'] == 'done'
                                       and run.get('summary', {}).get('gate', {}).get('passed') is True else None)
        if baseline is not None and run['status'] == 'done':
            actual_baseline = run.get('summary', {}).get('baseline_run_id')
            record['baseline_pairing'] = {'expected': baseline['id'], 'actual': actual_baseline,
                                          'matches': actual_baseline == baseline['id']}
            save()
            if actual_baseline != baseline['id']:
                raise ValueError('Lens selected a different baseline; do not interpret this as a paired regression result.')
        save()
        emit(json.dumps({'variant': variant, 'status': run['status'], 'summary': run.get('summary'), 'url': run['url']}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', default='moyai-regression-lab-20261009')
    parser.add_argument('--variant', action='append')
    args = parser.parse_args()
    base = os.environ['LENS_BASE_URL'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + os.environ['LENS_API_KEY'],
               'X-Lens-Contract': '2', 'Content-Type': 'application/json'}

    def api(method, path, body=None, extra=None):
        request = Request(base + path, method=method,
                          headers={**headers, **(extra or {})},
                          data=json.dumps(body).encode() if body is not None else None)
        with urlopen(request, timeout=45) as response:
            content = response.read()
        return json.loads(content) if content else None

    publish(args, api, version=os.environ.get('LENS_VERSION', ''))


if __name__ == '__main__':
    main()
