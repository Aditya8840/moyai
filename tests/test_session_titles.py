"""Real Agents SDK requests with a mocked HTTP boundary, not fake SDK results."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Store
from app.main import create_app, public_run
from app.session_titles import SessionTitles, clean_title
from test_slack import slack_app, event, signed


def config(tmp_path, **extra):
    return Settings(_env_file=None, data_dir=tmp_path, litellm_api_base='https://gateway.example/v1',
                    litellm_api_key='test-key', session_title_backfill_limit=0, **extra)


def completion(text='Improve session navigation'):
    return httpx.Response(200, json={'id':'chatcmpl-test', 'object':'chat.completion', 'created':1,
        'model':'fireworks_ai/deepseek-v4-pro', 'choices':[{'index':0,'finish_reason':'stop',
        'message':{'role':'assistant','content':text}}],
        'usage':{'prompt_tokens':20,'completion_tokens':5,'total_tokens':25}})


def gateway(monkeypatch, handler):
    clients=[]
    def factory(**kwargs):
        assert kwargs['max_retries']==0
        client=AsyncOpenAI(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr('app.session_titles.AsyncOpenAI',factory)
    return clients


async def drain(service):
    await asyncio.wait_for(service.queue.join(),3)


async def test_backfill_labels_shutdown_and_migration(tmp_path,monkeypatch):
    import sqlite3
    store=Store(tmp_path)
    runs=[store.create_run('Task '+str(i),'','demo',[],chat_enabled=True) for i in range(4)]
    store.execute('UPDATE runs SET agent_label=? WHERE id=?',('Explicit label',runs[-1]['id']))
    with sqlite3.connect(store.path) as conn:
        conn.execute('DROP INDEX idx_runs_title_backfill')
        conn.execute('ALTER TABLE runs DROP COLUMN display_title')
        conn.execute('ALTER TABLE runs DROP COLUMN title_attempted_at')
    store=Store(tmp_path)
    requests=[]
    clients=gateway(monkeypatch,lambda request:requests.append(request) or completion())
    settings=config(tmp_path);settings.session_title_backfill_limit=1
    service=SessionTitles(store,settings,SimpleNamespace(flush=AsyncMock()));service.start()
    await drain(service)
    assert len(requests)==1
    assert store.run(runs[-2]['id'])['display_title']=='Improve session navigation'
    assert not store.run(runs[0]['id'])['title_attempted_at']
    assert store.run(runs[-1]['id'])['agent_label']=='Explicit label'
    await service.close()
    assert clients[0].is_closed() and not service.workers


async def test_disabled_and_cancelled_work(tmp_path,monkeypatch):
    store=Store(tmp_path);run=store.create_run('Task name','','demo',[],chat_enabled=True)
    entered,cancelled=asyncio.Event(),asyncio.Event()
    async def handler(request):
        entered.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    clients=gateway(monkeypatch,handler)
    settings=config(tmp_path,session_titles_enabled=False)
    service=SessionTitles(store,settings,SimpleNamespace(flush=AsyncMock()));service.start();service.schedule(run['id'])
    assert not clients and service.queue.empty()
    settings.session_titles_enabled=True;service.start();service.schedule(run['id'])
    await asyncio.wait_for(entered.wait(),2)
    await service.close()
    assert cancelled.is_set() and clients[0].is_closed() and not service.pending
    assert not store.run(run['id'])['display_title']


async def test_real_sdk_persistence_and_original_content(tmp_path,monkeypatch):
    requests=[]
    clients=gateway(monkeypatch,lambda request: requests.append(request) or completion())
    store=Store(tmp_path)
    run=store.create_run('Improve sidebar '+ 'x'*5000,'','demo',[],chat_enabled=True)
    before=store.messages(run['id'])
    service=SessionTitles(store,config(tmp_path),SimpleNamespace(flush=AsyncMock()))
    service.start()
    service.schedule(run['id']);service.schedule(run['id'])
    await drain(service)
    body=json.loads(requests[0].content)
    assert str(requests[0].url)=='https://gateway.example/v1/chat/completions'
    assert body['model']=='openai/gpt-4.1-nano'
    assert body['stream'] is False and body['max_tokens']==96
    assert not body.get('tools') and 'tool_choice' not in body and 'parallel_tool_calls' not in body
    assert len(body['messages'][1]['content'])==4000
    saved=Store(tmp_path).run(run['id'])
    assert saved['display_title']=='Improve session navigation'
    for key in ('prompt','status','updated_at','agent_label','summary'):
        assert saved[key]==run[key]
    assert store.messages(run['id'])==before
    assert 'title_attempted_at' not in public_run(saved)
    service.schedule(run['id']);await drain(service)
    assert len(requests)==1
    await service.close()
    assert clients[0].is_closed()


@pytest.mark.parametrize('failure',['500','429','timeout','empty','multiline'])
async def test_failure_keeps_fallback_and_does_not_retry(tmp_path,monkeypatch,failure):
    requests=[]
    async def handler(request):
        requests.append(request)
        if failure in ('500','429'):return httpx.Response(int(failure),json={'error':{'message':'test error'}})
        if failure=='timeout':await asyncio.Event().wait()
        return completion('' if failure=='empty' else 'A title\nMore prose')
    gateway(monkeypatch,handler)
    store=Store(tmp_path);run=store.create_run('Fix sidebar','','demo',[],chat_enabled=True)
    settings=config(tmp_path,session_title_timeout_seconds=0.1)
    for _ in range(2):
        service=SessionTitles(store,settings,SimpleNamespace(flush=AsyncMock()));service.start()
        service.schedule(run['id']);await drain(service);await service.close()
    assert len(requests)==1
    assert store.run(run['id'])['display_title']==''


@pytest.mark.parametrize('value',['',None,'x'*81,'Title: A task','**Task**','Task\nMore','<script>bad</script>','https://example.com','Task\u202e'])
def test_invalid_titles(value):
    assert clean_title(value)==''


def test_web_lifecycle_nonblocking_refresh(tmp_path,monkeypatch):
    entered,release=asyncio.Event(),asyncio.Event()
    async def handler(request):
        entered.set();await release.wait();return completion()
    clients=gateway(monkeypatch,handler)
    app=create_app(config(tmp_path,auto_prepare_repositories=False))
    monkeypatch.setattr(app.state.manager,'submit',lambda run:None)
    with TestClient(app,base_url=app.state.settings.public_url,client=('127.0.0.1',50000)) as client:
        session=client.get('/api/session').json()
        client.headers.update({'Origin':app.state.settings.public_url,'X-CSRF-Token':session['csrf']})
        response=client.post('/api/runs',json={'prompt':'Fix the sidebar names','client_id':'titles-test'})
        assert response.status_code==201
        run=response.json();assert run['display_title']==''
        client.portal.call(asyncio.wait_for,entered.wait(),2)
        assert client.get('/api/runs').json()[0]['display_title']==''
        client.portal.call(release.set);client.portal.call(drain,app.state.session_titles)
        assert client.get('/api/runs/'+run['id']).json()['display_title']=='Improve session navigation'
    assert clients[0].is_closed()


@pytest.mark.parametrize('thread_chat',[True,False])
def test_slack_lifecycle(slack_app,monkeypatch,thread_chat):
    app,client,runs,_=slack_app
    requests=[]
    gateway(monkeypatch,lambda request:requests.append(request) or completion())
    app.state.settings.session_titles_enabled=True
    app.state.settings.slack_thread_chat_enabled=thread_chat
    client.portal.call(app.state.session_titles.start)
    payload=event(text='<@U99999999> Fix sidebar task names')
    assert client.post('/hooks/slack/events',**signed(payload)).status_code==200
    client.portal.call(drain,app.state.session_titles)
    assert len(runs)==len(requests)==1
    assert app.state.store.run(runs[0]['id'])['display_title']=='Improve session navigation'
    client.post('/hooks/slack/events',**signed(payload))
    client.portal.call(drain,app.state.session_titles)
    assert len(requests)==1
