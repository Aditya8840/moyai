"""Live opt-in title smoke test. Requires LITELLM_API_BASE and LITELLM_API_KEY.

Uses synthetic input and a separate SQLite database, never a production session.
"""
import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.db import Store
from app.session_titles import SessionTitles


async def main(directory):
    settings = Settings(_env_file=None, data_dir=Path(directory), session_title_backfill_limit=0)
    store = Store(settings.data_dir)
    async def flush():
        pass
    service = SessionTitles(store, settings, SimpleNamespace(flush=flush))
    service.start()
    if not service.running:
        raise SystemExit('Gateway not configured')
    run = store.create_run('Please make the session sidebar show readable task names instead of raw prompts', '', 'demo', [], chat_enabled=True)
    service.schedule(run['id'])
    try:
        await asyncio.wait_for(service.queue.join(), 20)
        saved = store.run(run['id'])
        print(json.dumps({'model':settings.session_title_model,'display_title':saved['display_title'],
                          'prompt_preserved':saved['prompt']==run['prompt']}))
        if not saved['display_title']:
            raise SystemExit('No valid title returned; live model verification failed')
    finally:
        await service.close()


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='moyai-title-smoke-') as directory:
        asyncio.run(main(directory))
