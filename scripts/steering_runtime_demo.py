"""Local viewer for the real-runtime regression; no cloud or model requests.

Set HERMES_TEST_SOURCE and HERMES_TEST_PYTHON, then:
    uv run python scripts/steering_runtime_demo.py
Open http://127.0.0.1:8830 and run the checks. Results come from the subprocess,
not canned UI data. The model boundary is scripted; Hermes and file tools run.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock, Thread

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, '-m', 'pytest', '-q', '-s',
           'tests/test_hermes_steering.py', 'tests/test_goals.py']
LOCK = Lock()
STATE = {'status': 'Ready', 'results': [], 'log': '', 'summary': 'No checks run yet.'}

PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Moyai · Steering verification</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f7f6fb;color:#23202e;font:16px system-ui}
main{max-width:1020px;margin:36px auto;padding:0 28px}header{display:flex;align-items:center;gap:12px;color:#66518f;font-weight:650}
h1{font-size:36px;letter-spacing:-1px;margin:16px 0 8px}p{color:#676171;line-height:1.55;margin:8px 0}
button{font:inherit;background:#5b3fd1;border:0;color:white;border-radius:8px;padding:12px 20px;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}.actions{display:flex;align-items:center;gap:18px;margin:24px 0}
table{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden;font-size:15px}
th,td{padding:15px 18px;text-align:left;border-bottom:1px solid #eeebf5}th{font-size:12px;letter-spacing:.7px;color:#746e81;text-transform:uppercase}
td:last-child{color:#56505f}#summary{margin:20px 0;padding:18px;border-left:4px solid #5b3fd1;background:#eeebfa;font-weight:650}
code{font:12px ui-monospace,monospace}details{color:#676171;margin-top:18px}pre{font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;max-height:240px;overflow:auto;background:#fff;padding:16px}
.tag{font-size:12px;border:1px solid #c9c0e5;padding:4px 8px;border-radius:5px}footer{font-size:13px;margin-top:18px;color:#78717f}
</style><main><header>MOYAI DEVIN <span class="tag">LOCAL VERIFICATION</span></header>
<h1>Keep going when the user adds corrections.</h1>
<p>Real Hermes conversation loop and file writes. Scripted model replies.<br>No production sessions, provider requests, or Slack messages.</p>
<div class="actions"><button id="run">Run verification</button><strong id="status">Ready</strong></div>
<table><thead><tr><th>Scenario</th><th>Observed result</th><th>Evidence</th></tr></thead><tbody id="results">
<tr><td>Original runtime</td><td>Waiting</td><td>Reproduce the fourth-correction failure</td></tr>
<tr><td>Patched runtime</td><td>Waiting</td><td>Six corrections and seven file writes</td></tr>
<tr><td>Stop and retry guards</td><td>Waiting</td><td>Check genuine failures still stop</td></tr>
</tbody></table><div id="summary">No checks run yet.</div>
<code>python -m pytest -q -s tests/test_hermes_steering.py tests/test_goals.py</code>
<details><summary>Actual command output</summary><pre id="log"></pre></details>
<footer>Each click runs the checked-out PR code in temporary workspaces. This is a local backend demo.</footer>
</main><script>
const button=document.getElementById('run');
const labels={'redirect-cap':'Consecutive cancellations','provider-failure':'Provider errors','stop':'Explicit Stop','guards':'Stop reason and fallback limit'};
async function refresh(){
 const r=await fetch('/state'); const s=await r.json();
 document.getElementById('status').textContent=s.status;
 document.getElementById('summary').textContent=s.summary;
 document.getElementById('log').textContent=s.log;
 button.disabled=s.status==='Running';
 if(s.results.length){
  const body=document.getElementById('results');body.replaceChildren();
  for(const p of s.results){
   const row=document.createElement('tr');
   const original=p.scenario==='corrections'&&!p.completed;
   const label=p.scenario==='corrections'?(original?'Original runtime':'Patched runtime'):labels[p.scenario];
   const outcome=p.scenario==='corrections'?(original?'Stopped at correction 4':'Completed automatically'):'Guard verified';
   const evidence=p.scenario==='corrections'?`${p.corrections} corrections · ${p.writes} file writes`:
    p.scenario==='redirect-cap'?'Last correction preserved':p.scenario==='provider-failure'?'3 failed attempts · no tool replay':
    p.scenario==='stop'?'Stopped immediately':'Specific cause preserved';
   for(const text of [label,outcome,evidence]){const cell=document.createElement('td');cell.textContent=text;row.append(cell)}
   body.append(row);
  }
 }
}
button.onclick=async()=>{button.disabled=true;await fetch('/run',{method:'POST'});await refresh()};
setInterval(refresh,400);refresh();
</script></html>'''


def run_checks():
    try:
        with subprocess.Popen(COMMAND, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env={**os.environ, 'PYTHONUNBUFFERED': '1'}) as process:
            for line in process.stdout:
                with LOCK:
                    STATE['log'] = (STATE['log'] + line)[-40000:]
                    if line.startswith('STEERING_PROOF '):
                        STATE['results'].append(json.loads(line.removeprefix('STEERING_PROOF ')))
                    if ' passed' in line:
                        STATE['summary'] = line.strip()
            code = process.wait()
        with LOCK:
            STATE['status'] = 'Passed' if code == 0 else 'Failed'
            if code != 0:
                STATE['summary'] = 'A check failed. Open the actual command output below.'
    except Exception as exc:
        with LOCK:
            STATE.update(status='Failed', summary=str(exc))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, content_type='application/json'):
        data = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/':
            self.reply(PAGE, 'text/html; charset=utf-8')
        elif self.path == '/state':
            with LOCK:
                self.reply(STATE)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != '/run':
            self.send_error(404)
            return
        with LOCK:
            if STATE['status'] != 'Running':
                STATE.update(status='Running', results=[], log='', summary='Running real-runtime checks…')
                Thread(target=run_checks, daemon=True).start()
        self.reply({'started': True})


if __name__ == '__main__':
    if not all(os.environ.get(name) for name in ('HERMES_TEST_SOURCE', 'HERMES_TEST_PYTHON')):
        raise SystemExit('Set HERMES_TEST_SOURCE and HERMES_TEST_PYTHON before starting the demo.')
    print('Local steering verification: http://127.0.0.1:8830', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8830), Handler).serve_forever()
