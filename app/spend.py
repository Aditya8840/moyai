"""Durable, server-attributed gateway usage; monetary values remain decimal strings."""
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

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
                CREATE TABLE IF NOT EXISTS cost_callbacks (
                    request_id TEXT NOT NULL REFERENCES model_requests(id), event_id TEXT NOT NULL,
                    cost TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                    received_at TEXT NOT NULL, PRIMARY KEY(request_id,event_id)
                );
            ''')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(model_requests)')}
            if 'callback_at' not in columns:
                conn.execute('ALTER TABLE model_requests ADD COLUMN callback_at TEXT')
            if 'callback_pending' not in columns:
                conn.execute('ALTER TABLE model_requests ADD COLUMN callback_pending INTEGER NOT NULL DEFAULT 0')

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
        self.store.execute('UPDATE model_requests SET gateway_id=?,cost=CASE WHEN callback_at IS NULL THEN ? ELSE cost END WHERE id=?', (gateway_id, cost, request_id))

    def finish(self, request_id, capture, status):
        usage = capture.usage if capture else {}
        def tokens(field):
            value = usage.get(field)
            return value if type(value) is int and value >= 0 else None
        self.store.execute('UPDATE model_requests SET status=?,finished_at=?,cost=CASE WHEN callback_at IS NULL THEN COALESCE(?,cost) ELSE cost END,prompt_tokens=COALESCE(prompt_tokens,?),completion_tokens=COALESCE(completion_tokens,?),total_tokens=COALESCE(total_tokens,?) WHERE id=?',
                           (status, now(), capture.cost if capture else None, tokens('prompt_tokens'), tokens('completion_tokens'), tokens('total_tokens'), request_id))

    def callback_token(self, request_id):
        return hmac.new(self.security.secret.encode(), ('moyai-cost:' + request_id).encode(), hashlib.sha256).hexdigest()

    def callback(self, payload):
        records = payload if isinstance(payload, list) else [payload]
        if not records or len(records) > 1000:
            raise HTTPException(422, 'Send one cost event or a batch of up to 1000.')
        normalized = []
        for record in records:
            if not isinstance(record, dict):
                raise HTTPException(422, 'Invalid cost event.')
            metadata = record.get('metadata') or {}
            if not isinstance(metadata, dict):
                raise HTTPException(422, 'Invalid cost metadata.')
            # StandardLoggingPayload preserves this caller-owned metadata slot.
            source = metadata.get('spend_logs_metadata') or metadata.get('requester_metadata') or metadata
            if not isinstance(source, dict):
                raise HTTPException(401, 'Invalid cost event authentication.')
            request_id, token = source.get('moyai_request_id'), source.get('moyai_cost_token')
            if (not isinstance(request_id, str) or len(request_id) > 100 or not isinstance(token, str)
                    or not hmac.compare_digest(token, self.callback_token(request_id))):
                raise HTTPException(401, 'Invalid cost event authentication.')
            rows = self.store.rows('SELECT * FROM model_requests WHERE id=?', (request_id,))
            if not rows:
                raise HTTPException(401, 'Invalid cost event authentication.')
            local = rows[0]
            # The token is valid only for one pre-existing broker request. The
            # sender/owner/model remain those captured by the server, never the
            # untrusted user/model strings in the callback payload.
            supplied_key = metadata.get('user_api_key_hash')
            if supplied_key and supplied_key != local['key_hash']:
                raise HTTPException(401, 'Cost event belongs to another gateway key.')
            event_id = record.get('id') or record.get('litellm_call_id')
            if not isinstance(event_id, str) or not event_id or len(event_id) > 300:
                raise HTTPException(422, 'Cost event requires a stable event ID.')
            cost = money(record.get('response_cost'))
            usage = record.get('usage') if isinstance(record.get('usage'), dict) else record
            def tokens(field):
                value = usage.get(field)
                return value if type(value) is int and value >= 0 else None
            normalized.append((request_id, event_id, cost, tokens('prompt_tokens'), tokens('completion_tokens'), tokens('total_tokens'), now()))
        accepted = 0
        with self.store.connect() as conn:
            for row in normalized:
                accepted += conn.execute('INSERT OR IGNORE INTO cost_callbacks VALUES(?,?,?,?,?,?,?)', row).rowcount
            for request_id in {row[0] for row in normalized}:
                events = conn.execute('SELECT * FROM cost_callbacks WHERE request_id=?', (request_id,)).fetchall()
                total = sum((Decimal(event['cost'] or '0') for event in events), Decimal(0))
                pending = any(event['cost'] is None for event in events)
                def tokens(field):
                    known = [event[field] for event in events if event[field] is not None]
                    return sum(known) if known else None
                conn.execute('UPDATE model_requests SET cost=?,callback_at=?,callback_pending=?,prompt_tokens=?,completion_tokens=?,total_tokens=? WHERE id=?',
                             (str(total), now(), pending, tokens('prompt_tokens'), tokens('completion_tokens'), tokens('total_tokens'), request_id))
        return {'accepted': accepted, 'duplicates': len(records) - accepted}

    def report(self, start=None, end=None):
        start, end, lower, upper = period(start, end)
        users = {u['id']: u for u in self.store.rows('SELECT id,kind,email,name,linked_user_id FROM users')}
        requests = {r['id']: r for r in self.store.rows('SELECT * FROM model_requests WHERE key_hash=?', (self.key_hash,))}
        rows = [row for row in requests.values() if lower <= row['created_at'] < upper]
        def empty():
            return {'spend': Decimal(0), 'requests': 0, 'pending_costs': 0, 'unreconciled_requests': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'sessions': set()}
        groups = {user['id']: empty() for user in users.values() if user['kind'] == 'google'}
        sessions, models = {}, {}
        def add(bucket, row):
            bucket['requests'] += 1
            bucket['pending_costs'] += row['cost'] is None or bool(row['callback_pending'])
            bucket['unreconciled_requests'] += not bool(row['callback_at'])
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
        callback_total = sum((Decimal(row['cost'] or '0') for row in rows if row['callback_at']), Decimal(0))
        return {'start': str(start), 'end': str(end), 'currency': 'USD', 'timezone': 'UTC', 'total': clean(total),
                'callback_spend': str(callback_total), 'callback_requests': sum(bool(row['callback_at']) for row in rows),
                'last_callback_at': max((row['callback_at'] for row in requests.values() if row['callback_at']), default=None),
                'users': [{**identity(key), **clean(value)} for key, value in sorted(groups.items(), key=lambda x: x[1]['spend'], reverse=True)],
                'sessions': [{'user_id': user, 'user_name': identity(user)['name'], 'run_id': run, 'title': titles.get(run, 'Session'), **clean(value)} for (user, run), value in sorted(sessions.items(), key=lambda x: x[1]['spend'], reverse=True)],
                'models': [{'model': key, **clean(value)} for key, value in models.items()],
                'identities': list(users.values()), 'tracked_since': min((r['created_at'] for r in requests.values()), default=None)}

    def routes(self):
        router = APIRouter()

        @router.get('/api/admin/spend')
        async def report(request: Request, start: date | None = None, end: date | None = None):
            self.security.require(request, admin=True)
            return self.report(start, end)

        @router.post('/hooks/litellm/cost')
        async def callback(request: Request):
            raw = await request.body()
            if len(raw) > 5 * 1024 * 1024:
                raise HTTPException(413, 'Cost callback is too large.')
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(422, 'Invalid cost event JSON.')
            result = self.callback(payload)
            await self.checkpoints.flush()
            return result

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
