import asyncio
import base64
import json
from pathlib import Path
import sys
import threading
from unittest.mock import AsyncMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from app.config import Settings
from app.db import Store
from app.runner import RunManager
from app.sandboxes.substrate import SubstrateProvider, Sandbox
from app.sandboxes.proto import ateapi_pb2 as pb
from sandbox import substrate_guest as guest
from tests.test_workspace import workspace


def key():
    value = Ed25519PrivateKey.generate()
    return value, base64.b64encode(value.private_bytes_raw()).decode()


def test_readiness_only_requires_selected_provider(tmp_path):
    _, signing = key()
    settings = Settings(_env_file=None, data_dir=tmp_path, sandbox_provider='substrate',
                        substrate_api_url='https://api.example.com', substrate_router_url='https://router.example.com',
                        substrate_api_token='api-token', substrate_signing_key=signing,
                        litellm_api_base='https://llm.example.com', litellm_api_key='llm-key', agent_model='test')
    assert settings.missing_cloud() == []
    assert settings.missing_sandbox('modal') == ['MODAL_TOKEN_ID', 'MODAL_TOKEN_SECRET']


def test_actor_template_requires_digest_and_projects_actor_identity():
    from scripts.substrate_template import template
    args = {'image': 'registry/moyai:latest', 'storage': 'gs://bucket/moyai', 'public_key': 'test'}
    with pytest.raises(ValueError, match='requires an image digest'):
        template(**args)
    args['image'] = 'registry/moyai@sha256:' + 'a' * 64
    result = template(**args)
    assert result['containers'][0]['image'] == args['image']
    assert result['volumes'][0]['systemInfo']['dataSources'][0]['actorMetadata']['items'][0]['field'] == 'ACTOR_METADATA_FIELD_UID'


@pytest.mark.parametrize('url', ['http://untrusted.example', 'https://user:secret@example.com', 'https://example.com/path', 'https://example.com?token=x'])
def test_connection_endpoint_validation(url):
    with pytest.raises(ValueError):
        Settings(_env_file=None, substrate_api_url=url)


def test_default_switch_preserves_existing_sessions(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = Store(tmp_path)
    manager = RunManager(store, settings)
    old = store.create_run('An old session', '', 'modal', [])
    settings.sandbox_provider = 'substrate'
    new = store.create_run('A new session', '', 'modal', [])
    assert manager.provider(old).name == 'modal'
    assert manager.provider(new).name == 'substrate'
    assert Store(tmp_path).run(old['id'])['sandbox_provider'] == 'modal'


def connection():
    return {'provider': 'substrate', 'revision': 0, 'values': {
        'substrate_api_url': 'https://api.example.com', 'substrate_router_url': 'https://router.example.com',
        'substrate_api_token': 'private-substrate-token', 'substrate_atespace': 'moyai', 'substrate_template': 'moyai'}}


def test_connect_checks_before_publishing_and_encrypts_settings(workspace, monkeypatch):
    app, client = workspace
    check = AsyncMock(return_value='Live sandbox passed')
    monkeypatch.setattr(SubstrateProvider, 'check', check)
    response = client.put('/api/settings/sandboxes', json=connection())
    assert response.status_code == 200, response.text
    assert response.json()['provider'] == 'substrate'
    assert check.call_count == 1
    assert 'private-substrate-token' not in response.text
    assert 'private-substrate-token' not in app.state.store.rows('SELECT encrypted FROM sandbox_settings')[0]['encrypted']
    assert app.state.settings.substrate_api_token == 'private-substrate-token'
    assert client.put('/api/settings/sandboxes', json=connection()).status_code == 409


def test_failed_connect_does_not_change_saved_default(workspace, monkeypatch):
    app, client = workspace
    monkeypatch.setattr(SubstrateProvider, 'check', AsyncMock(side_effect=RuntimeError('secret-error-token')))
    result = client.put('/api/settings/sandboxes', json=connection())
    assert result.status_code == 502
    assert 'secret-error-token' not in result.text
    assert app.state.settings.sandbox_provider == 'modal'


def test_connections_require_admin_and_csrf(workspace):
    app, client = workspace
    client.headers.pop('X-CSRF-Token')
    assert client.put('/api/settings/sandboxes', json=connection()).status_code == 403
    assert 'substrate_api_token' not in app.state.sandbox_settings.view(False)['providers']['substrate']


def test_saved_connection_survives_restart(workspace, monkeypatch):
    app, client = workspace
    monkeypatch.setattr(SubstrateProvider, 'check', AsyncMock(return_value='Connected'))
    assert client.put('/api/settings/sandboxes', json=connection()).status_code == 200
    from app.sandbox_settings import SandboxSettings
    from app.security import Security
    settings = Settings(_env_file=None, data_dir=app.state.settings.data_dir)
    security = Security(settings)
    SandboxSettings(app.state.store, settings, security)
    assert settings.sandbox_provider == 'substrate'
    assert settings.substrate_api_token == 'private-substrate-token'
    assert settings.substrate_signing_key == app.state.settings.substrate_signing_key


@pytest.fixture
def transport(tmp_path, monkeypatch):
    private, signing = key()
    identity = tmp_path / 'identity'
    identity.write_text('actor-one')
    monkeypatch.setattr(guest, 'IDENTITY', identity)
    monkeypatch.setattr(guest, 'ROOT', tmp_path / 'runtime')
    monkeypatch.setattr(guest, 'NONCES', {})
    monkeypatch.setenv('MOYAI_SUBSTRATE_PUBLIC_KEY', base64.b64encode(private.public_key().public_bytes_raw()).decode())
    server = guest.ThreadingHTTPServer(('127.0.0.1', 0), guest.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(_env_file=None, substrate_signing_key=signing,
                        substrate_router_url=f'http://127.0.0.1:{server.server_port}')
    actor = pb.Actor(metadata={'atespace': 'tests', 'name': 'one', 'uid': 'actor-one'})
    yield Sandbox(SubstrateProvider(settings), actor), tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


async def test_real_http_signed_file_transfer_and_commands(transport):
    sandbox, tmp = transport
    target = str(tmp / 'test.txt')
    await sandbox.filesystem.write_text.aio('unicode ✓', target)
    assert (await sandbox.filesystem.stat.aio(target)).size == len('unicode ✓'.encode())
    assert (await sandbox.filesystem.read_bytes.aio(target)).decode() == 'unicode ✓'
    process = await sandbox.exec.aio(sys.executable, '-c', 'import sys; print("out ✓"); print("err ✓", file=sys.stderr)', timeout=10)
    out, err = await asyncio.gather(process.stdout.read.aio(), process.stderr.read.aio())
    assert out == 'out ✓\n' and err == 'err ✓\n'
    assert await process.wait.aio() == 0


async def test_real_http_streaming_drains_multiple_chunks_and_nonzero_exit(transport):
    sandbox, _ = transport
    process = await sandbox.exec.aio(sys.executable, '-c', 'import sys; print("x"*700000); print("y"*700000,file=sys.stderr); sys.exit(7)', timeout=10)
    out, err = await asyncio.gather(process.stdout.read.aio(), process.stderr.read.aio())
    assert out == 'x'*700000+'\n' and err == 'y'*700000+'\n'
    assert await process.wait.aio() == 7


async def test_chunked_unicode_writes_and_empty_overwrite(transport):
    sandbox, tmp = transport
    path = str(tmp / 'large.txt')
    value = '✓' * 800000
    await sandbox.filesystem.write_text.aio(value, path)
    assert (await sandbox.filesystem.read_bytes.aio(path)).decode() == value
    await sandbox.filesystem.write_text.aio('', path)
    assert await sandbox.filesystem.read_bytes.aio(path) == b''


async def test_checkpoint_blocks_other_handle_requests_until_thawed():
    actor = pb.Actor(metadata={'atespace': 'tests', 'name': 'lock-test', 'uid': 'actor-lock-test'})
    backend = SubstrateProvider(Settings(_env_file=None))
    entered, release, read = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def rpc(method, data, **kwargs):
        if method == 'CreateTag':
            entered.set()
            await release.wait()
    backend.rpc = AsyncMock(side_effect=rpc)
    original, reconnected = Sandbox(backend, actor), Sandbox(backend, actor)
    original._request = AsyncMock(return_value={})
    async def read_request(*args):
        read.set()
    reconnected._request = read_request
    checkpoint = asyncio.create_task(original.snapshot_filesystem.aio())
    await entered.wait()
    reader = asyncio.create_task(reconnected.request('/read', {}))
    await asyncio.sleep(0)
    assert not read.is_set()
    release.set()
    await asyncio.gather(checkpoint, reader)
    assert read.is_set()


async def test_actor_identity_and_signing_key_are_enforced(transport):
    sandbox, _ = transport
    sandbox.actor.metadata.uid = 'other-actor'
    with pytest.raises(RuntimeError, match='401'):
        await sandbox.request('/activate', {})
    sandbox.actor.metadata.uid = 'actor-one'
    _, sandbox.provider.settings.substrate_signing_key = key()
    with pytest.raises(RuntimeError, match='401'):
        await sandbox.request('/activate', {})


def test_clone_activation_kills_frozen_processes_and_erases_parent_capabilities(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    (root / 'jobs').mkdir(parents=True)
    (root / 'jobs' / 'secret').write_text('parent token')
    identity = tmp_path / 'identity'
    identity.write_text('child')
    guest.atomic(root / 'frozen.json', {'uid': 'parent', 'processes': {'42': 'start'}})
    monkeypatch.setattr(guest, 'ROOT', root)
    monkeypatch.setattr(guest, 'IDENTITY', identity)
    monkeypatch.setattr(guest, 'process_identity', lambda pid: ('start', 'T'))
    calls = []
    monkeypatch.setattr(guest.os, 'kill', lambda pid, sig: calls.append((pid, sig)))
    guest.activate()
    assert calls == [(42, guest.signal.SIGKILL)]
    assert not (root / 'jobs').exists()
    assert not (root / 'frozen.json').exists()


def test_original_activation_resumes_processes_and_ignores_reused_pids(tmp_path, monkeypatch):
    identity = tmp_path / 'identity'
    identity.write_text('parent')
    guest.atomic(tmp_path / 'frozen.json', {'uid': 'parent', 'processes': {'42': 'start', '43': 'old'}})
    monkeypatch.setattr(guest, 'ROOT', tmp_path)
    monkeypatch.setattr(guest, 'IDENTITY', identity)
    monkeypatch.setattr(guest, 'process_identity', lambda pid: ('start', 'T'))
    calls = []
    monkeypatch.setattr(guest.os, 'kill', lambda pid, sig: calls.append((pid, sig)))
    guest.activate()
    assert calls == [(42, guest.signal.SIGCONT)]


async def test_checkpoint_always_thaws_original_after_failed_tag():
    actor = pb.Actor(metadata={'atespace': 'tests', 'name': 'one', 'uid': 'actor-one'})
    backend = SubstrateProvider(Settings(_env_file=None))
    async def rpc(method, data, **kwargs):
        if method == 'CreateTag':
            raise RuntimeError('tag failure')
    backend.rpc = AsyncMock(side_effect=rpc)
    sandbox = Sandbox(backend, actor)
    sandbox._request = AsyncMock(return_value={})
    with pytest.raises(RuntimeError, match='tag failure'):
        await sandbox.snapshot_filesystem.aio()
    assert [call.args[0] for call in backend.rpc.call_args_list] == ['SuspendActor', 'CreateTag', 'ResumeActor']
    assert [call.args[0] for call in sandbox._request.call_args_list] == ['/freeze', '/activate']


async def test_retry_initializes_partial_actor_without_deleting_it_on_failure():
    from modal.exception import AlreadyExistsError
    actor = pb.Actor(metadata={'atespace': 'tests', 'name': 'one', 'uid': 'actor-one'})
    backend = SubstrateProvider(Settings(_env_file=None))
    async def rpc(method, data, **kwargs):
        if method == 'CreateActor':
            raise AlreadyExistsError('exists')
        return actor
    backend.rpc = AsyncMock(side_effect=rpc)
    backend.initialize = AsyncMock(side_effect=RuntimeError('temporary router outage'))
    with pytest.raises(RuntimeError):
        await backend.create(name='one')
    assert [call.args[0] for call in backend.rpc.call_args_list] == ['CreateActor', 'GetActor']
    backend.initialize.reset_mock(side_effect=True)
    sandbox = await backend.find('one', initialize=True, token='retry-token')
    assert sandbox.env['WORKSPACE_RUN_TOKEN'] == 'retry-token'
    backend.initialize.assert_awaited_once()
    backend.initialize.reset_mock()
    await backend.find('one')
    backend.initialize.assert_not_awaited()


def test_repeated_activation_does_not_extend_original_lease(transport):
    sandbox, _ = transport
    guest.dispatch('/activate', {'expires_at': 1234})
    guest.dispatch('/activate', {'expires_at': 5678})
    assert json.loads((guest.ROOT / 'lease.json').read_text())['expires_at'] == 1234


def test_initial_signing_key_does_not_pin_environment_defaults(workspace):
    app, _ = workspace
    from app.sandbox_settings import SandboxSettings
    settings = Settings(_env_file=None, data_dir=app.state.settings.data_dir, modal_token_id='new-env-token')
    SandboxSettings(app.state.store, settings, app.state.security)
    assert settings.modal_token_id == 'new-env-token'


async def test_control_tls_negotiates_http2_and_verifies_private_ca(tmp_path):
    import datetime
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from grpclib.exceptions import StreamTerminatedError
    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(private.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(minutes=10))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(private, hashes.SHA256()))
    certificate = cert.public_bytes(serialization.Encoding.PEM).decode()
    (tmp_path / 'cert.pem').write_text(certificate)
    (tmp_path / 'key.pem').write_bytes(private.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / 'cert.pem', tmp_path / 'key.pem')
    context.set_alpn_protocols(['h2'])
    negotiated = asyncio.get_running_loop().create_future()
    async def connected(reader, writer):
        negotiated.set_result(writer.get_extra_info('ssl_object').selected_alpn_protocol())
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(connected, '127.0.0.1', 0, ssl=context)
    async with server:
        settings = Settings(_env_file=None, substrate_api_url=f'https://localhost:{server.sockets[0].getsockname()[1]}',
                            substrate_ca_cert=certificate)
        with pytest.raises(StreamTerminatedError):
            await SubstrateProvider(settings).rpc('GetActorTemplate', {'actor_template': {'atespace':'tests','name':'one'}}, timeout=5)
        assert await negotiated == 'h2'
