"""A cold-start coding task from a real Moyai bug, with held-out checks.

prepare and controls are offline. run reads the normal agent_worker payload on
stdin and uses the real broker and SDK; execute run only in a disposable container.
The historical task files and the current harness source are separate checkouts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

BROKEN_REVISION = '61818fa91f3cd37db38b0d3d25ce4a8f6a1fce82'
FIXED_REVISION = '5fd2114a74fd48cd6be7fa27175cb9c45a459946'
SNAPSHOTS = {
    BROKEN_REVISION: ('broken.json', '05e249114c4dd7d30b278dc5e450fa336614a0fd027bca53d6823d5e31d39518'),
    FIXED_REVISION: ('fixed.json', '5213e0db5f54779758b2943f3f74de0fc1379b69361ff9bb983926800bfe6f99'),
}
FILES = (
    'app/__init__.py', 'app/config.py', 'app/context_budget.py', 'app/harnesses.py',
    'sandbox/harness_registry.py', 'sandbox/harness_agent.py',
    'sandbox/history_reference.py', 'sandbox/memory_history.py',
    'tests/test_config.py', 'README.md', 'docs/harnesses.md',
    'docs/getting-started.md', 'pyproject.toml', '.env.example',
)
PROMPT = (
    'Fix model-to-SDK routing in this Moyai repository slice. Some newly selected OpenAI models '
    'start on the wrong agent SDK. New sessions should choose Codex for an enabled model in the '
    'openai provider namespace and Claude Agent SDK for an enabled anthropic model, including '
    'new versions without adding a model-specific allowlist. Resolve user aliases and catalog '
    'display names before selecting. Preserve explicit deployment harness choices from constructor, '
    'environment, or dotenv settings. Other providers and unprefixed gateway aliases should keep '
    'the existing fallback, and unconfigured models must still be rejected. '
    'Inspect the existing configuration, harness registry, tests, and docs. Update the relevant '
    'implementation, regression tests, and documentation, then run the targeted tests. '
    'This is a source slice of the real repository; the available dependencies are already '
    'installed in the runtime Python environment. Do not download or look up an upstream solution. '
    'Explain what changed and what you actually verified.'
)


def prepare(workspace, revision=BROKEN_REVISION):
    workspace = Path(workspace)
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError('The historical task workspace must be empty.')
    if revision not in SNAPSHOTS:
        raise ValueError('Only the pinned broken and fixed historical snapshots are supported.')
    filename, expected_hash = SNAPSHOTS[revision]
    snapshot_bytes = Path(__file__).with_name('repository_fixture').joinpath(filename).read_bytes()
    if hashlib.sha256(snapshot_bytes).hexdigest() != expected_hash:
        raise ValueError('Historical fixture content does not match its pinned snapshot.')
    snapshot = json.loads(snapshot_bytes)
    if snapshot['revision'] != revision or set(snapshot['files']) != set(FILES):
        raise ValueError('Historical fixture revision or file inventory does not match.')
    workspace.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in FILES:
        # Vendored source bytes also work in shallow CI checkouts. Full history
        # is only needed to regenerate these provenance-pinned fixtures.
        data = snapshot['files'][name].encode('utf-8')
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    # Only task instructions accompany the historical files; hidden checks and
    # the known fixed patch are never copied into the agent's workspace.
    (workspace / 'TASK.md').write_text(PROMPT + '\n')
    return {'task_revision': revision, 'files_sha256': files}


def verify(workspace, *, interpreter=sys.executable):
    script = Path(__file__).with_name('repository_fixture') / 'verify_provider_routing.py'
    environment = {key: value for key, value in os.environ.items() if key in {'PATH', 'LANG', 'LC_ALL'}}
    try:
        result = subprocess.run([interpreter, '-I', str(script), str(Path(workspace).resolve())],
                                cwd=workspace, env=environment, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return {'passed': False, 'status': 'verification_error', 'exit_code': result.returncode}
        report = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'passed': False, 'status': 'verification_timeout'}
    except (json.JSONDecodeError, OSError):
        return {'passed': False, 'status': 'verification_error'}
    report['status'] = 'completed'
    report['verifier_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
    return report


def validate_controls():
    with tempfile.TemporaryDirectory(prefix='moyai-repo-task-control-') as directory:
        before, after = Path(directory) / 'before', Path(directory) / 'after'
        prepare(before, BROKEN_REVISION)
        prepare(after, FIXED_REVISION)
        return {'experiment': 'historical-repository-task-controls', 'model_calls': 0,
                'source': 'https://github.com/BerriAI/moyai/pull/178',
                'scope': 'A source slice of the actual repository, graded using full Settings imports and external held-out checks. This is fixture validation, not model execution.',
                'broken_revision': BROKEN_REVISION, 'fixed_revision': FIXED_REVISION,
                'before': verify(before), 'after': verify(after)}


def run(payload, output, private_output):
    if os.environ.get('MOYAI_REGRESSION_LAB_ISOLATED') != '1':
        raise ValueError('Run inside a disposable container with MOYAI_REGRESSION_LAB_ISOLATED=1.')
    from evals.agent import AgentRunError
    from evals.agent_worker import execute
    from .actual import classify_execution_error, package_version, write_private_records, write_report

    workspace = Path(payload['workspace'])
    fixture = prepare(workspace)
    prompt = PROMPT + (
        f' For this prepared fixture, use {sys.executable} -m pytest tests/test_config.py '
        'to run tests; that interpreter has the required dependencies. '
        'The ordinary shell includes ripgrep, grep, find, head, sed, and git.'
    )
    (workspace / 'TASK.md').write_text(prompt + '\n')
    payload = {**payload, 'input': prompt}
    started = time.monotonic()
    report = {'experiment': 'real-agent-historical-repository-task', 'case': 'provider-sdk-routing',
              'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(),
              'source_revision': payload['version'], 'fixture': fixture,
              'model': payload['model'], 'harness': payload['harness'],
              'max_iterations': payload['max_iterations'], 'timeout': payload['timeout'],
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'runtime': {name: package_version(name) for name in ('openai-codex', 'claude-agent-sdk', 'openai', 'pytest')},
              'tooling': {'test_interpreter': sys.executable,
                          'paths': {name: shutil.which(name) for name in ('rg', 'grep', 'find', 'head', 'sed', 'git')}}}
    if report['tooling']['paths']['rg']:
        report['tooling']['ripgrep_version'] = subprocess.check_output(['rg', '--version'], text=True).splitlines()[0]
    write_report(output, report)
    missing = [name for name, path in report['tooling']['paths'].items() if path is None]
    if package_version('pytest') is None:
        missing.append('pytest in the prepared Python environment')
    if missing:
        report.update(status='infrastructure_error', failure_kind='fixture_setup', passed=None,
                      missing_tools=missing, error='Required fixture tools are unavailable: ' + ', '.join(missing),
                      duration_seconds=round(time.monotonic() - started, 3))
        write_private_records(private_output, [{'input': prompt, 'status': report['status'],
                                               'error': report['error'], 'metadata': report}])
        write_report(output, report)
        print(json.dumps({'status': report['status'], 'passed': None, 'missing_tools': missing}), flush=True)
        return report
    try:
        result = execute(payload)
    except AgentRunError as exc:
        report.update(classify_execution_error(exc), passed=None)
        private = {'input': prompt, 'status': report['status'], 'error': str(exc)}
    except Exception as exc:
        report.update(status='infrastructure_error', error_type=type(exc).__name__, passed=None)
        private = {'input': prompt, 'status': 'infrastructure_error', 'error_type': type(exc).__name__}
    else:
        verification = verify(workspace)
        report.update(status='completed', passed=verification['passed'], verification=verification,
                      trace_id=result['trace_id'], session_id=result['session_id'],
                      model_calls=result['model_calls'], tool_calls=result['tool_calls'])
        private = {'input': prompt, 'answer': result['output'], 'verification': verification,
                   'trace_id': result['trace_id'], 'session_id': result['session_id']}
    if report['status'] != 'completed':
        # Retain artifact correctness separately when export or execution fails.
        # A partial patch can pass checks without being a completed agent run.
        report['artifact_verification'] = verify(workspace)
        report['artifact_verification_scope'] = 'Post-execution workspace checks; does not change execution or trace-delivery status'
    report['duration_seconds'] = round(time.monotonic() - started, 3)
    report['changed_files'] = [name for name, digest in fixture['files_sha256'].items()
                               if not (workspace / name).is_file()
                               or hashlib.sha256((workspace / name).read_bytes()).hexdigest() != digest]
    private['changes'] = {name: (workspace / name).read_text(errors='replace')[:1_000_000]
                          if (workspace / name).is_file() else None for name in report['changed_files']}
    private['metadata'] = report
    write_private_records(private_output, [private])
    write_report(output, report)
    print(json.dumps({'status': report['status'], 'passed': report['passed'],
                      'duration_seconds': report['duration_seconds']}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    controls = commands.add_parser('controls')
    controls.add_argument('--output', type=Path, required=True)
    seed = commands.add_parser('prepare')
    seed.add_argument('--workspace', type=Path, required=True)
    grader = commands.add_parser('verify')
    grader.add_argument('--workspace', type=Path, required=True)
    runner = commands.add_parser('run')
    runner.add_argument('--output', type=Path, required=True)
    runner.add_argument('--lens-records', type=Path)
    args = parser.parse_args()
    if args.command == 'controls':
        result = validate_controls()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'before_passed': result['before']['passed'],
                          'after_passed': result['after']['passed']}))
    elif args.command == 'prepare':
        print(json.dumps(prepare(args.workspace)))
    elif args.command == 'verify':
        print(json.dumps(verify(args.workspace)))
    else:
        run(json.load(sys.stdin), args.output, args.lens_records)


if __name__ == '__main__':
    main()
