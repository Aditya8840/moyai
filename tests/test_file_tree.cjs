const assert=require('node:assert/strict');
const {test}=require('node:test');
const {fileTree,renderFileTree}=require('../app/static/workspace-panel.js');
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const file=path=>({path,name:path.split('/').at(-1),archive_path:'new-files/'+path,size:12});
const render=(files,opts={})=>renderFileTree(files,{escape,size:n=>n+' B',...opts});

test('groups shared directories, retains full paths and duplicate basenames',()=>{
  const files=['repo/app/main.py','repo/tests/main.py','README.md','repo/app/config.py'].map(file);
  const tree=fileTree(files),repo=tree.directories.get('repo');
  assert.equal(tree.files[0].file,files[2]);
  assert.equal(repo.directories.get('app').files.length,2);
  assert.equal(repo.directories.get('tests').files[0].index,1);
  assert.equal(repo.directories.get('app').path,'repo/app');
});
test('folders precede files and labels are alphabetically sorted without changing click indexes',()=>{
  const html=render(['z.txt','b/z.py','a/b.py','a/a.py','a.txt'].map(file));
  assert.ok(html.indexOf('data-folder="a"')<html.indexOf('data-folder="b"'));
  assert.ok(html.indexOf('data-folder="b"')<html.indexOf('data-file="4"'));
  assert.ok(html.indexOf('data-file="3"')<html.indexOf('data-file="2"'));
  assert.ok(html.indexOf('data-file="4"')<html.indexOf('data-file="0"'));
});
test('folders are collapsed by default, saved expansion opens only chosen paths',()=>{
  const files=['repo/app/main.py','repo/tests/test.py'].map(file);
  assert.equal((render(files).match(/ open>/g)||[]).length,0);
  const expanded=new Set(['repo','repo/app']);
  assert.equal((render(files,{expanded}).match(/ open>/g)||[]).length,2);
  assert.equal((render(files,{expanded,search:true}).match(/ open>/g)||[]).length,3);
  assert.deepEqual([...expanded],['repo','repo/app']);
});
test('untrusted names are escaped and prototype-like folders are ordinary paths',()=>{
  const html=render(['__proto__/constructor/a.txt','<img onerror="bad">/x&y.txt'].map(file));
  assert.ok(html.includes('data-folder="__proto__/constructor"'));
  assert.ok(html.includes('&lt;img onerror=&quot;bad&quot;&gt;'));
  assert.ok(html.includes('x&amp;y.txt'));assert.ok(!html.includes('<img'));
});
test('handles empty catalogs, root files, capture icons and duplicate display paths',()=>{
  assert.match(render([]),/No matching saved files/);
  const files=[file('result.md'),file('result.md'),{...file('captures/video.webm'),kind:'video'},{...file('captures/image.png'),kind:'image'}];
  const html=render(files);assert.equal((html.match(/data-file=/g)||[]).length,4);
  assert.ok(html.includes('▷'));assert.ok(html.includes('▧'));
});
test('1000 files in one folder remain a single collapsed top-level directory',()=>{
  const html=render(Array.from({length:1000},(_,i)=>file('repo/app/file-'+i+'.py')));
  assert.equal((html.match(/<details/g)||[]).length,2);
  assert.equal((html.match(/data-file=/g)||[]).length,1000);
  assert.equal((html.match(/ open>/g)||[]).length,0);
});
