"""Run the real Moyai agent in a disposable container with controlled variants.

Requires the existing evals.agent environment, including real model and trace
credentials. Never writes a saved Lens dataset, eval definition, or CI gate.
Trace export is the normal agent path; the JSON report contains no credentials
or arbitrary agent output. This runner reports local verifier outcomes, not a
Lens scorer verdict. Use publish.py to score captured results in a separate
experimental eval through the versioned Lens result API.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import tempfile
import time

from evals.agent import AgentRunError, MoyaiAgent
from evals.test_moyai import verify_solution
from .offline import ORIGINAL_FIXTURE, ORIGINAL_REVISION, ROOT, original_cases


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def configure_variant(agent, variant, *, alternate_model=None, alternate_harness=None):
    if variant == 'baseline':
        return agent
    if variant == 'max-turns-1':
        return replace(agent, max_iterations=1)
    if variant == 'alternate-model' and alternate_model:
        return replace(agent, model=alternate_model)
    if variant == 'alternate-harness' and alternate_harness:
        return replace(agent, harness=alternate_harness)
    raise ValueError('Unknown variant or missing alternate model/harness.')


def classify_execution_error(error):
    """Worker errors are missing measurements, not failed correctness checks.

    A configured low iteration limit is only the intended intervention. The
    worker's generic incomplete result cannot prove that limit caused the stop.
    Lens can still fail an evaluation gate for these errors without that being
    evidence of an agent-quality regression.
    """
    message = str(error)
    causes = {
        'The isolated Moyai broker did not become ready.': ('infrastructure_error', 'broker_startup'),
        'Lens did not acknowledge all agent, model and tool spans before the deadline.':
            ('infrastructure_error', 'trace_delivery'),
        'Moyai did not produce all required agent, model and tool spans.':
            ('execution_error', 'missing_trace_evidence'),
        'Moyai stopped before completing the task or settling all tool calls.':
            ('execution_error', 'incomplete_execution'),
        'Moyai exceeded its execution and trace-delivery deadline.':
            ('execution_error', 'combined_deadline'),
        'Moyai source changed after evaluation started.': ('infrastructure_error', 'source_changed'),
    }
    status, cause = causes.get(message, ('execution_error', 'unknown_worker_failure'))
    return {'status': status, 'error': message, 'failure_kind': cause,
            'verification_status': 'not_run', 'causality': 'unverified',
            'current_passed': None, 'stronger_passed': None}


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(path)


def write_private_records(path, records):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(records, output, indent=2)
            output.write('\n')
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_experiment(args):
    if os.environ.get('MOYAI_REGRESSION_LAB_ISOLATED') != '1':
        raise ValueError('Run in a disposable container and set MOYAI_REGRESSION_LAB_ISOLATED=1.')
    fixture = ORIGINAL_FIXTURE
    cases = original_cases()
    strengthened_fixture = ROOT / 'evals' / 'coding_cases.json'
    strengthened = {case['meta']['name']: case for case in json.loads(strengthened_fixture.read_text())['cases']}
    if args.case:
        cases = [case for case in cases if case['meta']['name'] in args.case]
        if len(cases) != len(set(args.case)):
            raise ValueError('Unknown coding case.')
    if args.repeats < 1:
        raise ValueError('Repeats must be positive.')
    variants = args.variants.split(',')
    report = {'experiment': 'real-agent-controlled-variants', 'status': 'running',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
              'original_fixture_revision': ORIGINAL_REVISION,
              'stronger_fixture_sha256': hashlib.sha256(strengthened_fixture.read_bytes()).hexdigest(),
              'verifier_labels': {'current': 'Original smoke checks at ffcc83e',
                                  'stronger': 'Checked-out strengthened production checks'},
              'python': platform.python_version(),
              'runtime': {name: package_version(name) for name in
                          ('openai-codex', 'claude-agent-sdk', 'openai', 'lens-evals')},
              'scope': 'Existing four fresh-workspace coding tasks. Local independent verifier outcomes, not Lens scorer verdicts or broad agent capability estimates.',
              'runs': []}
    private_records = []
    write_report(args.output, report)
    with tempfile.TemporaryDirectory(prefix='moyai-real-regression-lab-') as directory:
        for repeat in range(args.repeats):
            for variant in variants:
                for case in cases:
                    name = case['meta']['name']
                    workspace = Path(directory) / f'{repeat}-{variant}-{name}'
                    agent = configure_variant(MoyaiAgent.from_env(workspace=workspace), variant,
                                              alternate_model=args.alternate_model,
                                              alternate_harness=args.alternate_harness)
                    report['source_revision'] = agent.version
                    row = {'case': name, 'variant': variant, 'repeat': repeat + 1,
                           'source_revision': agent.version,
                           'model': agent.model, 'harness': agent.harness,
                           'max_iterations': agent.max_iterations, 'timeout': agent.timeout,
                           'started_at': datetime.now(timezone.utc).isoformat()}
                    started = time.monotonic()
                    private = {'case': name, 'variant': variant, 'repeat': repeat + 1,
                               'input': case['input'], 'expected': case['expected']}
                    try:
                        result = agent.run(input=case['input'])
                    except AgentRunError as exc:
                        observation = classify_execution_error(exc)
                        row.update(observation)
                        private.update(observation)
                    except Exception as exc:
                        # Arbitrary exception messages can contain provider bodies.
                        row.update(status='infrastructure_error', error_type=type(exc).__name__,
                                   verification_status='not_run', causality='unverified',
                                   current_passed=None, stronger_passed=None)
                        private.update(status='infrastructure_error', error_type=type(exc).__name__)
                    else:
                        code = case['meta']['verification_code']
                        current = verify_solution(workspace, code)
                        stronger = verify_solution(workspace, strengthened[name]['meta']['verification_code'])
                        solution = workspace / 'solution.py'
                        row.update(status='completed', current_passed=current['passed'],
                                   stronger_passed=stronger['passed'], verification_status='scored',
                                   trace_id=result.trace_id,
                                   session_id=result.session_id, model_calls=result.model_calls,
                                   tool_calls=result.tool_calls, output_chars=len(result.output),
                                   output_sha256=hashlib.sha256(result.output.encode()).hexdigest(),
                                   solution_sha256=hashlib.sha256(solution.read_bytes()).hexdigest()
                                   if solution.is_file() else None)
                        private.update(status='completed', answer=result.output,
                                       verification=current, stronger_verification=stronger,
                                       trace_id=result.trace_id, session_id=result.session_id)
                    solution = workspace / 'solution.py'
                    if solution.is_file():
                        with solution.open('r', errors='replace') as source_file:
                            contents = source_file.read(1_000_001)
                        private['solution'] = contents[:1_000_000]
                        private['solution_truncated'] = len(contents) > 1_000_000
                    row['duration_seconds'] = round(time.monotonic() - started, 3)
                    private['metadata'] = dict(row)
                    private_records.append(private)
                    write_private_records(args.lens_records, private_records)
                    report['runs'].append(row)
                    write_report(args.output, report)
                    print(json.dumps({'case': name, 'variant': variant, 'status': row['status'],
                                      'current_passed': row['current_passed'],
                                      'stronger_passed': row['stronger_passed'],
                                      'duration_seconds': row['duration_seconds']}), flush=True)
    report['status'] = 'completed'
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    report['summary'] = {
        variant: {'trials': len(rows := [row for row in report['runs'] if row['variant'] == variant]),
                  'completed': sum(row['status'] == 'completed' for row in rows),
                  'execution_errors': sum(row['status'] == 'execution_error' for row in rows),
                  'infrastructure_errors': sum(row['status'] == 'infrastructure_error' for row in rows),
                  'current_passed': sum(row['current_passed'] is True for row in rows),
                  'current_failed': sum(row['current_passed'] is False for row in rows),
                  'stronger_passed': sum(row['stronger_passed'] is True for row in rows),
                  'stronger_failed': sum(row['stronger_passed'] is False for row in rows),
                  'not_scored': sum(row['current_passed'] is None for row in rows)}
        for variant in variants}
    write_report(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lens-records', type=Path,
                        help='Optional mode-0600 actual outputs for isolated Lens scoring; never publish this file.')
    parser.add_argument('--variants', default='baseline,max-turns-1')
    parser.add_argument('--alternate-model')
    parser.add_argument('--alternate-harness', choices=('codex', 'claude-agent-sdk'))
    parser.add_argument('--case', action='append')
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    run_experiment(args)


if __name__ == '__main__':
    main()
