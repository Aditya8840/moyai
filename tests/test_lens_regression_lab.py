from types import SimpleNamespace

import pytest

from experiments.lens_regression_lab.actual import (
    classify_execution_error, configure_variant, run_experiment, write_private_records,
)
from experiments.lens_regression_lab.offline import run_matrix


@pytest.fixture
def repository_run(monkeypatch, tmp_path):
    from evals import agent_worker
    from experiments.lens_regression_lab import repository_case

    monkeypatch.setenv('MOYAI_REGRESSION_LAB_ISOLATED', '1')
    original_prepare = repository_case.prepare
    captured = {}
    original_check_output = repository_case.subprocess.check_output

    def check_output(command, *args, **kwargs):
        if command == ['rg', '--version']:
            return 'ripgrep test-fixture\n'
        return original_check_output(command, *args, **kwargs)

    # These unit tests replace the model worker, so supply its tool inventory
    # without requiring ripgrep in a developer's or CI's unit-test environment.
    monkeypatch.setattr(repository_case.shutil, 'which', lambda name: '/fixture-tools/' + name)
    monkeypatch.setattr(repository_case.subprocess, 'check_output', check_output)

    def execute(payload):
        captured['payload'] = payload
        if captured.get('error'):
            raise captured['error']
        return {'output': 'Finished the task.', 'trace_id': '1' * 32,
                'session_id': 'synthetic-unit-test-session', 'model_calls': 2, 'tool_calls': 1}

    monkeypatch.setattr(agent_worker, 'execute', execute)
    monkeypatch.setattr(repository_case, 'prepare', lambda workspace: original_prepare(
        workspace, captured.get('revision', repository_case.FIXED_REVISION)))
    payload = {'workspace': str(tmp_path / 'workspace'), 'state': str(tmp_path / 'state'),
               'version': 'a' * 40, 'model': 'test-model', 'harness': 'codex',
               'max_iterations': 32, 'timeout': 360}
    output = tmp_path / 'public-result.json'
    return repository_case, payload, output, captured


def test_should_preserve_trace_delivery_failure_when_recovered_patch_passes(repository_run):
    import json
    from evals.agent import AgentRunError

    experiment, payload, output, captured = repository_run
    captured['error'] = AgentRunError('Lens did not acknowledge all agent, model and tool spans before the deadline.')
    result = experiment.run(payload, output, None)
    saved = json.loads(output.read_text())
    assert result['status'] == saved['status'] == 'infrastructure_error'
    assert result['failure_kind'] == 'trace_delivery'
    assert result['passed'] is saved['passed'] is None
    assert result['artifact_verification']['passed'] is True
    assert result['artifact_verification']['passed_checks'] == 16
    assert 'verification' not in result


@pytest.mark.parametrize(('revision', 'passed', 'checks'), [
    ('61818fa91f3cd37db38b0d3d25ce4a8f6a1fce82', False, 12),
    ('5fd2114a74fd48cd6be7fa27175cb9c45a459946', True, 16),
])
def test_should_grade_completed_repository_runs_using_actual_workspace(repository_run, revision, passed, checks):
    experiment, payload, output, captured = repository_run
    captured['revision'] = revision
    result = experiment.run(payload, output, None)
    assert result['status'] == 'completed'
    assert result['passed'] is passed
    assert result['verification']['passed_checks'] == checks
    assert result['trace_id'] == '1' * 32
    assert 'artifact_verification' not in result


def test_should_give_both_models_the_available_test_interpreter(repository_run):
    import hashlib
    import sys

    experiment, payload, output, captured = repository_run
    result = experiment.run(payload, output, None)
    prompt = captured['payload']['input']
    assert f'{sys.executable} -m pytest tests/test_config.py' in prompt
    assert result['prompt_sha256'] == hashlib.sha256(prompt.encode()).hexdigest()
    assert result['tooling']['test_interpreter'] == sys.executable


def test_should_stop_fixture_setup_before_model_calls_when_tool_is_missing(repository_run, monkeypatch):
    import json

    experiment, payload, output, captured = repository_run
    original_which = experiment.shutil.which
    monkeypatch.setattr(experiment.shutil, 'which', lambda name: None if name == 'rg' else original_which(name))
    result = experiment.run(payload, output, None)
    assert 'payload' not in captured, 'The model worker must not run with incomplete fixture tooling'
    assert result['status'] == 'infrastructure_error'
    assert result['failure_kind'] == 'fixture_setup'
    assert result['passed'] is None
    assert result['missing_tools'] == ['rg']
    assert json.loads(output.read_text())['passed'] is None


def test_should_measure_false_negatives_against_the_real_versioned_verifier():
    result = run_matrix()
    assert result['model_calls'] == 0
    assert result['summary'] == {
        'known_bad_outputs': 12, 'current_detected': 8, 'stronger_detected': 12,
        'correct_controls_current_passed': 4, 'correct_controls_stronger_passed': 4,
    }
    assert {row['mutation'] for row in result['mutations'] if not row['current_detected']} == {
        'stringify-deduplication-keys', 'drop-zero-width-intervals',
        'size-one-shortcut', 'accept-numeric-aliases',
    }


def test_should_refuse_real_model_execution_without_disposable_runtime(monkeypatch):
    monkeypatch.delenv('MOYAI_REGRESSION_LAB_ISOLATED', raising=False)
    with pytest.raises(ValueError, match='disposable container'):
        run_experiment(SimpleNamespace())


def test_should_change_one_agent_configuration_value_per_variant(tmp_path):
    from dataclasses import replace
    from evals.agent import MoyaiAgent

    baseline = MoyaiAgent(tmp_path / 'workspace', 'baseline-model', 'https://gateway.example',
                          'secret', 'https://lens.example/v1/traces', 'secret', 'a' * 40)
    limited = configure_variant(baseline, 'max-turns-1')
    alternative = configure_variant(baseline, 'alternate-model', alternate_model='candidate-model')
    harness = configure_variant(baseline, 'alternate-harness', alternate_harness='claude-agent-sdk')
    assert baseline.max_iterations == 16
    assert limited == replace(baseline, max_iterations=1)
    assert alternative == replace(baseline, model='candidate-model')
    assert harness == replace(baseline, harness='claude-agent-sdk')


@pytest.mark.parametrize(('message', 'status', 'cause'), [
    ('Lens did not acknowledge all agent, model and tool spans before the deadline.',
     'infrastructure_error', 'trace_delivery'),
    ('The isolated Moyai broker did not become ready.', 'infrastructure_error', 'broker_startup'),
    ('Moyai stopped before completing the task or settling all tool calls.',
     'execution_error', 'incomplete_execution'),
    ('Moyai exceeded its execution and trace-delivery deadline.', 'execution_error', 'combined_deadline'),
    ('Moyai execution failed (ModuleNotFoundError).', 'execution_error', 'unknown_worker_failure'),
])
def test_should_not_count_unscored_execution_errors_as_quality_failures(message, status, cause):
    from evals.agent import AgentRunError

    result = classify_execution_error(AgentRunError(message))
    assert result['status'] == status and result['failure_kind'] == cause
    assert result['verification_status'] == 'not_run'
    assert result['causality'] == 'unverified'
    assert result['current_passed'] is None and result['stronger_passed'] is None


def test_should_save_private_scoring_evidence_with_restricted_permissions(tmp_path):
    import json
    import stat

    path = tmp_path / 'private.json'
    write_private_records(path, [{'answer': 'actual answer'}])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == [{'answer': 'actual answer'}]
    write_private_records(path, [{'answer': 'next answer'}])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == [{'answer': 'next answer'}]


@pytest.mark.parametrize(('revision', 'config_hash', 'failed_checks'), [
    ('61818fa91f3cd37db38b0d3d25ce4a8f6a1fce82',
     'ffe661e7a6e66dea8c6bcf9fe3d905b5c1b87522fc488100166aae75a9c1f59d',
     {'sol-alias', 'sol-display-name', 'future-openai-default', 'future-catalog-display-name'}),
    ('5fd2114a74fd48cd6be7fa27175cb9c45a459946',
     'c01f8175af2786a71b7b85677a2f8b4cf0940a32232023161f6d3756f6a39f6c', set()),
])
def test_should_grade_the_historical_repository_source_without_model_calls(
    tmp_path, revision, config_hash, failed_checks,
):
    import hashlib
    from experiments.lens_regression_lab.repository_case import FILES, prepare, verify

    workspace = tmp_path / 'repository'
    source = prepare(workspace, revision)
    assert source['task_revision'] == revision
    assert source['files_sha256']['app/config.py'] == config_hash
    assert hashlib.sha256((workspace / 'app/config.py').read_bytes()).hexdigest() == config_hash
    # Agent-visible files must not include the external grader or fixed solution.
    assert {str(path.relative_to(workspace)) for path in workspace.rglob('*') if path.is_file()} == {
        *FILES, 'TASK.md',
    }

    result = verify(workspace)
    assert result['status'] == 'completed'
    assert result['total_checks'] == 16
    assert {check['name'] for check in result['checks'] if not check['passed']} == failed_checks
    assert result['passed_checks'] == 16 - len(failed_checks)
    assert result['passed'] is (not failed_checks)


def test_should_preserve_existing_files_when_preparing_a_repository_fixture(tmp_path):
    from experiments.lens_regression_lab.repository_case import prepare

    existing = tmp_path / 'important.py'
    existing.write_text('existing work\n')
    with pytest.raises(ValueError, match='must be empty'):
        prepare(tmp_path)
    assert existing.read_text() == 'existing work\n'


def test_should_prepare_historical_tasks_without_git_history(tmp_path, monkeypatch):
    from experiments.lens_regression_lab import repository_case

    def unexpected_process(*args, **kwargs):
        raise AssertionError('Preparing a pinned fixture must not invoke Git or a subprocess.')

    monkeypatch.setattr(repository_case.subprocess, 'check_output', unexpected_process)
    source = repository_case.prepare(tmp_path)
    assert source['task_revision'] == repository_case.BROKEN_REVISION
    assert (tmp_path / 'app' / 'config.py').is_file()


def test_should_reject_an_unpinned_historical_revision(tmp_path):
    from experiments.lens_regression_lab.repository_case import prepare

    with pytest.raises(ValueError, match='Only the pinned'):
        prepare(tmp_path, 'main')
    assert not any(tmp_path.iterdir())
