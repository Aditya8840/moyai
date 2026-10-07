const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/app.js','utf8');
const functions=source.slice(source.indexOf('function harnessLogo('),source.indexOf('\nfunction setSidebar('));
function setup(){
  const context={state:{config:{harnesses:[{id:'hermes',name:'Hermes'},{id:'claude-agent-sdk',name:'Claude Code',model_prefix:'anthropic/claude-'}],models:[{id:'openai/gpt-6-astra',name:'Astra'},{id:'anthropic/claude-opus-5-5',name:'Opus'}]}},esc:s=>String(s),MoyaiProviderLogos:require('../app/static/provider-logos.js')};
  vm.createContext(context);vm.runInContext(functions,context);return context;
}
test('harness picker defaults to Hermes and retains an explicit Claude choice',()=>{
  const c=setup();assert.match(c.harnessPicker(),/value="hermes" selected/);
  assert.match(c.harnessPicker('claude-agent-sdk'),/value="claude-agent-sdk" selected/);
  assert.match(c.harnessPicker(),/aria-label="Agent harness"/);
});
test('Claude harness offers only Claude models without changing Hermes choices',()=>{
  const c=setup();assert.equal(c.harnessModels('hermes').length,2);
  assert.equal(c.harnessModels('claude-agent-sdk').length,1);
  const html=c.modelPicker('model','anthropic/claude-opus-5-5',false,'claude-agent-sdk');
  assert.match(html,/Opus/);assert.doesNotMatch(html,/Astra/);
});
test('harness picker shows the selected harness logo and hides it for harnesses without one',()=>{
  const c=setup();
  assert.match(c.harnessPicker('claude-agent-sdk'),/<img class="provider-logo harness-logo"[^>]*src="\/static\/harness-logos\/claude-code.svg"/);
  assert.match(c.harnessPicker('hermes'),/<img class="provider-logo harness-logo"[^>]*hidden>/);
});
