"""Private behavioral checks, executed only after the coding agent stops."""
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, sys.argv[1])
from app.config import MODEL_CATALOG, Settings


rows = []


def check(name, expected, operation):
    try:
        actual = operation()
    except Exception as exc:
        rows.append({'name': name, 'passed': False, 'expected': expected,
                     'error_type': type(exc).__name__})
    else:
        rows.append({'name': name, 'passed': actual == expected,
                     'expected': expected, 'actual': actual})


def selected(model, alias=None, **settings):
    return Settings(_env_file=None, agent_model=model, **settings).default_harness(alias)


check('sol-alias', 'codex', lambda: selected('openai/gpt-6-astra', 'sol'))
check('sol-display-name', 'codex', lambda: selected('openai/gpt-6-astra', 'GPT-6.1 Sol'))
check('future-openai-default', 'codex', lambda: selected('openai/heldout-model-2030'))
check('future-anthropic-default', 'claude-agent-sdk', lambda: selected('anthropic/heldout-model-2030'))
MODEL_CATALOG['openai/heldout-catalog-model'] = 'Held-out catalog model'
check('future-catalog-display-name', 'codex', lambda: selected('custom-gateway', 'Held-out catalog model'))
check('openai-compatible-is-another-provider', 'claude-agent-sdk', lambda: selected('openai-compatible/model'))
check('nested-openai-is-another-provider', 'claude-agent-sdk', lambda: selected('vendor/openai/model'))
check('bare-openai-is-an-alias', 'claude-agent-sdk', lambda: selected('openai'))
check('unprefixed-gateway-model', 'claude-agent-sdk', lambda: selected('my-internal-model'))
check('existing-anthropic-alias', 'claude-agent-sdk', lambda: selected('openai/gpt-6-astra', 'opus'))

for harness in ('hermes', 'claude-agent-sdk', 'codex'):
    check(f'explicit-init-{harness}', harness,
          lambda harness=harness: selected('openai/heldout-model-2030', agent_harness=harness))


def from_environment():
    os.environ['AGENT_HARNESS'] = 'hermes'
    try:
        return selected('openai/heldout-model-2030')
    finally:
        os.environ.pop('AGENT_HARNESS')


def from_dotenv():
    with tempfile.TemporaryDirectory(prefix='moyai-hidden-dotenv-') as directory:
        path = Path(directory) / '.env'
        path.write_text('AGENT_HARNESS=claude-agent-sdk\n')
        return Settings(_env_file=path, agent_model='openai/heldout-model-2030').default_harness()


def unknown_model_rejected():
    try:
        Settings(_env_file=None, agent_model='openai/gpt-6-astra').default_harness('unknown-unconfigured-model')
    except ValueError:
        return True
    return False


check('explicit-environment-wins', 'hermes', from_environment)
check('explicit-dotenv-wins', 'claude-agent-sdk', from_dotenv)
check('unknown-model-still-rejected', True, unknown_model_rejected)
print(json.dumps({'passed': all(row['passed'] for row in rows), 'checks': rows,
                  'passed_checks': sum(row['passed'] for row in rows), 'total_checks': len(rows)}))
