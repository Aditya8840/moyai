import asyncio
import io
import json
from types import SimpleNamespace
import zipfile

import pytest

from app.config import Settings
from app.db import Store
from app.runner import RunManager


def aio(function):
    return SimpleNamespace(aio=function)


class Lines:
    def __init__(self, lines):
        self.lines = lines

    async def __aiter__(self):
        for line in self.lines:
            yield line


class FakeSandbox:
    object_id = "sb-test-only"

    def __init__(self, completed=True):
        self.terminated = False
        self.spec = None
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("result.md", "Test result")
        self.archive = buffer.getvalue()
        self.completed = completed
        self.filesystem = SimpleNamespace(write_text=aio(self.write), stat=aio(self.stat), read_bytes=aio(self.read))
        self.terminate = aio(self.terminate_sandbox)
        self.wait = aio(self.wait_sandbox)
        self.exec = aio(self.execute)

    async def write(self, text, path):
        assert path == "/tmp/task.json"
        self.spec = json.loads(text)

    async def stat(self, path):
        return SimpleNamespace(size=len(self.archive))

    async def read(self, path):
        return self.archive

    async def terminate_sandbox(self):
        self.terminated = True

    async def wait_sandbox(self, *, raise_on_termination=True):
        assert self.terminated
        assert raise_on_termination is False
        return 0

    async def execute(self, *command, timeout=None, bufsize=-1):
        assert command[0] == "/opt/hermes-env/bin/python"
        async def wait():
            return 0 if self.completed else 1
        lines = [
            "Third-party diagnostic output is not copied to the UI.\n",
            "WORKSPACE_EVENT " + json.dumps({"kind": "tool", "message": "Ran test suite"}) + "\n",
            "WORKSPACE_EVENT " + json.dumps({"kind": "final", "message": "Tests passed", "completed": self.completed}) + "\n",
        ]
        # Modal's default is arbitrary chunks; adjacent writes can arrive in
        # one chunk. Respect the requested framing in the fake as the SDK does.
        return SimpleNamespace(stdout=Lines(lines if bufsize == 1 else ["".join(lines)]),
                               stderr=Lines([]), wait=aio(wait))


@pytest.fixture
def runner(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, public_url="https://workspace.example", workspace_password="a-valid-test-password",
                        modal_token_id="test", modal_token_secret="modal-secret-only", litellm_api_key="model-secret", litellm_api_base="https://model.example/v1", agent_model="test-model")
    manager = RunManager(Store(tmp_path), settings)
    manager.image = lambda: "fake image"
    async def client():
        return "fake client"
    manager.client = client
    async def lookup(*args, **kwargs):
        return "fake app"
    monkeypatch.setattr("app.runner.modal.App.lookup", aio(lookup))
    return manager


@pytest.mark.parametrize("completed,expected_status", [(True, "completed"), (False, "failed")])
async def test_cloud_lifecycle_collects_result_and_cleans_up(runner, monkeypatch, completed, expected_status):
    sandbox = FakeSandbox(completed=completed)
    async def create(**kwargs):
        assert kwargs["timeout"] == 1800
        assert kwargs["cpu"] == 2 and kwargs["memory"] == 4096
        return sandbox
    monkeypatch.setattr("app.runner.modal.Sandbox.create", aio(create))
    run = runner.store.create_run("Run tests", "", "modal", [])
    await runner.execute(run)
    result = runner.store.run(run["id"])
    assert result["status"] == expected_status
    assert result["sandbox_id"] == sandbox.object_id
    assert result["token_hash"] == ""
    assert sandbox.terminated
    assert "model-secret" not in json.dumps(sandbox.spec)
    assert (runner.settings.data_dir / "artifacts" / f"{run['id']}.zip").exists()
    assert any(row["kind"] == "artifact" for row in runner.store.events(run["id"]))
    assert any(row["message"] == "Ran test suite" for row in runner.store.events(run["id"]))
    assert not any("could not be decoded" in row["message"] for row in runner.store.events(run["id"]))


async def test_cancel_during_provisioning_cannot_orphan_sandbox(runner, monkeypatch):
    sandbox = FakeSandbox()
    started, release = asyncio.Event(), asyncio.Event()
    async def create(**kwargs):
        started.set()
        await release.wait()
        return sandbox
    monkeypatch.setattr("app.runner.modal.Sandbox.create", aio(create))
    run = runner.store.create_run("Cancel before boot", "", "modal", [])
    job = asyncio.create_task(runner.execute(run))
    await started.wait()
    await runner.cancel(run["id"])
    assert runner.store.run(run["id"])["token_hash"] == ""
    release.set()
    await job
    assert sandbox.terminated and sandbox.spec is None
    assert runner.store.run(run["id"])["status"] == "cancelled"


async def test_shutdown_during_provisioning_cleans_up_after_creation(runner, monkeypatch):
    sandbox = FakeSandbox()
    started, release = asyncio.Event(), asyncio.Event()
    async def create(**kwargs):
        started.set()
        await release.wait()
        return sandbox
    monkeypatch.setattr("app.runner.modal.Sandbox.create", aio(create))
    run = runner.store.create_run("Shutdown during boot", "", "modal", [])
    job = asyncio.create_task(runner.execute(run))
    await started.wait()
    job.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert sandbox.terminated
    assert runner.store.run(run["id"])["status"] == "interrupted"
    assert runner.store.run(run["id"])["token_hash"] == ""


async def test_slack_stop_during_context_fetch_settles_and_can_continue(runner):
    started, release = asyncio.Event(), asyncio.Event()
    async def prepare(run_id):
        started.set()
        await release.wait()
    runner.prepare_context = prepare
    run = runner.store.create_run("Stop before context returns", "", "modal", [], chat_enabled=True)
    runner.submit(run)
    await started.wait()
    # Slack reserves cancellation in its receipt transaction before cleanup.
    runner.store.update_run(run['id'], status='stopping', token_hash='')
    runner.store.execute("UPDATE messages SET status='cancelled' WHERE run_id=? AND status='queued'", (run['id'],))
    await runner.cancel(run['id'])
    job = runner.jobs[run['id']]
    release.set()
    await job
    assert runner.store.run(run['id'])['status'] == 'cancelled'
    message, submit = runner.store.enqueue_message(run['id'], 'Continue now', 'followup')
    assert submit and message['status'] == 'queued'
