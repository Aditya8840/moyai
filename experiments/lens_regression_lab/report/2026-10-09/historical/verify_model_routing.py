"""Execute actual historical default_harness methods without model calls.

From a Moyai checkout with its Python dependencies installed:
    cd /path/to/moyai
    python /path/to/report/historical/verify_model_routing.py

Set MOYAI_LAB_REPO to use a different checkout. Git history must include
5fd2114 and its parent. Results are written beside this script.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ.get('MOYAI_LAB_REPO', Path.cwd())).expanduser().resolve()
sys.path.insert(0, str(ROOT))
from app.config import Settings

rows = []
for ref in ['5fd2114^', '5fd2114', 'HEAD']:
    sha = subprocess.check_output(['git', 'rev-parse', ref], cwd=ROOT, text=True).strip()
    source = subprocess.check_output(['git', 'show', ref + ':app/config.py'], cwd=ROOT, text=True)
    tree = ast.parse(source)
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Settings')
    function = next(node for node in settings.body if isinstance(node, ast.FunctionDef) and node.name == 'default_harness')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'historical_default_harness.py', 'exec'), namespace)
    historical = type('HistoricalSettings', (Settings,), {'default_harness': namespace['default_harness']})
    instance = historical(_env_file=None, agent_model='openai/gpt-6.1-sol')
    rows.append({'revision': sha, 'reference': ref, 'model': instance.resolve_model(),
                 'effective_harness': instance.default_harness(),
                 'explicit_override': 'agent_harness' in instance.model_fields_set})
assert rows[0]['effective_harness'] == 'claude-agent-sdk'
assert rows[1]['effective_harness'] == rows[2]['effective_harness'] == 'codex'
report = {'measurement': 'Actual historical default_harness method, current Settings context; no model inference',
          'expected_harness': 'codex', 'cases': rows,
          'limitation': 'Confirms routing defect only, not the relative quality of model/SDK pairings'}
(Path(__file__).parent / 'model-routing.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
