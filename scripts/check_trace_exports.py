"""Send labeled verification turns through Moyai's real durable tracing code.

Usage: uv run python -m scripts.check_trace_exports --send --live-model
Credentials come from Settings/environment. Uses a new temporary SQLite store,
never the application's database, and does not launch a production chat.
Without --live-model, model observations are explicitly labeled fixtures.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4

import httpx

from app.agents import AgentCoordinator
from app.config import Settings
from app.db import Store
from app.tracing import AgentTracing


async def check(settings, *, live_model=False, directory=None):
    directory = Path(directory or tempfile.mkdtemp(prefix='moyai-trace-check-'))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = settings.model_copy(update={'data_dir': directory, 'trace_environment': 'verification',
                                          'langfuse_tracing_environment': 'verification'})
    store = Store(directory)
    tracing = store.tracing = AgentTracing(store, settings)
    if not tracing.outboxes:
        raise RuntimeError('No tracing destinations are configured.')
    marker = 'moyai-comparison-' + uuid4().hex[:12]
    model = 'openai/gpt-6-astra' if live_model else 'fixture/moyai-tracing-check'
    run = store.create_run(f'{marker}: verify reading a file, recovery, delegation, and a follow-up. This is a verification run.',
                           '', 'modal', [], chat_enabled=True, model=model)
    message = store.claim_message(run['id'])
    AgentCoordinator(store, settings, None)
    store.execute('''INSERT INTO agent_groups(id,parent_id,message_id,request_key,payload,created_at,status)
                     VALUES(?,?,?,'trace-check','{}',?,'running')''',
                  (marker, run['id'], message['id'], datetime.now(timezone.utc).isoformat()))
    child = store.create_run(f'{marker}: calculate the sum of 2, 3, and 5.', '', 'modal', [], chat_enabled=True, model=model)
    store.execute('UPDATE runs SET parent_run_id=?,agent_group_id=?,agent_label=? WHERE id=?',
                  (run['id'], marker, 'comparison-worker', child['id']))
    child_message = store.claim_message(child['id'])
    sample = directory / 'numbers.json'
    sample.write_text('[2, 3, 5]')
    stamp = time.time_ns()
    numbers = json.loads(sample.read_text())
    tracing.tool(child['id'], {'tool': 'read_file', 'call_id': uuid4().hex, 'start_ns': stamp,
                              'end_ns': time.time_ns(), 'input': {'path': 'numbers.json'}, 'output': numbers})
    # A real, harmless filesystem error provides an error/recovery comparison.
    stamp = time.time_ns()
    try:
        (directory / 'intentionally-missing.txt').read_text()
    except FileNotFoundError:
        tracing.tool(child['id'], {'tool': 'read_file', 'call_id': uuid4().hex, 'start_ns': stamp,
                                  'end_ns': time.time_ns(), 'input': {'path': 'intentionally-missing.txt'},
                                  'output': 'FileNotFoundError: intentional verification error; continuing with numbers.json.',
                                  'status': 'error'})
    store.finish_message(child['id'], child_message['id'], 'Sum: ' + str(sum(numbers)))
    store.execute("UPDATE agent_groups SET status='completed' WHERE id=?", (marker,))

    async def completion(prompt):
        current = store.run(run['id'])
        messages = [{'role': 'user', 'content': prompt}]
        request_id, stamp = uuid4().hex, time.time_ns()
        if live_model:
            if not settings.litellm_api_base or not settings.litellm_api_key:
                raise RuntimeError('Live model verification needs the existing inference gateway credentials.')
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(settings.litellm_api_base.rstrip('/') + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + settings.litellm_api_key, 'x-litellm-call-id': request_id},
                    json={'model': model, 'messages': messages, 'max_tokens': 100,
                          'metadata': {'moyai_request_id': request_id}, 'stream': False})
                if response.status_code != 200:
                    raise RuntimeError(f'Model verification returned HTTP {response.status_code}.')
                data = response.json()
        else:
            data = {'model': model, 'choices': [{'message': {'role': 'assistant', 'content': '10 (fixture)'}}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 4}}
        tracing.model(current, request_id, stamp, messages, data, 'completed')
        return data['choices'][0]['message'].get('content') or ''

    started = datetime.now(timezone.utc).isoformat()
    try:
        answer = await completion(f'{marker}. A worker read numbers.json and calculated 2 + 3 + 5 = 10. Reply with only the sum.')
        store.finish_message(run['id'], message['id'], answer)
        store.enqueue_message(run['id'], f'{marker}: follow-up; repeat the previous sum.', uuid4().hex)
        followup = store.claim_message(run['id'])
        answer2 = await completion(f'{marker}. Previous answer was {answer}. Repeat only that sum.')
        store.finish_message(run['id'], followup['id'], answer2)
        # Independent tasks exercise the same concurrency used at app startup.
        outcomes = await asyncio.gather(*(box.export_once() for box in tracing.exporters()))
        receipts = {box.table: store.rows(f'''SELECT count(*) total,
                    sum(delivered_at IS NOT NULL) delivered, max(last_error) last_error FROM {box.table}''')[0]
                    for box in tracing.exporters()}
        result = {'marker': marker, 'started_at': started, 'live_model': live_model,
                  'session_id': run['id'], 'child_run_id': child['id'],
                  'traces': [dict(row) for row in store.rows('SELECT * FROM trace_contexts')],
                  'receipts': receipts, 'all_delivered': all(outcomes), 'directory': str(directory)}
        (directory / 'verification.json').write_text(json.dumps(result, indent=2))
        return result
    finally:
        await tracing.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true', help='Send labeled traces to configured external destinations')
    parser.add_argument('--live-model', action='store_true', help='Make two small inference requests instead of model fixtures')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    if not args.send:
        parser.error('--send is required to write verification traces')
    result = asyncio.run(check(Settings(), live_model=args.live_model, directory=args.output_dir))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['all_delivered'] else 1)
