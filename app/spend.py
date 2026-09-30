"""Durable, server-attributed gateway usage; monetary values remain decimal strings."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .db import now
from .security import digest


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def period(start=None, end=None):
    today = datetime.now(timezone.utc).date()
    start, end = start or today.replace(day=1), end or today
    if start > end or (end - start).days > 92:
        raise HTTPException(422, 'Choose a date range of up to 93 days, with start before end.')
    return start, end, start.isoformat() + 'T00:00:00+00:00', (end + timedelta(days=1)).isoformat() + 'T00:00:00+00:00'


class UsageCapture:
    """Observe only usage/cost fields; never persist prompts or completions here."""
    def __init__(self, streaming):
        self.streaming = streaming
        self.buffer = b''
        self.usage = {}
        self.cost = None
        self.response_id = ''
        self.done = False

    def consume(self, value):
        if not isinstance(value, dict):
            return
        if isinstance(value.get('id'), str):
            self.response_id = value['id'][:200]
        if isinstance(value.get('usage'), dict):
            self.usage = value['usage']
        # LiteLLM's optional final streaming cost extension. Token prices are
        # never guessed when the gateway does not expose a final cost.
        for source in (value, value.get('usage', {})):
            if isinstance(source, dict):
                cost = money(source.get('x_litellm_response_cost'))
                if cost is not None:
                    self.cost = cost

    def feed(self, chunk):
        self.buffer += chunk
        if self.streaming:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                self.line(line)
        if len(self.buffer) > 8 * 1024 * 1024:
            self.buffer = b''

    def line(self, line):
        if not line.startswith(b'data:'):
            return
        data = line[5:].strip()
        if data == b'[DONE]':
            self.done = True
            return
        try:
            self.consume(json.loads(data))
        except (ValueError, UnicodeDecodeError):
            pass

    def finish(self):
        if self.streaming:
            self.line(self.buffer)
        else:
            try:
                value = json.loads(self.buffer)
                self.consume(value)
                self.done = isinstance(value, dict) and 'error' not in value
            except (ValueError, UnicodeDecodeError):
                pass
        self.buffer = b''


class IdentityLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slack_user_id: str = Field(max_length=150)
    google_user_id: str = Field(max_length=150)


class Spend:
    def __init__(self, store, settings, security, checkpoints):
        self.store, self.settings, self.security, self.checkpoints = store, settings, security, checkpoints
        self.lock = asyncio.Lock()
        with store.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS model_requests (
                    id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, gateway_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL REFERENCES runs(id), message_id INTEGER,
                    user_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL, created_at TEXT NOT NULL,
                    finished_at TEXT, status TEXT NOT NULL DEFAULT 'pending', cost TEXT,
                    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_model_requests_gateway ON model_requests(key_hash,gateway_id);
                CREATE INDEX IF NOT EXISTS idx_model_requests_time ON model_requests(key_hash,created_at);
                CREATE TABLE IF NOT EXISTS gateway_usage (
                    key_hash TEXT NOT NULL, request_id TEXT NOT NULL, local_request_id TEXT,
                    created_at TEXT NOT NULL, model TEXT NOT NULL, cost TEXT,
                    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                    PRIMARY KEY(key_hash,request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_time ON gateway_usage(key_hash,created_at);
                CREATE TABLE IF NOT EXISTS spend_sync (
                    key_hash TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                    checked_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(key_hash,start_date,end_date)
                );
            ''')

    @property
    def key_hash(self):
        return digest(self.settings.litellm_api_key) if self.settings.litellm_api_key else ''

    def begin(self, run, model):
        request_id = str(uuid4())
        user_id = run['active_user_id'] if run['chat_enabled'] else run['owner_id']
        self.store.execute('INSERT INTO model_requests(id,key_hash,run_id,message_id,user_id,model,created_at) VALUES(?,?,?,?,?,?,?)',
                           (request_id, self.key_hash, run['id'], run['active_message_id'], user_id, model, now()))
        return request_id, 'moyai:' + (user_id or 'unattributed')

    def headers(self, request_id, upstream, streaming):
        # Streaming headers arrive before generation ends and may report zero.
        # Only the final stream extension or reconciled logs can price a stream.
        cost = None if streaming else money(upstream.headers.get('x-litellm-response-cost'))
        gateway_id = upstream.headers.get('x-litellm-call-id', '')[:200]
        self.store.execute('UPDATE model_requests SET gateway_id=?,cost=? WHERE id=?', (gateway_id, cost, request_id))

    def finish(self, request_id, capture, status):
        usage = capture.usage if capture else {}
        def tokens(field):
            value = usage.get(field)
            return value if type(value) is int and value >= 0 else None
        self.store.execute('UPDATE model_requests SET status=?,finished_at=?,cost=COALESCE(?,cost),prompt_tokens=?,completion_tokens=?,total_tokens=? WHERE id=?',
                           (status, now(), capture.cost if capture else None, tokens('prompt_tokens'), tokens('completion_tokens'), tokens('total_tokens'), request_id))

    def sync_state(self, key, start, end, status, detail=''):
        self.store.execute('INSERT INTO spend_sync VALUES(?,?,?,?,?,?) ON CONFLICT(key_hash,start_date,end_date) DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status,detail=excluded.detail',
                           (key, str(start), str(end), now(), status, detail))

    async def sync(self, start=None, end=None):
        start, end, lower, upper = period(start, end)
        if not self.key_hash or not self.settings.litellm_api_base:
            return {'status': 'unavailable', 'detail': 'The model gateway is not configured.'}
        async with self.lock:
            key = self.key_hash
            base = self.settings.litellm_api_base.rstrip('/').removesuffix('/v1')
            records = []
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    for page in range(1, 101):
                        response = await client.get(base + '/spend/logs/v2', headers={'Authorization': 'Bearer ' + self.settings.litellm_api_key}, params={
                            'api_key': key, 'start_date': lower, 'end_date': upper, 'page': page, 'page_size': 1000,
                            'sort_by': 'startTime', 'sort_order': 'asc'})
                        if response.status_code in {401, 403}:
                            raise ValueError('The gateway key needs read-only access to /spend/logs/v2 to reconcile costs. Model calls still work.')
                        response.raise_for_status()
                        result = response.json()
                        rows = result.get('data')
                        if not isinstance(rows, list) or not isinstance(result.get('total'), int):
                            raise ValueError('The gateway returned an unsupported spend report. Costs have not been reconciled.')
                        records.extend(rows)
                        if len(records) >= result['total']:
                            break
                        if not rows:
                            raise ValueError('The gateway spend report was incomplete. Try syncing again.')
                    else:
                        raise ValueError('This range has too many requests. Choose a shorter date range.')
                normalized = []
                for row in records:
                    # A broader reporting response must never import another key.
                    if row.get('api_key') != key:
                        raise ValueError('The gateway returned a different key. No report was imported.')
                    request_id = row.get('request_id')
                    if not isinstance(request_id, str) or not request_id:
                        raise ValueError('The gateway report is missing request IDs.')
                    created = stamp(row.get('startTime'))
                    if not lower <= created < upper:
                        continue
                    metadata = row.get('metadata', {})
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except ValueError:
                            metadata = {}
                    metadata = metadata if isinstance(metadata, dict) else {}
                    correlation = metadata.get('moyai_request_id', '')
                    if not isinstance(correlation, str):
                        correlation = ''
                    local = self.store.rows('SELECT id FROM model_requests WHERE key_hash=? AND (id=? OR gateway_id=? OR id=?) LIMIT 1', (key, request_id, request_id, correlation))
                    def token(field):
                        value = row.get(field)
                        return value if type(value) is int and value >= 0 else None
                    normalized.append((key, request_id, local[0]['id'] if local else None, created, str(row.get('model') or ''), money(row.get('spend')),
                                       token('prompt_tokens'), token('completion_tokens'), token('total_tokens')))
                # All pages validate before committing; retries replace the same
                # gateway request, never add its charge twice.
                with self.store.connect() as conn:
                    conn.executemany('INSERT INTO gateway_usage VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(key_hash,request_id) DO UPDATE SET local_request_id=excluded.local_request_id,created_at=excluded.created_at,model=excluded.model,cost=excluded.cost,prompt_tokens=excluded.prompt_tokens,completion_tokens=excluded.completion_tokens,total_tokens=excluded.total_tokens', normalized)
                self.sync_state(key, start, end, 'synced')
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                detail = str(exc) if isinstance(exc, ValueError) else 'The gateway spend report is temporarily unavailable. Previously recorded costs are preserved.'
                self.sync_state(key, start, end, 'unavailable', detail)
            await self.checkpoints.flush()
            return self.store.rows('SELECT checked_at,status,detail FROM spend_sync WHERE key_hash=? AND start_date=? AND end_date=?', (key, str(start), str(end)))[0]

    def report(self, start=None, end=None):
        start, end, lower, upper = period(start, end)
        users = {u['id']: u for u in self.store.rows('SELECT id,kind,email,name,linked_user_id FROM users')}
        requests = {r['id']: r for r in self.store.rows('SELECT * FROM model_requests WHERE key_hash=?', (self.key_hash,))}
        gateway = self.store.rows('SELECT * FROM gateway_usage WHERE key_hash=? AND created_at>=? AND created_at<?', (self.key_hash, lower, upper))
        # Match against all imported logs to avoid a double count at a UTC date
        # boundary where the gateway and broker timestamps straddle midnight.
        imported = {r['local_request_id'] for r in self.store.rows('SELECT local_request_id FROM gateway_usage WHERE key_hash=? AND local_request_id IS NOT NULL', (self.key_hash,))}
        rows = []
        for item in gateway:
            local = requests.get(item['local_request_id'], {})
            rows.append({**item, 'user_id': local.get('user_id', ''), 'run_id': local.get('run_id', ''), 'reconciled': True})
        for item in requests.values():
            if item['id'] not in imported and lower <= item['created_at'] < upper:
                rows.append({**item, 'reconciled': False})
        def empty():
            return {'spend': Decimal(0), 'requests': 0, 'pending_costs': 0, 'unreconciled_requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'sessions': set()}
        groups = {user['id']: empty() for user in users.values() if user['kind'] == 'google'}
        sessions, models = {}, {}
        def add(bucket, row):
            bucket['requests'] += 1
            bucket['pending_costs'] += row['cost'] is None
            bucket['unreconciled_requests'] += not row['reconciled']
            bucket['spend'] += Decimal(row['cost'] or '0')
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                bucket[key] += row[key] or 0
            if row['run_id']:
                bucket['sessions'].add(row['run_id'])
        total = empty()
        for row in rows:
            actor = users.get(row['user_id'], {})
            user_id = actor.get('linked_user_id') or actor.get('id') or 'unattributed'
            row['user_id'] = user_id
            add(total, row)
            add(groups.setdefault(user_id, empty()), row)
            add(models.setdefault(row['model'], empty()), row)
            if row['run_id']:
                add(sessions.setdefault((user_id, row['run_id']), empty()), row)
        def clean(bucket):
            return {**bucket, 'spend': str(bucket['spend']), 'sessions': len(bucket['sessions'])}
        def identity(user_id):
            return users.get(user_id, {'id': 'unattributed', 'name': 'Unattributed / earlier usage', 'email': '', 'kind': 'unattributed'})
        titles = {r['id']: r['prompt'].split('\n')[0][:150] for r in self.store.rows('SELECT id,prompt FROM runs')}
        syncs = self.store.rows('SELECT checked_at,status,detail FROM spend_sync WHERE key_hash=? AND start_date=? AND end_date=?', (self.key_hash, str(start), str(end)))
        total_gateway = sum((Decimal(r['cost'] or '0') for r in gateway), Decimal(0))
        return {'start': str(start), 'end': str(end), 'currency': 'USD', 'timezone': 'UTC', 'total': clean(total),
                'gateway_spend': str(total_gateway), 'gateway_requests': len(gateway), 'gateway_pending_costs': sum(r['cost'] is None for r in gateway),
                'sync': syncs[0] if syncs else {'status': 'not_synced', 'detail': 'Sync with the gateway to verify totals.'},
                'users': [{**identity(key), **clean(value)} for key, value in sorted(groups.items(), key=lambda x: x[1]['spend'], reverse=True)],
                'sessions': [{'user_id': user, 'user_name': identity(user)['name'], 'run_id': run, 'title': titles.get(run, 'Session'), **clean(value)} for (user, run), value in sorted(sessions.items(), key=lambda x: x[1]['spend'], reverse=True)],
                'models': [{'model': key, **clean(value)} for key, value in models.items()],
                'identities': list(users.values()), 'tracked_since': min((r['created_at'] for r in requests.values()), default=None)}

    async def watch(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Reporting outages do not interrupt agent sessions.
                pass

    def routes(self):
        router = APIRouter()

        @router.get('/api/admin/spend')
        async def report(request: Request, start: date | None = None, end: date | None = None):
            self.security.require(request, admin=True)
            return self.report(start, end)

        @router.post('/api/admin/spend/sync')
        async def sync(request: Request, start: date | None = None, end: date | None = None):
            self.security.require(request, mutation=True, admin=True)
            await self.sync(start, end)
            return self.report(start, end)

        @router.post('/api/admin/spend/link-slack')
        async def link(body: IdentityLink, request: Request):
            self.security.require(request, mutation=True, admin=True)
            actor = self.store.identity(self.security.session_info(request))
            with self.store.connect() as conn:
                source = conn.execute("SELECT id FROM users WHERE id=? AND kind='slack'", (body.slack_user_id,)).fetchone()
                target = conn.execute("SELECT id FROM users WHERE id=? AND kind='google'", (body.google_user_id,)).fetchone()
                if not source or not target:
                    raise HTTPException(422, 'Choose a Slack account and an existing Google sign-in.')
                conn.execute('UPDATE users SET linked_user_id=?,updated_at=? WHERE id=?', (body.google_user_id, now(), body.slack_user_id))
                conn.execute('INSERT INTO identity_audit(actor_id,source_id,target_id,created_at) VALUES(?,?,?,?)', (actor, body.slack_user_id, body.google_user_id, now()))
            return {'ok': True}

        return router
