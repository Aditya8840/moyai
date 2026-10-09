import copy
import json
from types import SimpleNamespace

import pytest

from experiments.lens_regression_lab.publish import publish


class LensApi:
    """In-memory result API with observable writes and controllable scoring."""

    def __init__(self):
        self.calls = []
        self.runs = {}
        self.keys = {}
        self.polls = []
        self.result_error = None
        self.create_error = None
        self.baseline_passes = True
        self.forced_baseline = None
        self.source = {'name': 'moyai-python-coding-regressions', 'spec': {
            'agent': 'moyai', 'dataset_id': 'tasks', 'revision': 1, 'trials': 1,
            'scorers': [{'kind': 'task_completed'}], 'gate': {'pass_rate': 1},
            'timeout_per_trial_ms': 1000}}

    def __call__(self, method, path, body=None, extra=None):
        self.calls.append((method, path, copy.deepcopy(body), extra))
        if path == '/lens/evals/moyai-python-coding-regressions':
            assert method == 'GET', 'The production definition must remain read-only'
            return copy.deepcopy(self.source)
        if path == '/lens/datasets/tasks/revisions/1/cases':
            assert method == 'GET'
            return {'cases': [{'id': str(i), 'messages': [{'content': f'input-{i}'}]} for i in range(2)]}
        if path.startswith('/lens/evals/moyai-regression-lab-'):
            assert method == 'PUT'
            return None
        if path == '/lens/evals/runs':
            key = extra['Idempotency-Key']
            if key in self.keys:
                return copy.deepcopy(self.runs[self.keys[key]])
            run_id = str(len(self.runs) + 1)
            self.keys[key] = run_id
            self.runs[run_id] = {'id': run_id, 'status': 'running', 'url': f'https://lens.test/{run_id}',
                                 'branch': body['branch']}
            if self.create_error is not None:
                error, self.create_error = self.create_error, None
                raise error
            return copy.deepcopy(self.runs[run_id])
        run_id = path.split('/')[4].split('?')[0]
        run = self.runs[run_id]
        if '/results/' in path:
            assert method == 'PUT' and run['status'] == 'running'
            if self.result_error is not None:
                error, self.result_error = self.result_error, None
                raise error
            return None
        if path.endswith('/finish'):
            run['status'] = 'scoring'
        elif '?wait=' in path:
            if self.polls:
                status = self.polls.pop(0)
                if isinstance(status, Exception):
                    raise status
                if status != 'done':
                    run['status'] = status
                    return copy.deepcopy(run)
            run['status'] = 'done'
            baseline = next((item['id'] for item in reversed(list(self.runs.values()))
                             if item['branch'] == 'main' and item['status'] == 'done'
                             and item['id'] != run_id), None)
            run['summary'] = {'gate': {'passed': self.baseline_passes if run['branch'] == 'main' else True},
                              'baseline_run_id': self.forced_baseline or baseline}
        elif path.endswith('/cases'):
            assert run['status'] in ('done', 'failed')
            return [{'case_id': '0', 'passed': True}, {'case_id': '1', 'passed': True}]
        return copy.deepcopy(run)


def arguments(tmp_path, variants=('baseline', 'candidate')):
    records = [{'variant': variant, 'input': f'input-{i}', 'status': 'completed',
                'answer': 'Completed task', 'verification': {'passed': True},
                'trace_id': f'{i:032x}', 'metadata': {'source_revision': 'a' * 40}}
               for variant in variants for i in range(2)]
    path = tmp_path / 'private.json'
    path.write_text(json.dumps(records))
    return SimpleNamespace(records=path, output=tmp_path / 'report.json',
                           name='moyai-regression-lab-test', variant=None)


@pytest.mark.parametrize('name', ['moyai-python-coding-regressions',
                                  'moyai-regression-lab-../../moyai-python-coding-regressions'])
def test_should_reject_production_or_path_escape_names_before_any_api_call(tmp_path, name):
    args = arguments(tmp_path)
    args.name = name
    api = LensApi()
    with pytest.raises(ValueError, match='isolated'):
        publish(args, api)
    assert api.calls == []


def test_should_reject_partial_candidate_before_publishing_even_complete_baseline(tmp_path):
    args = arguments(tmp_path)
    records = json.loads(args.records.read_text())
    args.records.write_text(json.dumps(records[:-1]))
    api = LensApi()
    with pytest.raises(ValueError, match='exactly 2'):
        publish(args, api)
    assert all(method == 'GET' for method, *_ in api.calls)


def test_should_require_a_completed_passing_baseline_before_candidate_only_upload(tmp_path):
    args = arguments(tmp_path, variants=('candidate',))
    api = LensApi()
    with pytest.raises(ValueError, match='passing baseline'):
        publish(args, api)
    assert all(method == 'GET' for method, *_ in api.calls)


def test_should_publish_baseline_first_then_candidate_and_not_repeat_completed_writes(tmp_path):
    args = arguments(tmp_path, variants=('candidate', 'baseline'))
    api = LensApi()
    result = publish(args, api, emit=lambda _: None)
    assert [row['variant'] for row in result['runs']] == ['baseline', 'candidate']
    assert result['runs'][1]['baseline_pairing'] == {'expected': '1', 'actual': '1', 'matches': True}
    assert all(body['eval'] == args.name for method, path, body, _ in api.calls
               if method == 'POST' and path == '/lens/evals/runs')
    first_calls = len(api.calls)
    assert publish(args, api, emit=lambda _: None) == result
    assert all(method == 'GET' for method, *_ in api.calls[first_calls:])


def test_should_resume_scoring_without_recreating_run_or_reuploading_results(tmp_path):
    args = arguments(tmp_path, variants=('baseline',))
    api = LensApi()
    api.polls = [ConnectionError('temporary network failure')]
    with pytest.raises(ConnectionError):
        publish(args, api, emit=lambda _: None)
    assert json.loads(args.output.read_text())['runs'][0]['run']['status'] == 'scoring'
    calls_before_resume = len(api.calls)
    report = publish(args, api, emit=lambda _: None)
    assert report['runs'][0]['run']['status'] == 'done'
    assert len(api.runs) == 1
    assert all(method == 'GET' for method, *_ in api.calls[calls_before_resume:])


def test_should_resume_partial_result_upload_with_same_run_and_exact_evidence(tmp_path):
    args = arguments(tmp_path, variants=('baseline',))
    api = LensApi()
    api.result_error = ConnectionError('result acknowledgement interrupted')
    with pytest.raises(ConnectionError):
        publish(args, api, emit=lambda _: None)
    saved_id = json.loads(args.output.read_text())['runs'][0]['run']['id']
    result = publish(args, api, emit=lambda _: None)
    assert result['runs'][0]['run']['id'] == saved_id
    assert len(api.runs) == 1
    retries = [body for method, path, body, _ in api.calls if '/results/0/0' in path]
    assert len(retries) == 2 and retries[0] == retries[1]


def test_should_recover_lost_create_acknowledgement_with_same_idempotency_key(tmp_path):
    args = arguments(tmp_path, variants=('baseline',))
    api = LensApi()
    api.create_error = ConnectionError('creation response lost')
    with pytest.raises(ConnectionError):
        publish(args, api, emit=lambda _: None)
    assert json.loads(args.output.read_text())['runs'] == []
    report = publish(args, api, emit=lambda _: None)
    creates = [extra for method, path, _, extra in api.calls if method == 'POST' and path == '/lens/evals/runs']
    assert len(creates) == 2 and creates[0]['Idempotency-Key'] == creates[1]['Idempotency-Key']
    assert len(api.runs) == 1 and len(report['runs']) == 1


def test_should_preserve_prior_baseline_when_later_invocation_adds_candidate(tmp_path):
    args = arguments(tmp_path)
    args.variant = ['baseline']
    api = LensApi()
    before = publish(args, api, emit=lambda _: None)['runs'][0]
    args.variant = ['candidate']
    after = publish(args, api, emit=lambda _: None)
    assert after['runs'][0] == before
    assert [row['variant'] for row in after['runs']] == ['baseline', 'candidate']


def test_should_reject_report_from_different_eval_without_losing_prior_results(tmp_path):
    args = arguments(tmp_path, variants=('baseline',))
    api = LensApi()
    publish(args, api, emit=lambda _: None)
    saved = args.output.read_bytes()
    args.name = 'moyai-regression-lab-other'
    call_count = len(api.calls)
    with pytest.raises(ValueError, match='another eval'):
        publish(args, api, emit=lambda _: None)
    assert args.output.read_bytes() == saved
    assert all(method == 'GET' for method, *_ in api.calls[call_count:])


def test_should_refuse_scorer_configuration_drift_before_resumed_writes(tmp_path):
    args = arguments(tmp_path)
    args.variant = ['baseline']
    api = LensApi()
    publish(args, api, emit=lambda _: None)
    saved = args.output.read_bytes()
    api.source['spec']['gate']['pass_rate'] = 0.5
    args.variant = ['candidate']
    call_count = len(api.calls)
    with pytest.raises(ValueError, match='scorer/dataset'):
        publish(args, api, emit=lambda _: None)
    assert args.output.read_bytes() == saved
    assert all(method == 'GET' for method, *_ in api.calls[call_count:])


def test_should_stop_after_failed_baseline_without_creating_candidate(tmp_path):
    args = arguments(tmp_path)
    api = LensApi()
    api.baseline_passes = False
    with pytest.raises(ValueError, match='baseline did not pass'):
        publish(args, api, emit=lambda _: None)
    assert len(api.runs) == 1
    assert json.loads(args.output.read_text())['runs'][0]['run']['summary']['gate']['passed'] is False


def test_should_preserve_but_reject_a_candidate_scored_against_another_baseline(tmp_path):
    args = arguments(tmp_path)
    api = LensApi()
    api.forced_baseline = 'foreign-baseline'
    with pytest.raises(ValueError, match='different baseline'):
        publish(args, api, emit=lambda _: None)
    candidate = json.loads(args.output.read_text())['runs'][1]
    assert candidate['baseline_pairing']['matches'] is False
    with pytest.raises(ValueError, match='different baseline'):
        publish(args, api, emit=lambda _: None)
    assert len(api.runs) == 2


def test_should_checkpoint_timeout_without_fetching_unfinished_cases(tmp_path):
    args = arguments(tmp_path, variants=('baseline',))
    api = LensApi()
    ticks = iter((0, 601))
    with pytest.raises(TimeoutError, match='resume'):
        publish(args, api, clock=lambda: next(ticks), emit=lambda _: None)
    report = json.loads(args.output.read_text())
    assert report['runs'][0]['run']['status'] == 'scoring'
    assert not any(path.endswith('/cases') and '/runs/' in path for _, path, *_ in api.calls)
