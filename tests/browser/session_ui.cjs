// Real Chromium regression coverage. Start scripts/session_ui_demo.py first.
// With Playwright installed: node --test tests/browser/session_ui.cjs
// Uses real session APIs + demo execution; only failure/delay cases intercept requests.
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {chromium}=require('playwright');
const base=process.env.SESSION_UI_URL||'http://127.0.0.1:8830';
async function setup(t){
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(()=>browser.close());
  const page=await browser.newPage();
  await page.goto(base+'/demo/login');
  await page.locator('#prompt').waitFor();
  return page;
}
async function home(page){
  await page.locator('#new-task').click();
  await page.locator('#prompt').waitFor();
}
async function send(page,text,method='Enter'){
  await page.locator('#prompt').fill(text);
  if(method==='Enter')await page.locator('#prompt').press('Enter');
  else await page.getByRole('button',{name:'Start session',exact:true}).click();
  await page.locator('#followup').waitFor();
}

test('sender emails are visible for self and teammates, on desktop and mobile',async t=>{
  const page=await setup(t);
  await send(page,'Verify my sender email');
  const label=page.locator('.chat-message.user .message-label').first();
  assert.equal(await label.isVisible(),true);
  assert.match(await label.textContent(),/alex@example.com/);
  await page.reload();await page.locator('#followup').waitFor();
  assert.match(await label.textContent(),/alex@example.com/);
  const runs=await page.evaluate(()=>api('/api/runs?scope=all'));
  const teammate=runs.find(r=>r.prompt==='Local verification: sender labels');
  assert.ok(teammate);
  await page.evaluate(id=>openRun(id),teammate.id);
  assert.equal(await label.isVisible(),true);
  assert.match(await label.textContent(),/sam@example.com/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await label.isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  // Exercise the real renderer's missing-identity fallback and escaping.
  await page.evaluate(()=>{const r=structuredClone(state.chatRun);r.messages[0].user_name='<img src=x onerror=alert(1)>@example.com';updateChat(r);});
  assert.equal(await label.locator('img').count(),0);
  assert.match(await label.textContent(),/<img src=x/);
  await page.evaluate(()=>{const r=structuredClone(state.chatRun);delete r.messages[0].user_name;updateChat(r);});
  assert.match(await label.textContent(),/Earlier message/);
});

for(const method of ['Enter','button'])test(`successful ${method} submission clears the input when returning home`,async t=>{
  const page=await setup(t),text=`Clear submitted text via ${method}`;
  await send(page,text,method);
  assert.equal(await page.locator('.chat-message.user .message-content').first().textContent(),text);
  await home(page);
  assert.equal(await page.locator('#prompt').inputValue(),'');
});

test('unsent drafts and rejected submissions retain text; retry clears only on success',async t=>{
  const page=await setup(t);
  await page.locator('#prompt').fill('Keep this draft until accepted');
  await page.locator('.nav-button[data-view="settings"]').click();
  await home(page);
  assert.equal(await page.locator('#prompt').inputValue(),'Keep this draft until accepted');
  const reject=route=>route.request().method()==='POST'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Test rejection: retry safely'})}):route.continue();
  await page.route('**/api/runs',reject);
  await page.locator('#prompt').press('Enter');
  await page.locator('#toast').filter({hasText:'Test rejection'}).waitFor();
  assert.equal(await page.locator('#prompt').inputValue(),'Keep this draft until accepted');
  await page.unroute('**/api/runs',reject);
  await page.locator('#prompt').press('Enter');await page.locator('#followup').waitFor();
  await home(page);
  assert.equal(await page.locator('#prompt').inputValue(),'');
});

test('text typed during a pending create is not discarded',async t=>{
  const page=await setup(t);
  let release,arrived;
  const waiting=new Promise(resolve=>arrived=resolve);
  const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/runs',async route=>{
    if(route.request().method()==='POST'){arrived();await gate;}
    await route.continue();
  });
  await page.locator('#prompt').fill('First request');await page.locator('#prompt').press('Enter');
  await waiting;
  await page.locator('#prompt').fill('Different next request');release();
  await page.locator('#followup').waitFor();await home(page);
  assert.equal(await page.locator('#prompt').inputValue(),'Different next request');
});

test('accepted creation clears text even if subsequent sidebar refresh fails',async t=>{
  const page=await setup(t);
  await page.route('**/api/runs?*',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Test sidebar unavailable'})}));
  await page.locator('#prompt').fill('Accepted despite sidebar outage');await page.locator('#prompt').press('Enter');
  await page.locator('#toast').filter({hasText:'Test sidebar unavailable'}).waitFor();
  assert.equal(await page.locator('#prompt').inputValue(),'');
  await page.unroute('**/api/runs?*');
  await home(page);assert.equal(await page.locator('#prompt').inputValue(),'');
});
