const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {test}=require('node:test');
const vm=require('node:vm');
const script=readFileSync('app/static/app.js','utf8');
const slice=(start,end)=>script.slice(script.indexOf(start),script.indexOf(end));
function harness(){
  const nodes=new Map();
  const node=key=>{if(!nodes.has(key))nodes.set(key,{value:'',textContent:'',innerHTML:'',scrollTop:31,querySelector(){return null;}});return nodes.get(key);};
  const state={selected:'parent',runs:[],folders:[],organization:{},runsRefresh:0,expandedParents:new Set(['parent']),closedFolders:new Set(),drafts:{parent:'Unsent reply'}};
  const panelUpdates=[];
  const context={state,URL,URLSearchParams,ico:()=>'',glyph:{},location:{hash:''},$:node,relative:()=> '2m ago',document:{activeElement:null},workspacePanel:{syncTitles:rows=>panelUpdates.push(rows)},sessionFolderIcon:'',CSS:{escape:x=>x}};
  vm.createContext(context);
  vm.runInContext(slice('const esc =','const state =')+slice('function sessionTitle(','function modelName(')+slice('function sidebarGroups(','function setView(')+slice('async function refreshRuns(','async function renderHome('),context);
  return {context,state,node,panelUpdates};
}

test('explicit labels win over persisted titles; malformed and blank values fall through',()=>{
  const {context:h}=harness();
  assert.equal(h.sessionTitle({agent_label:'  API\nworker  ',display_title:'Generated',prompt:'Raw'}),'API worker');
  assert.equal(h.sessionTitle({agent_label:' \n ',display_title:'  Readable\n task ',prompt:'Raw'}),'Readable task');
  assert.equal(h.sessionTitle({agent_label:[],display_title:{},prompt:'- fix the test'}),'Fix the test');
  assert.equal(h.sessionTitle({}),'Untitled session');
});

test('fallback cleans bullets, links, whitespace and conversational preambles without rewriting identifiers',()=>{
  const {context:h}=harness();
  const cases=[
    ['- we wanna fix the sidebar','Fix the sidebar'],
    ['\nhttps://example.com/context\n- [ ] please fix `display_title`','Fix display_title'],
    ['## **Investigate outage**','Investigate outage'],
    ['1. could you check [the logs](https://example.com/logs)','Check the logs'],
    ['- fix /api/runs and foo_bar','Fix /api/runs and foo_bar'],
    ['https://github.com/org/repo','Untitled session'],
    ['\n\n-\n','Untitled session'],
    ['- 修复会话标题','修复会话标题'],
  ];
  for(const [prompt,expected] of cases)assert.equal(h.sessionTitle({prompt}),expected,prompt);
  assert.ok(h.sessionTitle({prompt:'Fix '+ 'very long request '.repeat(40)}).length<=96);
  assert.match(h.sessionTitle({prompt:'Fix '+ 'very long request '.repeat(40)}),/…$/);
});

test('renamed parents and workers stay searchable by original text, URL, typo, and generated title',()=>{
  const {context:h}=harness();
  const child={id:'child',agent_label:'API worker',display_title:'Repair retry behavior',prompt:'- invsetigate\nhttps://example.com/errors'};
  const runs=[{id:'parent',display_title:'Improve session navigation',prompt:'- we wanna tidy this\noriginal detail',children:[child,{id:'sibling',prompt:'Other work'}]}];
  for(const term of ['navigation','wanna','original detail'])assert.equal(h.sidebarGroups(runs,term)[0].children.length,2);
  for(const term of ['API worker','retry behavior','invsetigate','example.com/errors']){
    const found=h.sidebarGroups(runs,term);assert.equal(found[0].id,'parent');assert.equal(found[0].children[0].id,'child');assert.equal(found[0].children.length,1);assert.equal(found[0].totalChildren,2);
  }
});

test('sidebar escapes titles and exposes real status, timestamps, repository, selection and demo context',()=>{
  const {context:h}=harness();
  const row=h.sidebarRow({id:'parent',display_title:'Fix <img src=x onerror="bad()">',status:'waiting_credential',repo_url:'https://github.com/org/repo.git',mode:'demo',updated_at:'2026-10-06T12:00:00Z'});
  assert.doesNotMatch(row,/<img|pull request|PR #|merged/i);
  assert.match(row,/Fix &lt;img/);assert.match(row,/Needs access/);assert.match(row,/Demo · org\/repo/);assert.match(row,/2m ago/);
  assert.match(row,/aria-current="page"/);assert.match(row,/aria-label="Fix/);assert.match(row,/draggable="true" data-drag-session="parent"/);
  const child=h.sidebarRow({id:'worker',agent_label:'Worker',status:'idle',created_at:'broken'},true);
  assert.match(child,/Ready/);assert.match(child,/Agent/);assert.doesNotMatch(child,/draggable|Invalid Date|NaN/);
  assert.match(h.sidebarRow({id:'unknown'}),/Status unknown/);
  assert.equal(h.sessionRepository({repo_url:'javascript:alert(1)'}),'');
  assert.equal(h.sessionRepository({repo_url:'https://github.com.evil.test/org/repo'}),'');
});

test('background list poll updates header, sidebar, search and side tabs without changing conversation state',async()=>{
  const {context:h,state,node,panelUpdates}=harness();
  const original={id:'parent',prompt:'- we wanna tidy things',status:'idle',children:[]};
  state.runs=[original];state.chatRun={...original,messages:[{content:'Conversation stays put'}]};
  h.renderSidebar();assert.match(node('#session-list').innerHTML,/Tidy things/);
  const messages=state.chatRun.messages;
  h.api=async path=>path.startsWith('/api/runs')?[{...original,display_title:'Polish session navigation'}]:{folders:[]};
  await h.refreshRuns();
  assert.equal(node('#page-title').textContent,'Polish session navigation');
  assert.match(node('#session-list').innerHTML,/Polish session navigation/);
  assert.equal(state.chatRun.display_title,'Polish session navigation');assert.equal(state.chatRun.messages,messages);
  assert.equal(state.drafts.parent,'Unsent reply');assert.equal(state.selected,'parent');assert.equal(state.expandedParents.has('parent'),true);
  assert.equal(node('#session-list').scrollTop,31);assert.equal(panelUpdates.length,2);
  node('#session-search').value='wanna';h.renderSidebar();assert.match(node('#session-list').innerHTML,/Polish session navigation/);
  node('#session-search').value='navigation';h.renderSidebar();assert.match(node('#session-list').innerHTML,/Polish session navigation/);
});

test('detail refresh updates a title with unchanged status and retains child labels',()=>{
  const {context:h,state,node}=harness();
  state.runs=[{id:'parent',prompt:'Old',status:'idle',children:[{id:'child',agent_label:'Worker assignment',status:'idle'}]}];
  h.syncRunSummary({id:'parent',display_title:'New title',status:'idle'});
  assert.equal(node('#page-title').textContent,'New title');assert.match(node('#session-list').innerHTML,/New title/);
  state.selected='child';h.syncRunSummary({id:'child',display_title:'Generated child title',status:'idle'});
  assert.equal(node('#page-title').textContent,'Worker assignment');
});

test('late and overlapping list polls do not rename another view or apply an older response',async()=>{
  const {context:h,state,node}=harness();const pending=[];
  h.api=path=>path.startsWith('/api/runs')?new Promise(resolve=>pending.push(resolve)):Promise.resolve({folders:[]});
  const first=h.refreshRuns(),second=h.refreshRuns();
  pending[1]([{id:'parent',display_title:'Newest'}]);await second;
  pending[0]([{id:'parent',display_title:'Stale'}]);await first;
  assert.equal(node('#page-title').textContent,'Newest');assert.equal(state.runs[0].display_title,'Newest');
  const third=h.refreshRuns();state.selected=null;node('#page-title').textContent='Settings';
  pending[2]([{id:'parent',display_title:'Later'}]);await third;
  assert.equal(node('#page-title').textContent,'Settings');
});

test('polling during folder drag defers list replacement but updates titles after drag ends',async()=>{
  const {context:h,state,node}=harness();
  state.runs=[{id:'parent',prompt:'Old title',status:'idle'}];h.renderSidebar();const old=node('#session-list').innerHTML;
  state.draggedSessionId='parent';h.api=async path=>path.startsWith('/api/runs')?[{id:'parent',display_title:'New title',status:'idle'}]:{folders:[]};
  await h.refreshRuns();assert.equal(node('#session-list').innerHTML,old);assert.equal(node('#page-title').textContent,'New title');
  state.draggedSessionId=null;h.renderSidebar();assert.match(node('#session-list').innerHTML,/New title/);
});

test('side-chat titles and menu search refresh without replacing tabs or drafts',()=>{
  const panel=readFileSync('app/static/workspace-panel.js','utf8'),{context:h}=harness();
  const tab={kind:'chat',chatId:'side',title:'Old',draft:'Keep this draft',element:{}};
  const nodes=new Map(),q=selector=>{if(!nodes.has(selector))nodes.set(selector,{value:'',hidden:false,querySelectorAll:()=>[]});return nodes.get(selector);};
  let draws=0,saves=0;
  Object.assign(h,{tabs:new Map([['side',tab]]),sideChats:[{id:'side',prompt:'original typo serch',display_title:'Old'}],disposed:false,titleFor:h.sessionTitle,matchesSession:h.sessionMatches,run:{},q,draw:()=>draws++,save:()=>saves++});
  vm.runInContext(panel.slice(panel.indexOf('function renderMenuItems(items)'),panel.indexOf('initial.tabs.forEach'))+panel.slice(panel.indexOf('drawMenu=function()'),panel.indexOf("q('.panel-menu input').oninput=drawMenu;",panel.indexOf('drawMenu=function()'))),h);
  h.syncTitles([{id:'side',display_title:'Readable side title'}]);
  assert.equal(tab.title,'Readable side title');assert.equal(tab.draft,'Keep this draft');assert.equal(h.tabs.get('side'),tab);assert.equal(draws,1);assert.equal(saves,1);
  for(const term of ['serch','Readable']){q('.panel-menu input').value=term;h.drawMenu();assert.match(q('[data-menu-items]').innerHTML,/Readable side title/);}
  h.syncTitles([{id:'side',display_title:'Readable side title'}]);assert.equal(draws,1);
  h.disposed=true;h.syncTitles([{id:'side',display_title:'Stale title'}]);assert.equal(tab.title,'Readable side title');
});
