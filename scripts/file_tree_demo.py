"""Local real-app file tree demo: python scripts/file_tree_demo.py.

Serves the actual workspace UI and archive API with this checkout's tracked
source files in a saved session. No provider, Modal, or production credentials.
Open the printed session URL, then Files.
"""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from app.config import Settings
from app.main import create_app


def demo(directory):
    root = Path(__file__).resolve().parents[1]
    values = {key: field.get_default(call_default_factory=True) for key, field in Settings.model_fields.items()}
    values.update(data_dir=Path(directory), public_url='http://127.0.0.1:8797', auto_prepare_repositories=False)
    app = create_app(Settings(_env_file=None, **values))
    store = app.state.store
    actor = store.identity({'method': 'local', 'role': 'admin'})
    run = store.create_run('Browse workspace files by directory', '', 'demo', [], chat_enabled=True, user_id=actor)
    message = store.claim_message(run['id'])
    store.finish_message(run['id'], message['id'], 'Local verification session. Open Files to browse this checkout by folder, search for workspace-panel, and preview its source.')
    store.update_run(run['id'], status='idle')
    archive_path = Path(directory) / 'artifacts' / (run['id'] + '.zip')
    archive_path.parent.mkdir(exist_ok=True)
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in paths:
            if name and (root / name).is_file():
                archive.write(root / name, 'new-files/moyai/' + name)
    print(f"Demo: http://127.0.0.1:8797/#run={run['id']}", flush=True)
    return app


if __name__ == '__main__':
    with TemporaryDirectory(prefix='file-tree-demo-', dir='/workspace') as directory:
        uvicorn.run(demo(directory), host='0.0.0.0', port=8797)
