import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fastapi.responses import JSONResponse

from app.db import Store
from app.security import digest
from app.spend import UsageCapture, money, stamp
from test_workspace import workspace
from test_slack import slack_app
from test_slack import event, signed
from test_slack_chat import start


def sign_in(app, client, sub='alice', email='alice@berri.ai'):
    app.state.settings.google_client_id = 'google-client'
    app.state.settings.google_client_secret = 'google-secret'
    app.state.settings.google_admin_emails = 'alice@berri.ai'
    response = JSONResponse({})
    sid = app.state.security.new_session(response, identity={'sub': sub, 'email': email, 'domain': 'berri.ai', 'name': sub.title()})
    client.cookies.set('workspace_session', response.headers['set-cookie'].split('workspace_session=')[1].split(';')[0])
    client.headers['X-CSRF-Token'] = app.state.security.csrf(sid)
    client.headers['Origin'] = app.state.settings.public_url
    assert client.get('/api/session').json()['authenticated']
    return 'google:' + sub


def active(app, user='google:alice', model='openai/gpt-6-astra'):
    run = app.state.store.create_run('Spend test', '', 'modal', [], chat_enabled=True, user_id=user, model=model)
    app.state.store.claim_message(run['id'])
    app.state.store.update_run(run['id'], status='running', token_hash=digest('capability'))
    return app.state.store.run(run['id'])


def test_sso_owner_and_per_turn_actor_are_server_bound_and_persist(workspace, monkeypatch):
    app, client = workspace
    monkeypatch.setattr(app.state.manager, 'submit', lambda run: None)
    alice = sign_in(app, client)
    run = client.post('/api/runs', json={'prompt': 'First response'}).json()
    assert run['owner_id'] == alice
    first = app.state.store.claim_message(run['id'])
    bob = sign_in(app, client, 'bob', 'bob@berri.ai')
    data = {'content': 'Second response', 'client_id': 'a-new-message'}
    assert client.post(f"/api/runs/{run['id']}/messages", json=data).status_code == 202
    assert client.post('/api/runs', json={'prompt': 'Fake user', 'user_id': alice}).status_code == 422
    assert app.state.store.run(run['id'])['active_user_id'] == alice
    assert app.state.store.run(run['id'])['owner_id'] == alice
    app.state.store.finish_message(run['id'], first['id'], 'Done')
    app.state.store.claim_message(run['id'])
    assert app.state.store.run(run['id'])['active_user_id'] == bob
    sign_in(app, client)
    assert client.post(f"/api/runs/{run['id']}/messages", json=data).status_code == 409
    reopened = Store(app.state.settings.data_dir)
    assert reopened.run(run['id'])['owner_id'] == alice
    assert reopened.run(run['id'])['active_user_id'] == bob


def test_spend_is_admin_only_and_mutations_require_csrf(workspace):
    app, client = workspace
    sign_in(app, client)
    assert client.get('/api/admin/spend').status_code == 200
    assert client.post('/api/admin/spend/sync', headers={'X-CSRF-Token': ''}).status_code == 403
    sign_in(app, client, 'bob', 'bob@berri.ai')
    for url in ['/api/admin/spend', '/api/admin/spend/sync', '/api/admin/spend/link-slack']:
        response = client.get(url) if url.endswith('spend') else client.post(url, json={'slack_user_id':'x','google_user_id':'y'})
        assert response.status_code == 403
    client.cookies.clear()
    assert client.get('/api/admin/spend').status_code == 401


def test_broker_records_exact_cost_and_blocks_sandbox_attribution_override(workspace, monkeypatch):
    app, client = workspace
    sign_in(app, client)
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    run = active(app)
    app.state.store.enqueue_message(run['id'], 'Queued by Bob', 'bobs-next-message', user_id='google:bob')
    captured = []
    def gateway(request):
        data = json.loads(request.content)
        captured.append(data)
        assert data['user'] == 'moyai:google:alice'
        assert data['metadata']['session_id'] == run['id']
        assert data['metadata']['moyai_request_id'] == request.headers['x-litellm-call-id']
        return httpx.Response(200, headers={'x-litellm-response-cost':'0.0123456789', 'x-litellm-call-id':request.headers['x-litellm-call-id']}, json={'id':'response','choices':[], 'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}})
    real = httpx.AsyncClient
    monkeypatch.setattr('app.main.httpx.AsyncClient', lambda **kw: real(transport=httpx.MockTransport(gateway), **kw))
    response = client.post(f"/broker/{run['id']}/v1/chat/completions", headers={'Authorization':'Bearer capability'}, json={'messages':[], 'user':'someone-else','metadata':{'moyai_request_id':'spoof'}})
    assert response.status_code == 200
    row = app.state.store.rows('SELECT * FROM model_requests')[0]
    assert row['user_id'] == 'google:alice' and row['cost'] == '0.0123456789'
    assert row['total_tokens'] == 110 and row['status'] == 'completed'
    report = client.get('/api/admin/spend').json()
    assert report['total']['spend'] == '0.0123456789'
    assert report['total']['unreconciled_requests'] == 1
    assert 'spend-test-key' not in json.dumps(report)


def test_stream_parser_handles_split_utf8_and_final_usage_without_storing_text():
    raw = ('data: '+json.dumps({'choices':[{'delta':{'content':'hello ö'}}]},ensure_ascii=False)+'\n\ndata: '+json.dumps({'usage':{'total_tokens':50},'x_litellm_response_cost':0.000125})+'\n\ndata: [DONE]\n\n').encode()
    capture = UsageCapture(True)
    for byte in raw:
        capture.feed(bytes([byte]))
    capture.finish()
    assert capture.done and capture.usage['total_tokens'] == 50 and capture.cost == '0.000125'
    assert capture.buffer == b''
    for value in ('NaN','Infinity','-1',True,'garbage'):
        assert money(value) is None
    assert money('0') == '0'


def test_streaming_zero_header_is_pending_not_free_and_failures_are_recorded(workspace, monkeypatch):
    app, client = workspace
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    run = active(app)
    def gateway(request):
        data = json.loads(request.content)
        assert data['stream_options'] == {'include_usage':True}
        return httpx.Response(200, headers={'x-litellm-response-cost':'0'}, content=b'data: {"usage":{"total_tokens":9}}\n\ndata: [DONE]\n\n')
    real = httpx.AsyncClient
    monkeypatch.setattr('app.main.httpx.AsyncClient', lambda **kw: real(transport=httpx.MockTransport(gateway), **kw))
    url = f"/broker/{run['id']}/v1/chat/completions"
    assert client.post(url,headers={'Authorization':'Bearer capability'},json={'messages':[], 'stream':True}).status_code == 200
    row = app.state.store.rows('SELECT * FROM model_requests')[0]
    assert row['cost'] is None and row['total_tokens'] == 9
    assert app.state.spend.report()['total']['pending_costs'] == 1
    def fail(request):
        return httpx.Response(429)
    monkeypatch.setattr('app.main.httpx.AsyncClient', lambda **kw: real(transport=httpx.MockTransport(fail), **kw))
    assert client.post(url,headers={'Authorization':'Bearer capability'},json={'messages':[]}).status_code == 502
    assert app.state.store.rows("SELECT * FROM model_requests WHERE status='failed'")


def test_reconciliation_paginates_deduplicates_and_includes_legacy_usage(workspace, monkeypatch):
    app, client = workspace
    sign_in(app, client)
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    run = active(app)
    request_id, _ = app.state.spend.begin(run, run['model'])
    app.state.store.execute("UPDATE model_requests SET cost='0.1' WHERE id=?", (request_id,))
    today = datetime.now(timezone.utc).isoformat()
    rows = [{'request_id':request_id,'api_key':digest('spend-test-key'),'startTime':today,'model':run['model'],'spend':0.12,'total_tokens':100},
            {'request_id':'before-tracking','api_key':digest('spend-test-key'),'startTime':today,'model':run['model'],'spend':0.23,'total_tokens':200}]
    def gateway(request):
        assert request.url.params['api_key'] == digest('spend-test-key')
        page = int(request.url.params['page'])
        return httpx.Response(200,json={'data':[rows[page-1]],'total':2})
    real = httpx.AsyncClient
    monkeypatch.setattr('app.spend.httpx.AsyncClient', lambda **kw: real(transport=httpx.MockTransport(gateway), **kw))
    for _ in range(2):
        report = client.post('/api/admin/spend/sync').json()
        assert report['sync']['status'] == 'synced'
        assert Decimal(report['total']['spend']) == Decimal('0.35')
        assert report['total']['spend'] == report['gateway_spend']
        assert report['total']['requests'] == 2 and report['total']['unreconciled_requests'] == 0
        assert {x['id']:x['spend'] for x in report['users']} == {'google:alice':'0.12','unattributed':'0.23'}
    assert len(app.state.store.rows('SELECT * FROM gateway_usage')) == 2


def test_unavailable_or_wrong_key_report_never_invents_costs(workspace, monkeypatch):
    app, client = workspace
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    real = httpx.AsyncClient
    for response in [httpx.Response(403), httpx.Response(200,json={'data':[{'api_key':'wrong-key'}],'total':1})]:
        monkeypatch.setattr('app.spend.httpx.AsyncClient', lambda **kw: real(transport=httpx.MockTransport(lambda req: response), **kw))
        report = client.post('/api/admin/spend/sync').json()
        assert report['sync']['status'] == 'unavailable'
        assert report['gateway_requests'] == 0
    assert client.get('/api/admin/spend?start=2026-01-01&end=2026-12-31').status_code == 422
    assert client.get('/api/admin/spend?start=bad').status_code == 422


def test_slack_each_sender_is_tracked_and_admin_can_link_to_sso(slack_app):
    app, client, run_id = start(slack_app)
    first = app.state.store.messages(run_id)[0]
    assert first['user_id'].startswith('slack:')
    assert app.state.store.run(run_id)['owner_id'] == first['user_id']
    sign_in(app, client)
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.store.claim_message(run_id)
    run = app.state.store.run(run_id)
    request_id, _ = app.state.spend.begin(run, run['model'])
    app.state.store.execute("UPDATE model_requests SET cost='0.5' WHERE id=?", (request_id,))
    assert client.post('/api/admin/spend/link-slack',json={'slack_user_id':first['user_id'],'google_user_id':'google:alice'}).status_code == 200
    report = client.get('/api/admin/spend').json()
    assert report['users'][0]['id'] == 'google:alice' and report['users'][0]['spend'] == '0.5'
    assert app.state.store.rows('SELECT * FROM identity_audit')[0]['actor_id'] == 'google:alice'
    assert app.state.store.rows('SELECT user_id FROM model_requests')[0]['user_id'] == first['user_id']
    assert client.post('/api/admin/spend/link-slack',json={'slack_user_id':'google:alice','google_user_id':'google:alice'}).status_code == 422


def test_time_normalization_uses_utc():
    assert stamp('2026-09-28T22:00:00-07:00') == '2026-09-29T05:00:00+00:00'
    assert stamp('2026-09-29T05:00:00') == stamp('2026-09-29T05:00:00Z')


def test_google_subject_survives_email_changes(workspace):
    app, client = workspace
    sign_in(app, client)
    sign_in(app, client, 'alice', 'alice-renamed@berri.ai')
    users = app.state.store.rows("SELECT * FROM users WHERE kind='google'")
    assert len(users) == 1 and users[0]['id'] == 'google:alice'
    assert users[0]['email'] == 'alice-renamed@berri.ai'


def test_second_slack_sender_keeps_own_attribution(slack_app):
    app, client, run_id = start(slack_app)
    first = app.state.store.messages(run_id)[0]
    payload = event(event_id='SecondSender', text='<@U99999999> Please continue')
    payload['event']['user'] = 'U87654321'
    payload['event']['ts'] = '1790720765.000001'
    source = app.state.store.slack_source(run_id)
    payload['event']['thread_ts'] = source['thread_ts']
    assert client.post('/hooks/slack/events', **signed(payload)).status_code == 200
    messages = app.state.store.messages(run_id)
    assert len(messages) == 2
    assert messages[1]['user_id'] == 'slack:T12345678:U87654321'
    assert messages[0]['user_id'] == first['user_id']
    assert app.state.store.run(run_id)['owner_id'] == first['user_id']


def test_key_rotation_keeps_previous_key_costs_out_of_current_report(workspace):
    app, _ = workspace
    app.state.settings.litellm_api_key = 'first-key'
    run = active(app)
    rid, _ = app.state.spend.begin(run, run['model'])
    app.state.store.execute("UPDATE model_requests SET cost='2.5' WHERE id=?", (rid,))
    assert app.state.spend.report()['total']['spend'] == '2.5'
    app.state.settings.litellm_api_key = 'replacement-key'
    assert app.state.spend.report()['total']['spend'] == '0'
    assert app.state.store.rows('SELECT id FROM model_requests')


def test_reconciliation_uses_gateway_time_without_double_count_at_midnight(workspace, monkeypatch):
    app, client = workspace
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    run = active(app)
    rid, _ = app.state.spend.begin(run, run['model'])
    app.state.store.execute("UPDATE model_requests SET created_at='2026-09-29T23:59:59+00:00',cost='0.1' WHERE id=?", (rid,))
    real = httpx.AsyncClient
    def gateway(request):
        return httpx.Response(200,json={'data':[{'request_id':rid,'api_key':digest('spend-test-key'),'startTime':'2026-09-30T00:00:00Z','spend':0.1}],'total':1})
    monkeypatch.setattr('app.spend.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(gateway),**kw))
    result = client.post('/api/admin/spend/sync?start=2026-09-30&end=2026-09-30').json()
    assert result['total']['spend'] == '0.1'
    assert client.get('/api/admin/spend?start=2026-09-29&end=2026-09-29').json()['total']['spend'] == '0'


def test_usage_reporting_does_not_persist_prompt_or_response_text(workspace, monkeypatch):
    app, client = workspace
    app.state.settings.litellm_api_key = 'spend-test-key'
    app.state.settings.litellm_api_base = 'https://gateway.example/v1'
    real = httpx.AsyncClient
    def gateway(request):
        return httpx.Response(200,json={'data':[{'api_key':digest('spend-test-key'),'request_id':'legacy','startTime':datetime.now(timezone.utc).isoformat(),'spend':0,'messages':'private-source-text','response':'private-result-text','metadata':{'unrelated_secret':'never-retain-this'}}],'total':1})
    monkeypatch.setattr('app.spend.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(gateway),**kw))
    report = client.post('/api/admin/spend/sync').json()
    assert report['gateway_pending_costs'] == 0 and report['total']['spend'] == '0'
    stored = json.dumps(app.state.store.rows('SELECT * FROM gateway_usage'))
    assert 'private-' not in stored and 'never-retain-this' not in stored
