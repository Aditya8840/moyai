const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const logos=require('../app/static/provider-logos.js');
const config=fs.readFileSync(path.join(__dirname,'..','app','config.py'),'utf8');
const catalog=[...config.match(/MODEL_CATALOG[^{]*\{([^}]*)\}/)[1].matchAll(/'([^']+)':/g)].map(m=>m[1]);
test('every picker model resolves to a logo file that ships with the app',()=>{
  assert.ok(catalog.length>0);
  for(const model of catalog){
    const src=logos.src(model);
    assert.ok(src,`no logo for ${model}`);
    assert.ok(fs.existsSync(path.join(__dirname,'..','app',src)),`${src} is missing`);
  }
});
test('provider prefix decides the logo and unknown providers get none',()=>{
  assert.equal(logos.src('azure/gpt-x'),logos.src('azure_ai/gpt-x'));
  assert.notEqual(logos.src('openai/gpt-x'),logos.src('anthropic/gpt-x'));
  assert.equal(logos.src('Anthropic/claude'),logos.src('anthropic/claude'));
  assert.equal(logos.src('my-custom-model'),null);
  assert.equal(logos.src(''),null);
});
test('sync hides the logo for unknown providers and shows it again on switch',()=>{
  const img={hidden:false,src:'stale',removeAttribute(key){delete this[key];}};
  logos.sync(img,'custom/model');
  assert.equal(img.hidden,true);
  assert.equal(img.src,undefined);
  logos.sync(img,'fireworks_ai/glm');
  assert.equal(img.hidden,false);
  assert.equal(img.src,logos.src('fireworks_ai/glm'));
});
