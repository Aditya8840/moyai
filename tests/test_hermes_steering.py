"""Integration against the real Hermes pin and the patches installed in Modal."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from app.config import Settings
from sandbox.hermes_compat import apply_hermes_patches

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def runtime(tmp_path_factory):
    source, python = os.environ.get('HERMES_TEST_SOURCE'), os.environ.get('HERMES_TEST_PYTHON')
    if not source or not python:
        pytest.skip('Set HERMES_TEST_SOURCE and HERMES_TEST_PYTHON for real Hermes steering tests')
    source, python = str(Path(source).resolve()), str(Path(python).absolute())
    revision = subprocess.check_output(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == Settings.model_fields['hermes_revision'].default
    # Patch a private checkout, never the caller's development tree.
    directory = tmp_path_factory.mktemp('hermes')
    original, target = directory / 'original', directory / 'patched'
    for checkout in (original, target):
        subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', source, str(checkout)], check=True)
        subprocess.run(['git', '-C', str(checkout), 'checkout', '--quiet', '--detach', revision], check=True)
    apply_hermes_patches(target)
    # Snapshots created from an already patched image must resume safely too.
    apply_hermes_patches(target)
    return str(original), str(target), python


@pytest.mark.parametrize('scenario,unpatched', [
    ('corrections', True), ('corrections', False), ('redirect-cap', False),
    ('provider-failure', False), ('stop', False), ('guards', False),
])
def test_real_hermes_steering(runtime, tmp_path, scenario, unpatched):
    original, patched, python = runtime
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'config.yaml').write_text(json.dumps({
        'terminal': {'backend': 'local', 'cwd': str(tmp_path)},
        'tools': {'tool_search': {'enabled': 'off'}},
        'display': {'file_mutation_footer': False},
        'agent': {'auto_recovery_cycles': 0},
    }))
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
    env.update(HERMES_HOME=str(profile), PYTHONPATH=original if unpatched else patched,
               HERMES_RUNTIME_DIR=str(tmp_path / 'runtimes'))
    command = [python, str(ROOT / 'tests/hermes_steering_probe.py'), scenario]
    if unpatched:
        command.append('--unpatched')
    result = subprocess.run(command, env=env, cwd=tmp_path, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = next(line for line in result.stdout.splitlines() if line.startswith('STEERING_PROOF '))
    print(result.stdout)
    assert json.loads(proof.removeprefix('STEERING_PROOF '))['scenario'] == scenario


def test_incompatible_snapshot_is_rejected_before_any_patch_is_applied(runtime, tmp_path):
    original, _, _ = runtime
    (tmp_path / 'agent').mkdir()
    for name in ('conversation_loop.py', 'turn_iteration_prep.py', 'turn_finalizer.py'):
        shutil.copyfile(Path(original) / 'agent' / name, tmp_path / 'agent' / name)
    # A runtime that cannot accept the second patch must not get half upgraded.
    (tmp_path / 'agent/turn_finalizer.py').write_text('# Incompatible runtime\n')
    before = {path.name: path.read_bytes() for path in (tmp_path / 'agent').iterdir()}
    with pytest.raises(RuntimeError, match='does not match this runtime'):
        apply_hermes_patches(tmp_path)
    assert {path.name: path.read_bytes() for path in (tmp_path / 'agent').iterdir()} == before
