"""Live Substrate conformance test; requires a disposable, configured cluster.

Never substitutes a fake control plane. All actors and tags created here are
deleted in finally blocks. No model or third-party account credentials needed.
"""
import asyncio
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.sandboxes.substrate import SubstrateProvider


async def execute(sandbox, code):
    process = await sandbox.exec.aio('/usr/local/bin/python', '-c', code, timeout=30)
    out, err = await asyncio.gather(process.stdout.read.aio(), process.stderr.read.aio())
    assert await process.wait.aio() == 0, err
    return out


async def main():
    settings = Settings()
    backend = SubstrateProvider(settings)
    actors, tags = [], []
    try:
        original = await backend.create(name='moyai-smoke-' + uuid4().hex, token='parent-test-capability', timeout=300)
        actors.append(original)
        print('PASS create, authentication, resume', flush=True)
        await original.filesystem.write_text.aio('persisted ✓', '/workspace/proof.txt')
        assert (await original.filesystem.read_bytes.aio('/workspace/proof.txt')).decode() == 'persisted ✓'
        assert 'out ✓' in await execute(original, 'import sys; print("out ✓"); print("err", file=sys.stderr)')
        await execute(original, 'from pathlib import Path; Path("/usr/local/rootfs-proof").write_text("rootfs")')
        print('PASS command, stdout/stderr, file read/write', flush=True)
        await original.exec.aio('/usr/local/bin/python', '-c',
            'import time; from pathlib import Path\np=Path("/workspace/counter")\nwhile True:\n p.write_text(str(int(p.read_text() if p.exists() else "0")+1)); time.sleep(.1)', timeout=120)
        await asyncio.sleep(1)
        snapshot = await original.snapshot_filesystem.aio(timeout=300)
        tags.append(snapshot.object_id)
        parent_before = await execute(original, 'from pathlib import Path; print(Path("/workspace/counter").read_text())')
        clone = await backend.create(name='moyai-clone-' + uuid4().hex, snapshot_id=snapshot.object_id, token='child-test-capability', timeout=300)
        actors.append(clone)
        assert await clone.filesystem.read_bytes.aio('/usr/local/rootfs-proof') == b'rootfs'
        value = await clone.filesystem.read_bytes.aio('/workspace/counter')
        await asyncio.sleep(1)
        assert await clone.filesystem.read_bytes.aio('/workspace/counter') == value, 'Clone resumed parent process!'
        assert int(await execute(original, 'from pathlib import Path; print(Path("/workspace/counter").read_text())')) > int(parent_before)
        assert (await execute(clone, 'import os; print(os.environ["WORKSPACE_RUN_TOKEN"])')).strip() == 'child-test-capability'
        print('PASS filesystem checkpoint, clone isolation, original process continuation', flush=True)
        reconnected = await SubstrateProvider(settings).get(clone.object_id)
        assert await reconnected.filesystem.read_bytes.aio('/workspace/proof.txt') == 'persisted ✓'.encode()
        print('PASS reconnect from persisted sandbox ID', flush=True)
        output = await execute(clone, 'from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"]); page=b.new_page(); page.set_content("<h1>Moyai</h1>"); print(page.locator("h1").inner_text()); page.screenshot(path="/workspace/browser.png"); b.close()')
        assert output.strip() == 'Moyai'
        assert (await clone.filesystem.read_bytes.aio('/workspace/browser.png')).startswith(b'\x89PNG')
        print('PASS real Chromium and screenshot artifact', flush=True)
        await clone.terminate.aio()
        from modal.exception import NotFoundError
        try:
            await backend.get(clone.object_id)
        except NotFoundError:
            pass
        else:
            raise AssertionError('Deleted actor still exists')
        print('PASS cancellation and cleanup', flush=True)
    finally:
        for sandbox in actors:
            await sandbox.terminate.aio()
        for identity in tags:
            _, space, _, tag = identity.split(':')
            await backend.rpc('DeleteTag', {'tag': {'atespace': space, 'name': tag}})


if __name__ == '__main__':
    asyncio.run(main())
