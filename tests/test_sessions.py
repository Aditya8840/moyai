import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Store
from app.main import create_app
from app.persistence import Checkpoints, restore_checkpoint
from test_runner import FakeSandbox, aio, runner
from test_workspace import wait_for


class SessionSandbox(FakeSandbox):
    def __init__(self, index):
        super().__init__()
        self.object_id = f'sb-session-{index}'
        self.index = index
        self.snapshot_filesystem = aio(self.snapshot)

    async def write(self, text, path):
        if path == '/tmp/task.json':
            await super().write(text, path)
        else:
            assert path.startswith('/opt/workspace-runner/')

    async def snapshot(self, *, timeout, ttl):
        assert ttl is None and timeout == 55
        return SimpleNamespace(object_id=f'im-session-{self.index}')


async def wait_jobs(manager):
    async with asyncio.timeout(5):
        while manager.jobs:
            await asyncio.sleep(0.01)


async def test_queued_followup_runs_once_with_saved_workspace_and_fresh_capability(runner, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    machines, images = [], []
    async def create(**kwargs):
        images.append(kwargs['image'])
        machine = SessionSandbox(len(machines) + 1)
        machines.append(machine)
        if len(machines) == 1:
            original = machine.exec.aio
            async def blocked(*args, **kwargs):
                started.set()
                await release.wait()
                return await original(*args, **kwargs)
            machine.exec = aio(blocked)
        return machine
    monkeypatch.setattr('app.runner.modal.Sandbox.create', aio(create))
    monkeypatch.setattr('app.runner.modal.Image.from_id', lambda image_id, **kwargs: image_id)
    run = runner.store.create_run('Create a file', '', 'modal', [], chat_enabled=True)
    runner.submit(run)
    runner.submit(run)
    await started.wait()
    token1 = runner.store.run(run['id'])['token_hash']
    message, created = runner.store.enqueue_message(run['id'], 'Read the same file', 'request-0002')
    runner.submit(runner.store.run(run['id']))
    release.set()
    await wait_jobs(runner)
    assert created and len(machines) == 2
    assert images == ['fake image', 'im-session-1']
    assert machines[1].spec['prompt'] == 'Read the same file'
    assert all(m.terminated for m in machines)
    row = runner.store.run(run['id'])
    assert row['status'] == 'idle' and row['snapshot_id'] == 'im-session-2'
    assert row['token_hash'] == '' and token1
    transcript = runner.store.messages(run['id'])
    assert len(transcript) == 4
    assert all(m['status'] == 'completed' for m in transcript)
    runner.store.enqueue_message(run['id'], 'Read the same file', 'request-0002')
    assert not runner.store.has_queued_messages(run['id'])


async def test_snapshot_failure_does_not_claim_saved_success_or_replay_followups(runner, monkeypatch):
    machine = SessionSandbox(1)
    async def broken_snapshot(**kwargs):
        raise RuntimeError('Snapshot storage unavailable')
    machine.snapshot_filesystem = aio(broken_snapshot)
    async def create(**kwargs):
        return machine
    monkeypatch.setattr('app.runner.modal.Sandbox.create', aio(create))
    run = runner.store.create_run('First response', '', 'modal', [], chat_enabled=True)
    runner.store.enqueue_message(run['id'], 'Queued followup', 'next-0001')
    runner.submit(run)
    await wait_jobs(runner)
    row = runner.store.run(run['id'])
    assert row['status'] == 'failed' and not row['snapshot_id'] and machine.terminated
    assert [m['status'] for m in runner.store.messages(run['id']) if m['role'] == 'user'] == ['failed', 'cancelled']


async def test_cancel_before_chat_driver_starts_cannot_restart_it(runner):
    run = runner.store.create_run('Stop before boot', '', 'demo', [], chat_enabled=True)
    await runner.cancel(run['id'])
    runner.submit(runner.store.run(run['id']))
    await wait_jobs(runner)
    assert runner.store.run(run['id'])['status'] == 'cancelled'
    assert runner.store.messages(run['id'])[0]['status'] == 'cancelled'
    assert len(runner.store.messages(run['id'])) == 1


def test_message_idempotency_is_transactional_and_conflicts_are_rejected(tmp_path):
    store = Store(tmp_path)
    run = store.create_run('Initial', '', 'demo', [], chat_enabled=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.enqueue_message(run['id'], 'Follow up', 'same-request'), range(4)))
    assert sum(created for _, created in results) == 1
    assert len({message['id'] for message, _ in results}) == 1
    with pytest.raises(ValueError, match='different text'):
        store.enqueue_message(run['id'], 'Different', 'same-request')


async def test_restart_preserves_idle_session_and_interrupts_unfinished_messages(runner):
    saved = runner.store.create_run('Saved chat', '', 'demo', [], chat_enabled=True)
    runner.submit(saved)
    await wait_jobs(runner)
    pending = runner.store.create_run('Unfinished', '', 'demo', [], chat_enabled=True)
    runner.store.claim_message(pending['id'])
    # Crash in the boundary after agent completion but before committing the reply.
    runner.store.update_run(pending['id'], status='completed')
    await runner.recover()
    assert runner.store.run(saved['id'])['status'] == 'idle'
    assert runner.store.run(pending['id'])['status'] == 'interrupted'
    assert runner.store.messages(pending['id'])[0]['status'] == 'interrupted'
    assert not runner.jobs


def test_followup_api_keeps_one_session_and_enforces_auth_csrf_and_legacy_boundary(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, public_url='http://127.0.0.1:8787', demo_step_seconds=0.01,
                        workspace_password='', modal_token_id='', modal_token_secret='', litellm_api_key='')
    app = create_app(settings)
    with TestClient(app, base_url=settings.public_url, client=('127.0.0.1', 51000)) as client:
        session = client.get('/api/session').json()
        client.headers.update({'Origin': settings.public_url, 'X-CSRF-Token': session['csrf']})
        run = client.post('/api/runs', json={'prompt':'Start a conversation','mode':'demo'}).json()
        wait_for(lambda: app.state.store.run(run['id'])['status'] == 'idle')
        endpoint = f"/api/runs/{run['id']}/messages"
        message = {'content':'Continue please','client_id':'followup-123'}
        assert client.post(endpoint, json=message, headers={'X-CSRF-Token':''}).status_code == 403
        assert client.post(endpoint, json=message).status_code == 202
        assert client.post(endpoint, json=message).json()['created'] is False
        wait_for(lambda: app.state.store.run(run['id'])['status'] == 'idle')
        result = client.get(f"/api/runs/{run['id']}").json()
        assert len(result['messages']) == 4 and result['chat_enabled']
        assert len(client.get('/api/runs').json()) == 1
        old = app.state.store.create_run('Legacy task','', 'demo', [])
        assert client.post(f"/api/runs/{old['id']}/messages",json=message).status_code == 409
        client.cookies.clear()
        assert client.post(endpoint,json=message).status_code == 401


async def test_checkpoints_replace_latest_session_archive_and_restore_chat(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path/'data', checkpoint_dir=tmp_path/'checkpoints')
    store = Store(settings.data_dir)
    run = store.create_run('Persist chat','', 'demo', [], chat_enabled=True)
    directory = settings.data_dir/'artifacts';directory.mkdir()
    archive = directory/f"{run['id']}.zip";archive.write_bytes(b'first turn')
    async def commit(): pass
    checkpoints = Checkpoints(store, settings, commit=commit)
    await checkpoints.flush()
    archive.write_bytes(b'newest files from second turn')
    store.enqueue_message(run['id'],'Second turn','second-turn')
    await checkpoints.flush()
    settings.data_dir=tmp_path/'restored'
    restore_checkpoint(settings)
    restored=Store(settings.data_dir)
    assert len(restored.messages(run['id'])) == 2
    assert (settings.data_dir/'artifacts'/archive.name).read_bytes() == b'newest files from second turn'
