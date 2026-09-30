"""Real Temporal dev-server tests; the Modal boundary is a deterministic fake."""
import asyncio
import json

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from app.db import Store
from app.session_workflow import SessionWorkflow
from app.temporal_runtime import TemporalRunManager
from test_durable import durable  # noqa: F401 -- shared fixture


async def eventually(predicate, seconds=60):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.1)


async def test_real_temporal_restarts_worker_and_drains_offline_followup(durable):
    manager, cloud, run_id = durable
    cloud.finished = False
    async with await WorkflowEnvironment.start_local(dev_server_log_level='error') as env:
        async def connect():
            return env.client
        manager.connect_temporal = connect
        await manager.recover()
        successor = None
        try:
            await eventually(lambda: manager.state(run_id).get('phase') == 'monitor', seconds=20)
            token_hash = manager.store.run(run_id)['token_hash']
            await manager.shutdown()
            assert cloud.machines[0].alive
            assert manager.store.run(run_id)['token_hash'] == token_hash
            manager.store.enqueue_message(run_id, 'A follow-up sent while offline', 'offline-followup')
            manager.submit(manager.store.run(run_id))
            cloud.finished = True
            successor = cloud.attach(TemporalRunManager(Store(manager.settings.data_dir), manager.settings))
            successor.connect_temporal = connect
            await successor.recover()
            await eventually(lambda: len([m for m in successor.store.messages(run_id) if m['role'] == 'assistant']) == 2)
            assert len(cloud.machines) == len(cloud.launches) == len(cloud.terminations) == 2
            assert successor.store.run(run_id)['status'] == 'idle'
            handle = env.client.get_workflow_handle('moyai-session-' + run_id)
            history = await handle.fetch_history()
            # Replaying a real execution history validates workflow determinism.
            await Replayer(workflows=[SessionWorkflow]).replay_workflow(history)
            history_text = history.to_json()
            assert 'A follow-up sent while offline' not in history_text
            assert manager.settings.session_secret not in history_text
            await handle.terminate('Integration test complete')
        finally:
            if successor:
                await successor.shutdown()
            await manager.shutdown()
