"""Run using python -m experiments.lens_regression_lab.offline --output PATH."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile

from evals.test_moyai import verify_solution
from .mutations import BASELINES, MUTATIONS


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_REVISION = 'ffcc83e054d45242a8a1a6465d1f775761ad6452'
ORIGINAL_FIXTURE = Path(__file__).with_name('fixtures') / 'coding_cases_ffcc83e.json'
ORIGINAL_FIXTURE_SHA256 = 'a5d1b87395b77552bbb0a3f67521dd95a8af46d36ec794a798a8e5e55f909a29'


def original_cases():
    data = ORIGINAL_FIXTURE.read_bytes()
    if hashlib.sha256(data).hexdigest() != ORIGINAL_FIXTURE_SHA256:
        raise ValueError('Original regression fixture changed; the historical comparison is invalid.')
    return json.loads(data)['cases']


def run_matrix():
    fixture = ORIGINAL_FIXTURE
    cases = {case['meta']['name']: case for case in original_cases()}
    strengthened_fixture = ROOT / 'evals' / 'coding_cases.json'
    strengthened = {case['meta']['name']: case for case in json.loads(strengthened_fixture.read_text())['cases']}
    rows = []
    controls = []
    with tempfile.TemporaryDirectory(prefix='moyai-verifier-mutations-') as directory:
        workspace = Path(directory)
        for name, solution in BASELINES.items():
            (workspace / 'solution.py').write_text(solution)
            code = cases[name]['meta']['verification_code']
            controls.append({'case': name, 'current_passed': verify_solution(workspace, code)['passed'],
                             'stronger_passed': verify_solution(workspace, strengthened[name]['meta']['verification_code'])['passed']})
        for mutation in MUTATIONS:
            (workspace / 'solution.py').write_text(mutation.solution)
            code = cases[mutation.case]['meta']['verification_code']
            rows.append({'mutation': mutation.id, 'case': mutation.case, 'defect': mutation.defect,
                         'solution_sha256': hashlib.sha256(mutation.solution.encode()).hexdigest(),
                         'current_detected': not verify_solution(workspace, code)['passed'],
                         'stronger_detected': not verify_solution(workspace, strengthened[mutation.case]['meta']['verification_code'])['passed']})
    return {'experiment': 'handcrafted-output-mutation-sensitivity',
            'model_calls': 0,
            'warning': 'A deterministic verifier experiment, not agent/model execution or estimated real-world recall. Additional checks were selected after identifying these gaps; validate on held-out mutations next.',
            'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'original_fixture_revision': ORIGINAL_REVISION,
            'verifier_labels': {'current': 'Original smoke checks at ffcc83e',
                                'stronger': 'Checked-out strengthened production checks'},
            'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
            'stronger_fixture_sha256': hashlib.sha256(strengthened_fixture.read_bytes()).hexdigest(),
            'python': platform.python_version(), 'created_at': datetime.now(timezone.utc).isoformat(),
            'controls': controls, 'mutations': rows,
            'summary': {'known_bad_outputs': len(rows),
                        'current_detected': sum(row['current_detected'] for row in rows),
                        'stronger_detected': sum(row['stronger_detected'] for row in rows),
                        'correct_controls_current_passed': sum(row['current_passed'] for row in controls),
                        'correct_controls_stronger_passed': sum(row['stronger_passed'] for row in controls)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run_matrix()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
