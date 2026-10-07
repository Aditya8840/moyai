/* Local browser fixture, not a backend or generated-title integration test.
 * Run: node tests/preview_session_titles.cjs --serve
 * The button applies a fixture API response through the real refreshRuns().
 */
if(process.argv.includes('--serve')){
  const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
  const root=path.resolve(__dirname,'../app/static');
  let titled=false;
  const parent='a'.repeat(32),child='b'.repeat(32),side='c'.repeat(32);
  const run=(id,prompt,status,extra={})=>({id,prompt,status,mode:'demo',chat_enabled:true,parent_run_id:'',plugins:[],events:[],messages:[],approvals:[],credential_requests:[],repo_url:'https://github.com/example/project',updated_at:'2026-10-06T12:00:00Z',...extra});
  const rows=()=>[
    run(parent,'- we wanna polish the session list\nhttps://example.com/original-context','waiting_children',{display_title:titled?'Polish session navigation':'',folder_id:'work',children:[run(child,'check sidebar behavior','running',{parent_run_id:parent,agent_label:'Verify sidebar behavior',display_title:'Generated worker title'})]}),
    run(side,'- can you explain the title refresh?','idle',{side_chat_of:parent,display_title:titled?'Explain background title refresh':'',children:[]}),
    run('d'.repeat(32),'## **Investigate upload errors**','waiting_credential',{children:[]}),
    run('e'.repeat(32),'https://example.com/task\n- fix `display_title` fallback','completed',{children:[]}),
  ];
  const send=(response,status,body,type='application/json')=>{response.writeHead(status,{'Content-Type':type});response.end(type==='application/json'?JSON.stringify(body):body);};
  const server=http.createServer((request,response)=>{
    const url=new URL(request.url,'http://localhost');
    if(url.pathname==='/'){
      let html=fs.readFileSync(path.join(root,'index.html'),'utf8');
      html=html.replace('</body>',`<button id="fixture-poll" style="position:fixed;right:20px;bottom:20px;z-index:99">Test fixture: deliver generated titles</button><script>document.addEventListener('DOMContentLoaded',()=>{document.querySelector('#fixture-poll').onclick=async()=>{await fetch('/fixture/title',{method:'POST'});await refreshRuns();document.querySelector('#fixture-poll').textContent='Fixture API poll applied';};});</script></body>`);
      return send(response,200,html,'text/html');
    }
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,url.pathname.slice(8));
      if(!file.startsWith(root+path.sep)||!fs.existsSync(file))return send(response,404,{});
      const type={'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)]||'application/octet-stream';
      return send(response,200,fs.readFileSync(file),type);
    }
    if(url.pathname==='/fixture/title'){titled=true;return send(response,200,{fixture:true});}
    if(url.pathname==='/api/session')return send(response,200,{authenticated:true,local:true,csrf:'fixture',role:'admin',user_id:'fixture'});
    if(url.pathname==='/api/config')return send(response,200,{missing:[],models:[],cloud_ready:false});
    if(url.pathname==='/api/organization')return send(response,200,{name:'Frontend test fixture'});
    if(url.pathname==='/api/session-folders')return send(response,200,{folders:[{id:'work',name:'In progress'}]});
    if(url.pathname==='/api/runs')return send(response,200,rows());
    if(url.pathname==='/api/connections'||url.pathname==='/api/environments')return send(response,200,[]);
    if(url.pathname.endsWith('/side-chats'))return send(response,200,[rows()[1]]);
    if(url.pathname.endsWith('/files'))return send(response,200,{files:[]});
    if(url.pathname.endsWith('/events')){response.writeHead(200,{'Content-Type':'text/event-stream'});response.write(': fixture stream\n\n');return;}
    if(url.pathname.startsWith('/api/runs/')){
      const id=url.pathname.split('/')[3],item=rows().flatMap(row=>[row,...(row.children||[])]).find(row=>row.id===id);
      if(item)return send(response,200,item);
    }
    return send(response,404,{detail:'Not provided by the local frontend fixture'});
  });
  server.listen(8766,'0.0.0.0',()=>console.log('Frontend fixture ready on port 8766; API data is synthetic test input.'));
}
