import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from app.db import Store
from sandbox.anthropic_bridge import to_completion, from_completion, message_events
from sandbox.broker_relay import BrokerRelay
from sandbox.broker_transport import unseal
from test_workspace import workspace
from test_slack import slack_app, event, signed
from test_slack_chat import send

OPUS = 'anthropic/claude-opus-5-5'


def test_session_harness_validation_persistence_and_idempotency(workspace, monkeypatch):
    app, client = workspace
    monkeypatch.setattr(app.state.manager, 'submit', lambda run: None)
    assert len(client.get('/api/config').json()['harnesses']) == 6
    assert client.post('/api/runs', json={'prompt': 'bad choice', 'harness': 'unknown'}).status_code == 422
    assert client.post('/api/runs', json={'prompt': 'bad model', 'harness': 'claude-agent-sdk'}).status_code == 422
    body = {'prompt': 'SDK task', 'harness': 'claude-agent-sdk', 'model': OPUS, 'client_id': 'harness-test-1'}
    response = client.post('/api/runs', json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run['harness'] == 'claude-agent-sdk'
    assert client.post('/api/runs', json=body).json()['id'] == run['id']
    assert client.post('/api/runs', json={**body, 'harness': 'hermes'}).status_code == 409
    reopened = Store(app.state.settings.data_dir)
    assert reopened.run(run['id'])['harness'] == 'claude-agent-sdk'
    endpoint = f"/api/runs/{run['id']}/messages"
    assert client.post(endpoint, json={'content': 'follow up', 'client_id': 'harness-followup', 'model': 'astra'}).status_code == 422
    assert client.post(endpoint, json={'content': 'follow up', 'client_id': 'harness-followup'}).status_code == 202
    assert app.state.manager.spec(app.state.store.run(run['id']))['harness'] == 'claude-agent-sdk'
    legacy = client.post('/api/runs', json={'prompt': 'default task'}).json()
    assert legacy['harness'] == 'hermes'


def test_slack_harness_command_and_followups(slack_app):
    app, client, submitted, _ = slack_app
    body = event(text='<@U99999999> harness claude-agent-sdk')
    assert client.post('/hooks/slack/events', **signed(body)).status_code == 200
    assert not submitted
    run = app.state.store.rows('SELECT * FROM runs')[0]
    assert run['harness'] == 'claude-agent-sdk' and run['model'] == OPUS
    send(client, 1, '<@U99999999> Read the repository')
    assert submitted[-1]['harness'] == 'claude-agent-sdk'
    send(client, 2, '<@U99999999> harness hermes')
    assert app.state.store.run(run['id'])['harness'] == 'claude-agent-sdk'
    assert 'fixed' in app.state.store.rows("SELECT text FROM slack_outbox WHERE dedupe_key='command:EvChat2'")[0]['text']
    send(client, 3, '<@U99999999> model astra')
    assert app.state.store.run(run['id'])['model'] == OPUS


def test_slack_harness_with_initial_task(slack_app):
    app, client, submitted, _ = slack_app
    body = event(text='<@U99999999> harness claude-agent-sdk\nRead the repository')
    assert client.post('/hooks/slack/events', **signed(body)).status_code == 200
    assert len(submitted) == 1
    assert submitted[0]['harness'] == 'claude-agent-sdk'
    assert app.state.store.messages(submitted[0]['id'])[0]['content'] == 'Read the repository'


def test_protocol_preserves_tool_receipts_and_images():
    payload = to_completion({'system': [{'type': 'text', 'text': 'system'}], 'messages': [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': '/workspace/a'}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'saved result'}, {'type': 'text', 'text': 'continue'}]}]})
    assert payload['messages'][1]['tool_calls'][0]['id'] == 't1'
    assert payload['messages'][2] == {'role': 'tool', 'tool_call_id': 't1', 'content': 'saved result'}
    result = from_completion({'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'id': 't1', 'function': {'name': 'Read', 'arguments': '{"file_path":"/workspace/a"}'}}]}}]})
    assert result['stop_reason'] == 'tool_use'
    events = b''.join(message_events(result)).decode()
    assert 'input_json_delta' in events and 'message_stop' in events


def test_sdk_wire_uses_existing_sealed_model_broker():
    calls = []
    class Edge(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            assert self.path == '/broker/run/v1/chat/completions'
            value = json.loads(unseal('test-token', '/v1/chat/completions', self.rfile.read(int(self.headers['Content-Length']))))
            calls.append(value)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'choices': [{'message': {'content': 'test fixture'}, 'finish_reason': 'stop'}]}).encode())
    edge = ThreadingHTTPServer(('127.0.0.1', 0), Edge)
    threading.Thread(target=edge.serve_forever, daemon=True).start()
    relay = BrokerRelay(f'http://127.0.0.1:{edge.server_port}/broker/run', 'test-token').start()
    try:
        with httpx.Client(base_url=relay.url) as client:
            payload = {'messages': [{'role': 'user', 'content': 'test'}], 'stream': True}
            assert client.post('/v1/messages', json=payload).status_code == 401
            response = client.post('/v1/messages?beta=true', headers={'x-api-key': 'test-token'}, json=payload)
            assert response.status_code == 200 and 'message_stop' in response.text
            assert calls[0]['stream'] is False
            relay.before_model = lambda messages: False
            assert client.post('/v1/messages', headers={'x-api-key': 'test-token'}, json=payload).status_code == 409
            assert len(calls) == 1
    finally:
        relay.close()
        edge.shutdown()
        edge.server_close()


def test_claude_boundary_preserves_receipts_without_recursive_history():
    from types import SimpleNamespace
    from sandbox.litellm_harness import LiteLLMAgent
    from sandbox.harness_registry import resolve
    from sandbox.continuation import RotationDeadline
    relay = SimpleNamespace(before_model=None)
    agent = LiteLLMAgent(spec={}, relay=relay, config={}, activity=None, step=lambda: None, cwd='/workspace', definition=resolve('claude-agent-sdk'))
    agent.history = [{'role': 'user', 'content': 'previous'}, {'role': 'assistant', 'content': 'done'}]
    agent.prompt = 'followup'
    messages = [{'role': 'system', 'content': 'private'}, {'role': 'user', 'content': 'SAVED CONVERSATION REFERENCE: serialized history'},
                {'role': 'assistant', 'tool_calls': [{'id': 'write1'}]},
                {'role': 'tool', 'tool_call_id': 'write1', 'content': 'write completed'}]
    agent.step = agent.interrupt
    assert agent.before_model(messages) is False
    assert agent.messages[2]['content'] == 'followup'
    assert len(agent.messages) == 5
    deadline = RotationDeadline(0)
    deadline.requested = True
    assert deadline.can_continue({'interrupted': True, 'messages': agent.messages})
    agent.close()
    assert relay.before_model is None


def test_claude_private_tool_prefixes_are_scrubbed():
    from sandbox.memory_history import scrub_memory_history
    from sandbox.activity import ActivityReporter
    messages = [{'role': 'assistant', 'tool_calls': [{'id': 'm', 'function': {
        'name': 'mcp__moyai__memory_save', 'arguments': '{"content":"private note"}'}}]}]
    assert 'private note' not in json.dumps(scrub_memory_history(messages))
    events = []
    reporter = ActivityReporter(lambda *args: events.append(args), tracing=True)
    reporter.start('m', 'mcp__moyai__memory_save', {'content': 'private note'})
    reporter.complete('m', 'mcp__moyai__memory_save', {'content': 'private note'}, {})
    assert 'private note' not in json.dumps(events)


def test_wire_image_and_unsupported_blocks():
    value = to_completion({'messages': [{'role': 'user', 'content': [
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'test-fixture'}}]}]})
    assert value['messages'][0]['content'][0]['image_url']['url'] == 'data:image/png;base64,test-fixture'
    with pytest.raises(ValueError, match='Unsupported'):
        to_completion({'messages': [{'role': 'user', 'content': [{'type': 'unknown'}]}]})


def test_agent_entrypoint_dispatches_claude_without_importing_hermes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sandbox import agent, litellm_harness
    events = []
    relay = SimpleNamespace(url='http://test', control=lambda body=None: {}, last_error='',
                            wait_group='', wait_credential='', before_model=None)
    class FakeClaude:
        def __init__(self, **kwargs):
            assert kwargs['spec']['harness'] == 'claude-agent-sdk'
        def run_conversation(self, prompt, **kwargs):
            return {'completed': True, 'final_response': 'SDK result',
                    'messages': [{'role': 'user', 'content': prompt}, {'role': 'assistant', 'content': 'SDK result'}]}
        def close(self): pass
        def validate(self): pass
    monkeypatch.setattr(litellm_harness, 'LiteLLMAgent', FakeClaude)
    monkeypatch.setattr(agent, 'prepare_attachments', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'prepare_project', lambda *a, **k: None)
    monkeypatch.setattr(agent, 'collect_archive', lambda *a: None)
    monkeypatch.setattr(agent, 'computer_request', lambda *a, **k: {})
    monkeypatch.setattr(agent, 'Path', lambda value: tmp_path / str(value).lstrip('/'))
    monkeypatch.setattr(agent, 'emit', lambda kind, message, data=None, **extra: events.append((kind, message, extra)))
    monkeypatch.setenv('WORKSPACE_RUN_TOKEN', 'test-capability')
    monkeypatch.setenv('HERMES_HOME', '/home')
    monkeypatch.chdir(tmp_path)
    spec = {'harness': 'claude-agent-sdk', 'broker_url': 'http://test', 'repo_url': '', 'model': OPUS,
            'prompt': 'test request', 'chat_enabled': True, 'max_iterations': 2, 'timeout': None}
    assert agent.run_agent(spec, relay) == 0
    assert next(e for e in events if e[0] == 'final')[1:] == ('SDK result', {
        'completed': True, 'continuation': False, 'wait_group': '', 'wait_credential': '',
        'steer_message_id': None, 'steering_applied': []})
    assert json.loads((tmp_path / 'session/conversation.json').read_text())[-1]['content'] == 'SDK result'


def test_registry_extension_reaches_api_without_changing_entrypoint(workspace, monkeypatch):
    from sandbox.harness_registry import HARNESSES, HarnessDefinition, create_agent
    from sandbox import litellm_harness
    app, client = workspace
    monkeypatch.setattr(app.state.manager, 'submit', lambda run: None)
    definition = HarnessDefinition('test-adapter', 'Test adapter', 'litellm_harness', 'LiteLLMAgent',
                                   model_prefix='openai/', litellm_harness='CODEX', runtime_binding='test')
    monkeypatch.setitem(HARNESSES, definition.id, definition)
    assert definition.public() in client.get('/api/config').json()['harnesses']
    run = client.post('/api/runs', json={'prompt': 'registry test', 'harness': definition.id}).json()
    assert run['harness'] == definition.id
    assert client.post('/api/runs', json={'prompt': 'invalid model', 'harness': definition.id, 'model': OPUS}).status_code == 422
    marker = object()
    monkeypatch.setattr(litellm_harness, 'LiteLLMAgent', lambda **context: (context['definition'], marker))
    assert create_agent(definition.id) == (definition, marker)


def test_unregistered_litellm_binding_fails_before_startup():
    from types import SimpleNamespace
    from sandbox.harness_registry import HarnessDefinition
    from sandbox.litellm_harness import LiteLLMAgent
    definition = HarnessDefinition('unsupported', 'Unsupported', 'litellm_harness', 'LiteLLMAgent', litellm_harness='CODEX')
    agent = LiteLLMAgent(spec={}, relay=SimpleNamespace(), config={}, activity=None, step=lambda: None,
                        cwd='/workspace', definition=definition)
    with pytest.raises(ValueError, match='No verified'):
        agent.validate()


@pytest.mark.parametrize('harness,model', [
    ('claude-agent-sdk', OPUS), ('codex', 'openai/gpt-6-astra'),
    ('opencode', OPUS), ('deepagents', OPUS), ('tool-loop', OPUS)])
def test_all_litellm_harnesses_can_be_selected(workspace, monkeypatch, harness, model):
    app, client = workspace
    monkeypatch.setattr(app.state.manager, 'submit', lambda run: None)
    result = client.post('/api/runs', json={'prompt': 'run this harness', 'harness': harness, 'model': model})
    assert result.status_code == 201, result.text
    assert result.json()['harness'] == harness
    assert app.state.manager.spec(app.state.store.run(result.json()['id']))['harness'] == harness


def test_catalog_covers_upstream_harness_enum():
    litellm = pytest.importorskip('litellm.harness')
    from sandbox.harness_registry import HARNESSES
    assert {h.litellm_harness for h in HARNESSES.values() if h.litellm_harness} == {h.name for h in litellm.Harness}


def test_responses_wire_conversion():
    pytest.importorskip('litellm.harness')
    from sandbox.responses_bridge import to_completion, from_completion, message_events
    request = {'model': 'openai/test', 'instructions': 'test instructions', 'input': [
        {'role': 'user', 'content': 'hello'},
        {'type': 'function_call', 'call_id': 'call_one', 'name': 'read_file', 'arguments': '{"path":"a"}'},
        {'type': 'function_call_output', 'call_id': 'call_one', 'output': 'saved receipt'}],
        'tools': [{'type': 'function', 'name': 'read_file', 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}]}
    payload = to_completion(request)
    assert payload['stream'] is False
    assert any(m.get('tool_call_id') == 'call_one' and m['content'] == 'saved receipt' for m in payload['messages'])
    response = from_completion({'id': 'chatcmpl-test', 'model': 'openai/test', 'choices': [
        {'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'done'}}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}, request)
    assert response['status'] == 'completed'
    assert b'response.completed' in b''.join(message_events(response))
    with pytest.raises(ValueError, match='full conversation'):
        to_completion({'previous_response_id': 'resp_old'})


@pytest.mark.parametrize('name', ['moyai_memory_save', 'mcp__moyai__memory_save', 'workspace_call'])
def test_all_harnesses_scrub_private_tool_aliases(name):
    from sandbox.memory_history import scrub_memory_history
    from sandbox.activity import ActivityReporter
    message = {'role': 'assistant', 'tool_calls': [{'function': {'name': name, 'arguments': 'private note'}}]}
    assert 'private note' not in json.dumps(scrub_memory_history([message]))
    events = []
    reporter = ActivityReporter(lambda *args: events.append(args), tracing=True)
    reporter.start('one', name, {'content': 'private note'})
    reporter.complete('one', name, {'content': 'private note'}, 'private note')
    assert 'private note' not in json.dumps(events)
