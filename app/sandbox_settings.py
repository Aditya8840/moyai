"""Admin-only, encrypted sandbox connection settings and live connection checks."""
import asyncio
import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .config import Settings
from .sandboxes import provider

FIELDS = {
    'modal': ('modal_token_id', 'modal_token_secret', 'modal_app_name', 'modal_vm_runtime'),
    'substrate': ('substrate_api_url', 'substrate_router_url', 'substrate_api_token', 'substrate_ca_cert',
                  'substrate_atespace', 'substrate_template', 'substrate_egress_hosts'),
}
SECRETS = {'modal_token_id', 'modal_token_secret', 'substrate_api_token'}


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['modal', 'substrate']
    revision: int = Field(ge=0)
    values: dict = Field(default_factory=dict, max_length=10)


class SandboxSettings:
    def __init__(self, store, settings, security):
        self.store, self.settings, self.security = store, settings, security
        self.lock = asyncio.Lock()
        store.execute('CREATE TABLE IF NOT EXISTS sandbox_settings (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, encrypted TEXT NOT NULL)')
        rows = store.rows('SELECT * FROM sandbox_settings WHERE id=1')
        if rows:
            saved = json.loads(security.decrypt(rows[0]['encrypted']))
            validated = Settings(_env_file=None, **{**settings.model_dump(), **saved})
            for key in saved:
                setattr(settings, key, getattr(validated, key))
        else:
            if not settings.substrate_signing_key:
                settings.substrate_signing_key = base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()
            self.save(0)

    def save(self, revision):
        values = {key: getattr(self.settings, key) for fields in FIELDS.values() for key in fields}
        values.update(sandbox_provider=self.settings.sandbox_provider, substrate_signing_key=self.settings.substrate_signing_key)
        self.store.execute('INSERT INTO sandbox_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,encrypted=excluded.encrypted',
                           (revision, self.security.encrypt(json.dumps(values))))

    def view(self, admin):
        result = {'provider': self.settings.sandbox_provider,
                  'revision': self.store.rows('SELECT revision FROM sandbox_settings WHERE id=1')[0]['revision'],
                  'providers': {name: {'configured': not self.settings.missing_sandbox(name)} for name in FIELDS}}
        if admin:
            from sandbox.substrate_protocol import private_key
            result['public_key'] = base64.b64encode(private_key(self.settings.substrate_signing_key).public_key().public_bytes_raw()).decode()
            for name, fields in FIELDS.items():
                result['providers'][name]['values'] = {key: getattr(self.settings, key) for key in fields if key not in SECRETS}
                result['providers'][name]['secrets'] = {key: bool(getattr(self.settings, key)) for key in fields if key in SECRETS}
        return result

    def candidate(self, body):
        if set(body.values) - set(FIELDS[body.provider]):
            raise HTTPException(422, 'Unknown sandbox connection setting.')
        changes = {key: value for key, value in body.values.items() if key not in SECRETS or value}
        try:
            candidate = Settings(_env_file=None, **{**self.settings.model_dump(), **changes, 'sandbox_provider': body.provider})
        except Exception:
            raise HTTPException(422, 'Invalid sandbox connection. Check endpoints, names, and credentials.') from None
        if candidate.missing_sandbox():
            raise HTTPException(422, 'Complete the connection fields: ' + ', '.join(candidate.missing_sandbox()))
        # An existing actor/snapshot cannot be moved to another cluster by
        # editing credentials. Switching defaults between providers is safe.
        if any(getattr(candidate, key) != getattr(self.settings, key) for key in
               ('substrate_api_url', 'substrate_router_url', 'substrate_atespace')) and self.store.rows(
                "SELECT 1 FROM runs WHERE sandbox_provider='substrate' AND (snapshot_id!='' OR sandbox_id!='') LIMIT 1"):
            raise HTTPException(409, 'Existing Substrate sessions belong to this cluster. Use a separate Moyai installation for another cluster.')
        return candidate

    def routes(self):
        router = APIRouter()

        @router.get('/api/settings/sandboxes')
        async def get(request: Request):
            self.security.require(request)
            return self.view(self.security.role(request) == 'admin')

        @router.put('/api/settings/sandboxes')
        async def connect(body: Connection, request: Request):
            self.security.require(request, mutation=True, admin=True)
            async with self.lock:
                if body.revision != self.view(False)['revision']:
                    raise HTTPException(409, 'Sandbox settings changed. Reload before saving.')
                candidate = self.candidate(body)
                try:
                    async with asyncio.timeout(180):
                        message = await provider(candidate).check()
                except Exception:
                    raise HTTPException(502, 'Connection test failed. Check credentials, endpoints, worker capacity, and the Moyai actor template. Your saved connection was not changed.') from None
                for key in FIELDS[body.provider]:
                    setattr(self.settings, key, getattr(candidate, key))
                self.settings.sandbox_provider = body.provider
                self.save(body.revision + 1)
                return {**self.view(True), 'message': message}

        return router
