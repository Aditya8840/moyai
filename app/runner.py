import asyncio
import json
import re
import secrets
from pathlib import Path

import modal

from .security import digest

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "idle"}
SANDBOX_FILES = Path(__file__).parent.parent / "sandbox"


class RunManager:
    """Single-process durable admission; interrupted work is never silently replayed."""
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.jobs = {}
        self.sandboxes = {}
        self.slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self.closing = False
        self.prepare_context = None

    async def persist(self):
        """Replaced by the cloud checkpoint callback when hosted on Modal."""

    async def terminate(self, sandbox):
        async with asyncio.timeout(30):
            await sandbox.terminate.aio()
            # terminate() acknowledges the request before the machine exits.
            await sandbox.wait.aio(raise_on_termination=False)

    async def recover(self):
        rows = self.store.rows("SELECT id,sandbox_id,status FROM runs WHERE status NOT IN ('completed','failed','cancelled','interrupted','idle') OR EXISTS(SELECT 1 FROM messages WHERE messages.run_id=runs.id AND messages.status IN ('running','queued'))")
        for row in rows:
            self.store.update_run(row["id"], status="interrupted", token_hash="", error="The workspace restarted. This task was not replayed.")
            self.store.execute("UPDATE approvals SET status='expired' WHERE run_id=? AND status IN ('pending','approved')", (row["id"],))
            self.store.execute("UPDATE approvals SET status='uncertain' WHERE run_id=? AND status='executing'", (row["id"],))
            self.store.execute("UPDATE messages SET status='interrupted' WHERE run_id=? AND status IN ('running','queued')", (row["id"],))
            self.store.event(row["id"], "error", "Workspace restarted. Unfinished messages were interrupted and not replayed. Send a new message to continue from the last saved workspace.")
            if row["sandbox_id"] and self.settings.modal_token_id and self.settings.modal_token_secret:
                try:
                    sandbox = await modal.Sandbox.from_id.aio(row["sandbox_id"], client=await self.client())
                    await self.terminate(sandbox)
                except Exception:
                    self.store.event(row["id"], "error", "Could not confirm sandbox cleanup. Check Modal; its configured timeout still applies.")

    async def client(self):
        return await modal.Client.from_credentials.aio(self.settings.modal_token_id, self.settings.modal_token_secret)

    def submit(self, run):
        if self.closing or run["id"] in self.jobs:
            return
        task = asyncio.create_task(self.chat(run) if run.get("chat_enabled") else self.execute(run))
        self.jobs[run["id"]] = task
        def done(completed):
            if self.jobs.get(run["id"]) is completed:
                self.jobs.pop(run["id"], None)
            if not completed.cancelled() and completed.exception():
                self.store.update_run(run["id"], status="failed", token_hash="", error="Session processing stopped unexpectedly. No unfinished messages were replayed.")
                self.store.execute("UPDATE messages SET status='interrupted' WHERE run_id=? AND status IN ('running','queued')", (run["id"],))
            # A message may arrive while the last checkpoint is being saved.
            if not self.closing and run.get("chat_enabled") and self.store.has_queued_messages(run["id"]):
                self.submit(self.store.run(run["id"]))
        task.add_done_callback(done)

    async def chat(self, run):
        run_id = run["id"]
        if self.prepare_context:
            await self.prepare_context(run_id)
        while not self.closing:
            message = self.store.claim_message(run_id)
            if not message:
                return
            turn = {**self.store.run(run_id), "prompt": message["content"], "message_id": message["id"]}
            try:
                await self.execute(turn)
            except asyncio.CancelledError:
                self.store.finish_message(run_id, message["id"], "This response was interrupted. Send a new message to continue from the last saved workspace; external actions were not replayed.", "interrupted")
                self.store.execute("UPDATE messages SET status='interrupted' WHERE run_id=? AND status='queued'", (run_id,))
                raise
            row = self.store.run(run_id)
            if row["status"] == "completed":
                self.store.finish_message(run_id, message["id"], row["summary"])
                self.store.update_run(run_id, status="queued" if self.store.has_queued_messages(run_id) else "idle")
                await self.persist()
            else:
                explanation = row["summary"] or row["error"] or "This response was stopped. The last completed workspace checkpoint is preserved; recent unfinished changes may not be saved."
                self.store.finish_message(run_id, message["id"], explanation, row["status"])
                self.store.execute("UPDATE messages SET status='cancelled' WHERE run_id=? AND status='queued'", (run_id,))
                await self.persist()
                return

    async def shutdown(self):
        self.closing = True
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def stopped(self, run_id):
        return self.store.run(run_id)["status"] in TERMINAL | {"stopping"}

    async def cancel(self, run_id):
        run = self.store.run(run_id)
        if run["status"] in TERMINAL and run_id not in self.jobs:
            return
        # Revoke capabilities immediately, even while provisioning is still in flight.
        self.store.update_run(run_id, status="stopping", token_hash="")
        self.store.execute("UPDATE messages SET status='cancelled' WHERE run_id=? AND status='queued'", (run_id,))
        self.store.execute("UPDATE approvals SET status='expired' WHERE run_id=? AND status IN ('pending','approved')", (run_id,))
        self.store.event(run_id, "status", "Stop requested. Finishing sandbox cleanup.")
        sandbox = self.sandboxes.get(run_id)
        if sandbox:
            await sandbox.terminate.aio()
        elif run["status"] == "queued":
            self.store.update_run(run_id, status="cancelled")

    async def execute(self, run):
        run_id = run["id"]
        try:
            async with self.slots:
                if self.stopped(run_id):
                    return
                async with asyncio.timeout(self.settings.run_timeout_seconds + 180):
                    if run["mode"] == "demo":
                        await self.demo(run)
                    else:
                        await self.cloud(run)
        except asyncio.CancelledError:
            self.store.update_run(run_id, status="interrupted", token_hash="", error="Workspace shut down while the task was active.")
            self.store.event(run_id, "error", "Workspace shut down. This run was interrupted.")
            raise
        except Exception as exc:
            if self.stopped(run_id):
                self.store.update_run(run_id, status="cancelled", token_hash="")
            else:
                message = "Task exceeded its time limit." if isinstance(exc, TimeoutError) else f"Cloud run failed ({type(exc).__name__}). Check runtime configuration and Modal logs."
                self.store.update_run(run_id, status="failed", token_hash="", error=message)
                self.store.event(run_id, "error", message)
        finally:
            sandbox = self.sandboxes.pop(run_id, None)
            if sandbox:
                try:
                    await self.terminate(sandbox)
                    self.store.event(run_id, "status", "Sandbox terminated")
                except Exception:
                    self.store.event(run_id, "error", "Sandbox cleanup was not confirmed. Check Modal; the sandbox timeout still applies.")
            row = self.store.run(run_id)
            if row["status"] == "stopping":
                self.store.update_run(run_id, status="cancelled")
            self.store.update_run(run_id, token_hash="")
            self.store.execute("UPDATE approvals SET status='expired' WHERE run_id=? AND status IN ('pending','approved')", (run_id,))
            await self.persist()

    async def demo(self, run):
        run_id = run["id"]
        self.store.update_run(run_id, status="running")
        steps = [
            ("status", "Demo started · no cloud machine or model is being used"),
            ("plan", "Preview the task workflow, stream activity, and save a result."),
            ("tool", "Simulated sandbox ready", {"command": "Sandbox creation will happen on Modal in cloud mode."}),
            ("tool", "Simulated workspace inspection", {"command": "No repository files are read or changed in demo mode."}),
            ("result", "Demo complete. Your task and activity are saved. Configure Modal and your model gateway in Runtime to execute this task with Hermes."),
        ]
        for step in steps:
            await asyncio.sleep(self.settings.demo_step_seconds)
            if self.stopped(run_id):
                return
            self.store.event(run_id, *step)
        self.store.update_run(run_id, status="completed", summary=steps[-1][1])

    def image(self):
        revision = self.settings.hermes_revision
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("HERMES_REVISION must be a full commit SHA")
        return (modal.Image.debian_slim(python_version="3.14")
                .apt_install("git", "chromium", "ca-certificates", "build-essential", "libffi-dev", "ripgrep", "nodejs", "npm")
                .pip_install("playwright==1.58.0")
                .env({"HERMES_RUNTIME_DIR": "/opt/hermes-tools", "PYTHONPATH": "/opt/hermes"})
                .run_commands(f"git init /opt/hermes && cd /opt/hermes && git remote add origin https://github.com/NousResearch/hermes-agent.git && git fetch --depth 1 origin {revision} && git checkout --detach FETCH_HEAD",
                              "cd /opt/hermes && python -m pm.build_env --source /opt/hermes --out /opt/hermes-env --no-install-project --extra mcp",
                              "cd /opt/hermes && /opt/hermes-env/bin/python -c 'from run_agent import AIAgent; import mcp'")
                .add_local_dir(SANDBOX_FILES, remote_path="/opt/workspace-runner", copy=True)
                .env({"PYTHONUNBUFFERED": "1", "PYTHONPATH": "/opt/hermes", "HERMES_PYTHON": "/opt/hermes-env/bin/python", "HERMES_HOME": "/tmp/hermes-home", "GIT_TERMINAL_PROMPT": "0"}))

    async def cloud(self, run):
        run_id = run["id"]
        self.store.update_run(run_id, status="provisioning")
        self.store.event(run_id, "status", "Provisioning an isolated Modal sandbox")
        client = await self.client()
        app = await modal.App.lookup.aio(self.settings.modal_app_name, create_if_missing=True, client=client)
        if self.stopped(run_id):
            return
        token = secrets.token_urlsafe(48)
        self.store.update_run(run_id, token_hash=digest(token))
        secret = modal.Secret.from_dict({"WORKSPACE_RUN_TOKEN": token})
        # Shield provisioning so cancellation cannot discard a successfully-created sandbox ID.
        image = modal.Image.from_id(run["snapshot_id"], client=client) if run.get("chat_enabled") and run.get("snapshot_id") else self.image()
        provision = asyncio.create_task(modal.Sandbox.create.aio(
            app=app, client=client, image=image, secrets=[secret],
            env={"PYTHONUNBUFFERED": "1", "PYTHONPATH": "/opt/hermes", "HERMES_HOME": "/tmp/hermes-home",
                 "HERMES_RUNTIME_DIR": "/opt/hermes-tools", "HERMES_PYTHON": "/opt/hermes-env/bin/python", "GIT_TERMINAL_PROMPT": "0"},
            timeout=self.settings.run_timeout_seconds, cpu=2, memory=4096,
            experimental_options={"vm_runtime": True} if self.settings.modal_vm_runtime else {},
        ))
        try:
            sandbox = await asyncio.shield(provision)
        except asyncio.CancelledError:
            sandbox = await provision
            self.sandboxes[run_id] = sandbox
            self.store.update_run(run_id, sandbox_id=sandbox.object_id)
            raise
        self.sandboxes[run_id] = sandbox
        self.store.update_run(run_id, sandbox_id=sandbox.object_id)
        await self.persist()
        if self.stopped(run_id):
            return
        spec = {"run_id": run_id, "prompt": run["prompt"], "repo_url": run["repo_url"],
                "broker_url": f"{self.settings.public_url.rstrip('/')}/broker/{run_id}",
                "model": self.settings.agent_model, "max_iterations": self.settings.max_agent_iterations,
                "timeout": self.settings.run_timeout_seconds - 90,
                "chat_enabled": bool(run.get("chat_enabled")),
                "slack_source": self.store.slack_source(run_id),
                "history_fallback": [{"role": m["role"], "content": (f"[Prior {m['status']} message; context only, do not replay] " if m["role"] == "user" and m["status"] != "completed" else "") + m["content"]} for m in self.store.messages(run_id)
                                     if m["id"] < run.get("message_id", 0) and m["status"] not in {"queued", "running"}]}
        # Restored snapshots can contain an older adapter; refresh only our own
        # runner files, preserving all user workspace files and agent history.
        if run.get("snapshot_id"):
            for name in ("agent.py", "mcp_bridge.py"):
                await sandbox.filesystem.write_text.aio((SANDBOX_FILES / name).read_text(), f"/opt/workspace-runner/{name}")
        await sandbox.filesystem.write_text.aio(json.dumps(spec), "/tmp/task.json")
        self.store.update_run(run_id, status="running")
        self.store.event(run_id, "status", "Sandbox ready. Starting Hermes Agent.")
        # Modal streams arbitrary chunks by default. Protocol events are JSON
        # lines and must be framed before decoding, including parallel tools.
        process = await sandbox.exec.aio("/opt/hermes-env/bin/python", "/opt/workspace-runner/agent.py", "/tmp/task.json",
                                         timeout=self.settings.run_timeout_seconds, bufsize=1)
        result = None
        secrets_to_hide = [token, self.settings.litellm_api_key, self.settings.modal_token_secret]

        def scrub(value):
            for secret_value in secrets_to_hide:
                if secret_value:
                    value = value.replace(secret_value, "[redacted]")
            return value

        async def stderr():
            # Drain both streams to prevent subprocess deadlocks. Raw third-party diagnostics
            # stay in Modal, since they may contain credentials or provider request bodies.
            async for _ in process.stderr:
                pass

        drain = asyncio.create_task(stderr())
        try:
            async for line in process.stdout:
                if self.stopped(run_id):
                    return
                if line.startswith("WORKSPACE_EVENT "):
                    try:
                        event = json.loads(scrub(line[len("WORKSPACE_EVENT "):]))
                        if event.get("kind") == "final":
                            result = event
                        elif event.get("kind") in {"tool", "status", "error", "message"}:
                            self.store.event(run_id, event["kind"], str(event.get("message", "")), event.get("data", {}))
                    except (ValueError, TypeError):
                        self.store.event(run_id, "error", "An agent progress event could not be decoded.")
            code = await process.wait.aio()
            if self.stopped(run_id):
                return
            await self.save_artifact(sandbox, run_id)
            if self.stopped(run_id):
                return
            if run.get("chat_enabled"):
                self.store.update_run(run_id, status="saving", token_hash="")
                self.store.event(run_id, "status", "Saving conversation and workspace for your next message")
                snapshot = await sandbox.snapshot_filesystem.aio(timeout=55, ttl=None)
                self.store.update_run(run_id, snapshot_id=snapshot.object_id)
                await self.persist()
            if self.stopped(run_id):
                return
            if not result or code != 0 or not result.get("completed"):
                self.store.update_run(run_id, status="failed", error="Hermes did not complete the task.", summary=str((result or {}).get("message", "")))
                self.store.event(run_id, "error", str((result or {}).get("message") or "Hermes exited before completing. Review Modal logs for startup or provider errors."))
            else:
                self.store.update_run(run_id, status="completed", summary=result["message"])
                self.store.event(run_id, "result", result["message"])
        finally:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)

    async def save_artifact(self, sandbox, run_id):
        try:
            info = await sandbox.filesystem.stat.aio("/artifacts/result.zip")
            if info.size > 20 * 1024 * 1024:
                self.store.event(run_id, "error", "Artifact archive exceeds the 20 MB limit and was not downloaded.")
                return
            data = await sandbox.filesystem.read_bytes.aio("/artifacts/result.zip")
            if len(data) > 20 * 1024 * 1024:
                return
            directory = self.settings.data_dir / "artifacts"
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / f"{run_id}.zip"
            path.write_bytes(data)
            path.chmod(0o600)
            self.store.event(run_id, "artifact", "Result archive saved", {"download": f"/api/runs/{run_id}/artifact"})
        except Exception:
            self.store.event(run_id, "error", "No result archive was recovered from this run.")
