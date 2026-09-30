import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.generation = 0
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "workspace.db"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, prompt TEXT NOT NULL, repo_url TEXT NOT NULL,
                    mode TEXT NOT NULL, status TEXT NOT NULL, plugins TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    sandbox_id TEXT NOT NULL DEFAULT '', snapshot_id TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL DEFAULT '', model_calls INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_id_id ON events(run_id, id);
                CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                    client_id TEXT, created_at TEXT NOT NULL,
                    UNIQUE(run_id, client_id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_run ON messages(run_id,id);
                CREATE TABLE IF NOT EXISTS connections (
                    provider TEXT PRIMARY KEY, encrypted TEXT NOT NULL,
                    label TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connection_policies (
                    provider TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    read_only INTEGER NOT NULL DEFAULT 0 CHECK(read_only IN (0,1)),
                    checked_at TEXT, check_status TEXT
                );
                CREATE TABLE IF NOT EXISTS organization (
                    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connection_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL, action TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS slack_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    channel TEXT NOT NULL, thread_ts TEXT NOT NULL,
                    user_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    reply_status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    tool TEXT NOT NULL, arguments TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, result TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    session_id TEXT NOT NULL, expires REAL NOT NULL
                );
                PRAGMA optimize;
            """)
            if "model_calls" not in {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}:
                conn.execute("ALTER TABLE runs ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            for name in ("chat_enabled", "turn_model_calls"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(slack_events)")}
            for name, default in (("mention_ts", ""), ("context_status", "legacy"), ("context_json", "{}")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE slack_events ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
            if conn.total_changes:
                self.generation += 1
        finally:
            conn.close()

    def execute(self, sql, params=()):
        with self.connect() as conn:
            return conn.execute(sql, params).rowcount

    def rows(self, sql, params=()):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def run(self, run_id: str):
        rows = self.rows("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        row["plugins"] = json.loads(row["plugins"])
        return row

    def create_run(self, prompt: str, repo_url: str, mode: str, plugins: list[str], *, chat_enabled=False):
        run_id = uuid4().hex
        stamp = now()
        self.execute(
            "INSERT INTO runs(id,prompt,repo_url,mode,status,plugins,created_at,updated_at,chat_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
            (run_id, prompt, repo_url, mode, "queued", json.dumps(plugins), stamp, stamp, chat_enabled),
        )
        if chat_enabled:
            self.execute("INSERT INTO messages(run_id,role,content,status,client_id,created_at) VALUES(?,'user',?,'queued','initial',?)", (run_id, prompt, stamp))
        self.event(run_id, "status", "Task queued")
        return self.run(run_id)

    def create_slack_run(self, event_id, prompt, plugins, channel, thread_ts, user_id, mention_ts=None):
        # Slack retries deliveries. Reserve the event and its run in the same
        # transaction so parallel deliveries cannot create multiple sandboxes.
        run_id, stamp = uuid4().hex, now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM slack_events WHERE event_id=?", (event_id,)).fetchone():
                return None
            pending = conn.execute("SELECT COUNT(*) FROM runs WHERE status NOT IN ('completed','failed','cancelled','interrupted','idle')").fetchone()[0]
            if pending >= 20:
                raise ValueError("The session queue is full.")
            conn.execute("INSERT INTO runs(id,prompt,repo_url,mode,status,plugins,created_at,updated_at,chat_enabled) VALUES(?,?,'','modal','queued',?,?,?,1)",
                         (run_id, prompt, json.dumps(plugins), stamp, stamp))
            conn.execute("INSERT INTO messages(run_id,role,content,status,client_id,created_at) VALUES(?,'user',?,'queued','initial',?)", (run_id, prompt, stamp))
            conn.execute("INSERT INTO slack_events(event_id,run_id,channel,thread_ts,user_id,created_at,mention_ts,context_status) VALUES(?,?,?,?,?,?,?,'pending')",
                         (event_id, run_id, channel, thread_ts, user_id, stamp, mention_ts or thread_ts))
            conn.execute("INSERT INTO events(run_id,kind,message,data,created_at) VALUES(?,'status','Session requested from Slack','{}',?)", (run_id, stamp))
        return self.run(run_id)

    def slack_source(self, run_id):
        rows = self.rows("SELECT channel,thread_ts,mention_ts,user_id,context_status,context_json FROM slack_events WHERE run_id=?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        context = json.loads(row.pop("context_json"))
        return {**context, **row}

    def messages(self, run_id):
        return self.rows("SELECT id,role,content,status,created_at FROM messages WHERE run_id=? ORDER BY id", (run_id,))

    def enqueue_message(self, run_id, content, client_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row or not row["chat_enabled"]:
                raise ValueError("This older task has no saved chat workspace. Start a new session.")
            existing = conn.execute("SELECT * FROM messages WHERE run_id=? AND client_id=?", (run_id, client_id)).fetchone()
            if existing:
                if existing["content"] != content:
                    raise ValueError("That message ID was already used for different text.")
                return dict(existing), False
            if row["status"] == "stopping":
                raise ValueError("Wait for the current response to stop before sending another message.")
            pending = conn.execute("SELECT COUNT(*) FROM messages WHERE run_id=? AND status='queued'", (run_id,)).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM messages WHERE run_id=? AND role='user'", (run_id,)).fetchone()[0]
            if pending >= 5 or count >= 100:
                raise ValueError("This session allows 5 queued messages and 100 turns. Wait, or start a new session.")
            if conn.execute("SELECT COUNT(*) FROM runs WHERE status NOT IN ('completed','failed','cancelled','interrupted','idle')").fetchone()[0] >= 20 and row["status"] in {"idle", "completed", "failed", "cancelled", "interrupted"}:
                raise ValueError("The session queue is full. Wait for a response to finish.")
            stamp = now()
            message_id = conn.execute("INSERT INTO messages(run_id,role,content,status,client_id,created_at) VALUES(?,'user',?,'queued',?,?)", (run_id, content, client_id, stamp)).lastrowid
            running = conn.execute("SELECT 1 FROM messages WHERE run_id=? AND status='running'", (run_id,)).fetchone()
            if not running:
                conn.execute("UPDATE runs SET status='queued',error='',updated_at=? WHERE id=?", (stamp, run_id))
        self.event(run_id, "chat", "Message queued", {"message_id": message_id})
        return {"id": message_id, "status": "queued"}, True

    def claim_message(self, run_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run or run["status"] in {"stopping", "cancelled", "interrupted"}:
                return None
            if conn.execute("SELECT 1 FROM messages WHERE run_id=? AND status='running'", (run_id,)).fetchone():
                return None
            row = conn.execute("SELECT * FROM messages WHERE run_id=? AND role='user' AND status='queued' ORDER BY id LIMIT 1", (run_id,)).fetchone()
            if not row:
                return None
            conn.execute("UPDATE messages SET status='running' WHERE id=?", (row["id"],))
            conn.execute("UPDATE runs SET status='queued',turn_model_calls=0,error='',summary='' WHERE id=?", (run_id,))
        self.event(run_id, "chat", "Response started", {"message_id": row["id"]})
        return dict(row)

    def finish_message(self, run_id, message_id, content, status="completed"):
        with self.connect() as conn:
            changed = conn.execute("UPDATE messages SET status=? WHERE id=? AND run_id=? AND status='running'", (status, message_id, run_id)).rowcount
            if changed:
                conn.execute("INSERT INTO messages(run_id,role,content,status,created_at) VALUES(?,'assistant',?,'completed',?)", (run_id, content, now()))
        self.event(run_id, "chat", "Response saved", {"message_id": message_id})

    def has_queued_messages(self, run_id):
        return bool(self.rows("SELECT id FROM messages WHERE run_id=? AND status='queued' LIMIT 1", (run_id,)))

    def update_run(self, run_id: str, **fields):
        allowed = {"status", "summary", "error", "sandbox_id", "snapshot_id", "token_hash"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported run update")
        fields["updated_at"] = now()
        keys = ",".join(f"{key}=?" for key in fields)
        self.execute(f"UPDATE runs SET {keys} WHERE id=?", (*fields.values(), run_id))

    def event(self, run_id: str, kind: str, message: str, data=None):
        # Keep bounded event history even if an agent floods stdout.
        count = self.rows("SELECT COUNT(*) AS n FROM events WHERE run_id=?", (run_id,))[0]["n"]
        if count >= 2000 and kind not in {"result", "error", "artifact", "approval", "status"}:
            return
        self.execute(
            "INSERT INTO events(run_id,kind,message,data,created_at) VALUES(?,?,?,?,?)",
            (run_id, kind, message[:32000], json.dumps(data or {}), now()),
        )

    def events(self, run_id: str, after: int = 0, limit: int = 200):
        rows = self.rows("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, after, limit))
        for row in rows:
            row["data"] = json.loads(row["data"])
        return rows

    def approvals(self, run_id: str):
        rows = self.rows("SELECT * FROM approvals WHERE run_id=? ORDER BY created_at", (run_id,))
        for row in rows:
            row["arguments"] = json.loads(row["arguments"])
        return rows
