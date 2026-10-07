import base64
import time

import httpx
import pytest

from app.config import Settings
from app.db import Store
from app.tracing import AgentTracing
from test_trace_outbox import spans, transport


def settings(**kwargs):
    return Settings(_env_file=None, langfuse_public_key='pk-lf-test',
                    langfuse_secret_key='sk-lf-test', **kwargs)


@pytest.mark.parametrize('public,secret', [('', ''), ('pk-lf-test', ''), ('', 'sk-lf-test')])
def test_incomplete_credentials_do_not_enable_langfuse(public, secret):
    config = Settings(_env_file=None, langfuse_public_key=public, langfuse_secret_key=secret)
    assert config.trace_destinations() == []


@pytest.mark.parametrize('url', ['http://langfuse.example', 'https://key@langfuse.example',
                                'https://langfuse.example?key=x', 'https://langfuse.example#fragment'])
def test_langfuse_rejects_insecure_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        settings(langfuse_base_url=url)


async def test_langfuse_only_exports_typed_tree_and_redacts_keys(tmp_path):
    store = Store(tmp_path)
    tracing = store.tracing = AgentTracing(store, settings(langfuse_base_url='https://us.cloud.langfuse.com/'))
    assert tracing.enabled
    assert len(tracing.outboxes) == 1
    run = store.create_run('Read example.txt sk-lf-test', '', 'modal', [], chat_enabled=True)
    message = store.claim_message(run['id'])
    run = store.run(run['id'])
    stamp = time.time_ns()
    tracing.model(run, 'request', stamp,
                  [{'role': 'system', 'content': 'private-system'}, {'role': 'user', 'content': 'Read example.txt'}],
                  {'choices': [{'message': {'content': 'hello', 'reasoning_content': 'private-thought'}}],
                   'usage': {'prompt_tokens': 10, 'completion_tokens': 4}}, 'completed')
    tracing.tool(run['id'], {'tool': 'read_file', 'call_id': 'file', 'start_ns': stamp, 'end_ns': time.time_ns(),
                            'input': 'example.txt', 'output': 'hello pk-lf-test'})
    store.finish_message(run['id'], message['id'], 'hello sk-lf-test')
    requests = []
    def receiver(request):
        requests.append(request)
        return httpx.Response(200)
    await transport(tracing, receiver)
    assert await tracing.outboxes[0].export_once()
    request = requests[0]
    assert str(request.url) == 'https://us.cloud.langfuse.com/api/public/otel/v1/traces'
    assert request.headers['authorization'] == 'Basic ' + base64.b64encode(b'pk-lf-test:sk-lf-test').decode()
    assert request.headers['x-langfuse-ingestion-version'] == '4'
    assert request.headers['content-type'] == 'application/x-protobuf'
    exported = spans(request.content)
    attrs = lambda span: {a.key: a.value.string_value for a in span.attributes}
    by_type = {attrs(span)['langfuse.observation.type']: span for span in exported}
    assert set(by_type) == {'agent', 'generation', 'tool'}
    root, generation, tool = (by_type[name] for name in ('agent', 'generation', 'tool'))
    assert not root.parent_span_id
    assert generation.parent_span_id == tool.parent_span_id == root.span_id
    assert len({span.trace_id for span in exported}) == 1
    assert attrs(root)['input.value'] == 'Read example.txt [redacted]'
    assert attrs(root)['output.value'] == 'hello [redacted]'
    assert attrs(generation)['gen_ai.request.model'] == run['model']
    assert next(a.value.int_value for a in generation.attributes if a.key == 'gen_ai.usage.input_tokens') == 10
    for span in exported:
        assert attrs(span)['session.id'] == run['id']
        assert attrs(span)['langfuse.environment'] == 'development'
        assert attrs(span)['langfuse.trace.name'] == 'moyai'
        assert attrs(span)['langfuse.observation.metadata.session_url'].endswith('/#run=' + run['id'])
    for private in (b'pk-lf-test', b'sk-lf-test', b'private-system', b'private-thought'):
        assert private not in request.content
    await tracing.close()


async def test_langfuse_outage_is_independent_and_recovers_after_restart(tmp_path):
    config = settings(litellm_trace_endpoint='https://traces.example/v1/traces',
                      litellm_trace_api_key='lite-key', raindrop_write_key='rain-key')
    store = Store(tmp_path)
    tracing = store.tracing = AgentTracing(store, config)
    run = store.create_run('Say hello', '', 'modal', [], chat_enabled=True)
    message = store.claim_message(run['id'])
    store.finish_message(run['id'], message['id'], 'hello')
    delivered = {}
    def receiver(name, code):
        def handle(request):
            delivered.setdefault(name, []).append(request.content)
            return httpx.Response(code)
        return handle
    for i, name in enumerate(('litellm', 'raindrop', 'langfuse')):
        await transport(tracing, receiver(name, 503 if name == 'langfuse' else 200), i)
    assert [await box.export_once() for box in tracing.outboxes] == [True, True, False]
    assert store.messages(run['id'])[-1]['content'] == 'hello'
    pending = store.rows('SELECT * FROM trace_outbox_langfuse')[0]
    assert pending['payload'] and pending['attempts'] == 1 and pending['delivered_at'] is None
    assert pending['last_error'] == 'HTTP 503'
    await tracing.close()
    reopened = Store(tmp_path)
    resumed = AgentTracing(reopened, config)
    reopened.execute('UPDATE trace_outbox_langfuse SET next_attempt_at=0')
    for i, name in enumerate(('litellm', 'raindrop', 'langfuse')):
        await transport(resumed, receiver(name, 200), i)
    assert [await box.export_once() for box in resumed.outboxes] == [False, False, True]
    assert len(delivered['litellm']) == len(delivered['raindrop']) == 1
    assert delivered['langfuse'][0] == delivered['langfuse'][1]
    receipt = reopened.rows('SELECT * FROM trace_outbox_langfuse')[0]
    assert receipt['delivered_at'] and receipt['payload'] is None
    resumed.finish_turn(run['id'], message['id'], 'hello', 'completed')
    assert not await resumed.outboxes[2].export_once()
    await resumed.close()
