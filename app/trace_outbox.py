"""Persist sanitized OTLP spans before sending them; retry with unchanged IDs."""
import asyncio
import logging
import time

import httpx
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest, ExportTraceServiceResponse,
)

log = logging.getLogger(__name__)


TABLES = frozenset({'trace_outbox', 'trace_outbox_raindrop'})


class TraceOutbox:
    # One table per receiver: each keeps its own retries and receipts, so an
    # outage at one destination never delays or duplicates delivery to another.
    def __init__(self, store, table, endpoint, headers):
        if table not in TABLES:
            raise ValueError('Unknown trace outbox')
        self.store, self.table, self.endpoint = store, table, endpoint
        self.headers = {**headers, 'Content-Type': 'application/x-protobuf'}
        self.client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.task = None
        with store.connect() as conn:
            conn.executescript(f'''
                CREATE TABLE IF NOT EXISTS {table} (
                    trace_id TEXT NOT NULL, span_id TEXT NOT NULL, payload BLOB,
                    created_at REAL NOT NULL, delivered_at REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(trace_id,span_id)
                );
                CREATE INDEX IF NOT EXISTS {table}_pending
                    ON {table}(next_attempt_at,created_at) WHERE delivered_at IS NULL;
            ''')

    def enqueue(self, span, connection=None):
        payload = encode_spans([span]).SerializeToString()
        values = (format(span.context.trace_id, '032x'), format(span.context.span_id, '016x'),
                  payload, time.time())
        sql = f'''INSERT OR IGNORE INTO {self.table}(trace_id,span_id,payload,created_at)
                  VALUES(?,?,?,?)'''
        if connection is not None:
            connection.execute(sql, values)
        else:
            with self.store.connect() as conn:
                conn.execute(sql, values)
        self.wake.set()

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            self.wake.clear()
            try:
                if await self.export_once():
                    continue
            except Exception as exc:
                # Diagnostics must not include span contents or credentials.
                log.warning('Trace delivery to %s will retry (%s)', self.table, type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def export_once(self):
        async with self.lock:
            rows = self.store.rows(f'''SELECT * FROM {self.table} WHERE delivered_at IS NULL
                AND next_attempt_at<=? ORDER BY created_at LIMIT 64''', (time.time(),))
            if not rows:
                return False
            request = ExportTraceServiceRequest()
            for row in rows:
                request.MergeFrom(ExportTraceServiceRequest.FromString(row['payload']))
            error = ''
            try:
                response = await self.client.post(self.endpoint, headers=self.headers,
                                                  content=request.SerializeToString())
                if response.status_code != 200:
                    error = 'HTTP ' + str(response.status_code)
                elif response.content:
                    if 'application/json' in response.headers.get('content-type', ''):
                        result = response.json()
                        partial = result.get('partialSuccess', result.get('partial_success', {}))
                        rejected = int(partial.get('rejectedSpans', partial.get('rejected_spans', 0)))
                    else:
                        rejected = ExportTraceServiceResponse.FromString(response.content).partial_success.rejected_spans
                    if rejected:
                        error = 'OTLP partial rejection'
            except Exception as exc:
                error = type(exc).__name__
            stamp = time.time()
            with self.store.connect() as conn:
                for row in rows:
                    if error:
                        delay = min(300, 2 ** min(row['attempts'] + 1, 9))
                        conn.execute(f'''UPDATE {self.table} SET attempts=attempts+1,
                            next_attempt_at=?,last_error=? WHERE trace_id=? AND span_id=?''',
                            (stamp + delay, error, row['trace_id'], row['span_id']))
                    else:
                        # Retain a small receipt so journal replay cannot enqueue
                        # an acknowledged span again. Drop its sensitive payload.
                        conn.execute(f'''UPDATE {self.table} SET delivered_at=?,payload=NULL,
                            attempts=attempts+1,last_error='' WHERE trace_id=? AND span_id=?''',
                            (stamp, row['trace_id'], row['span_id']))
            if error:
                log.warning('Trace delivery to %s will retry (%s)', self.table, error)
            return not error

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Undelivered spans stay on disk; shutdown does not wait on the gateway.
        await self.client.aclose()
