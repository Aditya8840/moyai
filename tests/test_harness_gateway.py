import json

import httpx
import pytest

from app.harness_gateway import NativeUsageCapture
from app.security import digest
from test_workspace import workspace


@pytest.mark.parametrize('route,body,wire', [
    ('messages', {'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]}],
                  'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}]},
     b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_test","usage":{"input_tokens":11,"output_tokens":0}}}\n\nevent: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":3}}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'),
    ('responses', {'input': [{'type': 'function_call_output', 'call_id': 'one', 'output': 'receipt'}],
                   'tools': [{'type': 'custom', 'name': 'apply_patch', 'format': {'type': 'text'}}]},
     b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_test","status":"completed","usage":{"input_tokens":11,"output_tokens":3}}}\n\n'),
])
def test_native_gateway_preserves_protocol_stream_and_pins_access(workspace, monkeypatch, route, body, wire):
    app, client = workspace
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    app.state.settings.litellm_api_key = 'server-only-key'
    seen = []
    def upstream(request):
        assert str(request.url) == 'https://gateway.example/v1/' + route
        assert request.headers['authorization'] == 'Bearer server-only-key'
        payload = json.loads(request.content)
        seen.append(payload)
        field = 'messages' if route == 'messages' else 'input'
        assert payload[field] == body[field]
        assert payload['tools'] == body['tools']
        assert payload['model'] == 'anthropic/claude-opus-5-5'
        assert payload['stream'] is True
        assert 'api_base' not in payload and 'api_key' not in payload
        return httpx.Response(200, content=wire, headers={'Content-Type': 'text/event-stream'})
    actual = httpx.AsyncClient
    monkeypatch.setattr('app.harness_gateway.httpx.AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(upstream), **kw))
    run = app.state.store.create_run('native gateway', '', 'modal', [], model='anthropic/claude-opus-5-5')
    app.state.store.update_run(run['id'], status='running', token_hash=digest('cap'))
    url = f"/broker/{run['id']}/v1/{route}"
    payload = {**body, 'stream': True, 'model': 'override', 'api_key': 'override', 'api_base': 'https://evil.example'}
    assert client.post(url, json=payload).status_code == 401
    response = client.post(url, json=payload, headers={'Authorization': 'Bearer cap'})
    assert response.status_code == 200 and response.content == wire
    row = app.state.store.rows('SELECT * FROM model_requests WHERE run_id=?', (run['id'],))[0]
    assert row['status'] == 'completed'
    assert (row['prompt_tokens'], row['completion_tokens'], row['total_tokens']) == (11, 3, 14)
    app.state.store.update_run(run['id'], status='stopping', token_hash='')
    assert client.post(url, json=payload, headers={'Authorization': 'Bearer cap'}).status_code == 401
    assert len(seen) == 1


def test_native_error_event_is_not_accounted_as_success():
    capture = NativeUsageCapture(True)
    capture.feed(b'data: {"type":"response.failed","response":{"status":"failed"}}\n\n')
    capture.finish()
    assert capture.done and capture.failed


@pytest.mark.parametrize('route', ['messages', 'responses'])
def test_native_nonstream_response_bytes_unchanged(workspace, monkeypatch, route):
    app, client = workspace
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    wire = b'{"id":"native-id", "usage":{"input_tokens":2,"output_tokens":1},"status":"completed"}'
    actual = httpx.AsyncClient
    monkeypatch.setattr('app.harness_gateway.httpx.AsyncClient', lambda **kw: actual(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=wire)), **kw))
    run = app.state.store.create_run('test native', '', 'modal', [])
    app.state.store.update_run(run['id'], status='running', token_hash=digest('cap'))
    response = client.post(f"/broker/{run['id']}/v1/{route}", headers={'Authorization': 'Bearer cap'},
                           json={'messages': [], 'input': [], 'stream': False})
    assert response.content == wire
