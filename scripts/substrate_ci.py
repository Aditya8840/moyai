"""Configure the disposable GitHub runner's real cluster and run smoke tests."""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.config import Settings
from scripts.substrate_template import template


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    private = Ed25519PrivateKey.generate()
    os.environ['SUBSTRATE_SIGNING_KEY'] = base64.b64encode(private.private_bytes_raw()).decode()
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    os.environ['SUBSTRATE_ATESPACE'] = 'ate-demo-sandbox'
    os.environ['SUBSTRATE_TEMPLATE'] = 'moyai'
    os.environ['SUBSTRATE_API_TOKEN'] = output('kubectl', '-n', 'ate-system', 'create', 'token', 'ate-client', '--audience=api.ate-system.svc', '--duration=1h')
    bundles = json.loads(output('kubectl', 'get', 'clustertrustbundles', '-o', 'json'))
    os.environ['SUBSTRATE_CA_CERT'] = '\n'.join(x['spec']['trustBundle'] for x in bundles['items'] if x['spec'].get('signerName') == 'servicedns.podcert.ate.dev/identity')
    if not os.environ['SUBSTRATE_CA_CERT']:
        raise RuntimeError('Substrate service trust bundle was not installed')
    subprocess.run(['sudo', 'sh', '-c', 'echo "127.0.0.1 api.ate-system.svc" >> /etc/hosts'], check=True)
    os.environ['SUBSTRATE_API_URL'] = 'https://api.ate-system.svc:18080'
    os.environ['SUBSTRATE_ROUTER_URL'] = 'http://127.0.0.1:18081'
    forwards = [subprocess.Popen(['kubectl', '-n', 'ate-system', 'port-forward', 'svc/' + svc, ports],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for svc, ports in [('api', '18080:443'), ('atenet-router', '18081:80')]]
    try:
        time.sleep(3)
        from app.sandboxes.substrate import SubstrateProvider
        from google.protobuf.json_format import ParseDict
        from app.sandboxes.proto import ateapi_pb2 as pb
        from scripts.substrate_smoke import main as smoke

        async def run():
            backend = SubstrateProvider(Settings(_env_file=None))
            # Reuse the installed sandbox demo's storage scheme and worker pool.
            existing = await backend.rpc('GetActorTemplate', {'actor_template': {'atespace': 'ate-demo-sandbox', 'name': 'sandbox-template'}})
            config = template(image='localhost:5001/moyai-sandbox:test', storage=existing.snapshot_config.storage_location,
                              public_key=public, atespace='ate-demo-sandbox', memory='1536Mi', workload='sandbox')
            message = ParseDict(config, pb.ActorTemplate())
            await backend.rpc('CreateActorTemplate', {'actor_template': message})
            await smoke()
        asyncio.run(run())
    finally:
        for process in forwards:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    main()
