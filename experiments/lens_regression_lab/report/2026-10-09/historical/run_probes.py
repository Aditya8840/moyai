"""Run repository proof scripts without changing production source.

From a Moyai checkout with its native SDK dependencies installed:
    cd /path/to/moyai
    python /path/to/report/historical/run_probes.py yield

The repository defaults to the current directory. Set MOYAI_LAB_REPO to use a
different checkout. Results are written beside this script; Git history must
include the historical revisions used by each probe.

The old Codex adapters predate a report-only transport_attempt attribute.
Defaulting that field to zero allows today's result serializer to read them;
the historical adapter's execution methods are unchanged.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(os.environ.get('MOYAI_LAB_REPO', Path.cwd())).expanduser().resolve()
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('probe', choices=['yield', 'compaction', 'claude-buffer'])
    args = parser.parse_args()
    if args.probe == 'claude-buffer':
        module = load('claude_buffer_proof', ROOT / 'scripts/claude_buffer_smoke.py')
        proofs = module.main()
        (OUT / 'claude-buffer.json').write_text(json.dumps({
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'inference': 'scripted loopback; real Claude SDK and native image Read',
            'proofs': proofs,
        }, indent=2) + '\n')
        return
    module = load('codex_yield_proof', ROOT / 'scripts/codex_yield_demo.py')
    original = module.native_yield_case

    def compatible(*args, agent_class, **kwargs):
        if not hasattr(agent_class, 'transport_attempt'):
            agent_class.transport_attempt = 0
        return original(*args, agent_class=agent_class, **kwargs)

    module.native_yield_case = compatible
    module.demonstrate('e70f696' if args.probe == 'compaction' else '1500076',
                       OUT / args.probe, context=args.probe == 'compaction')


if __name__ == '__main__':
    main()
