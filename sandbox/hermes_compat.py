"""Install reviewed compatibility patches before importing the pinned runtime.

Used both in the image build and at startup after restoring an older snapshot.
Already patched files are left alone; incompatible sources fail before tools run.
"""
from pathlib import Path
import subprocess

PATCH_NAMES = ('hermes-steering.patch', 'hermes-stop-reason.patch')


def apply_hermes_patches(source=Path('/opt/hermes')):
    pending = []
    for name in PATCH_NAMES:
        patch = Path(__file__).parent / name
        command = ['git', '-C', str(source), 'apply']
        if subprocess.run([*command, '--reverse', '--check', str(patch)], capture_output=True).returncode == 0:
            continue
        check = subprocess.run([*command, '--check', str(patch)], capture_output=True)
        if check.returncode != 0:
            raise RuntimeError(f'Hermes compatibility patch {patch.name} does not match this runtime. '
                               'Rebuild the environment with the supported Hermes revision.')
        pending.append(str(patch))
    if pending:
        subprocess.run(['git', '-C', str(source), 'apply', *pending], check=True, capture_output=True)


if __name__ == '__main__':
    apply_hermes_patches()
