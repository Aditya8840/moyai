const $ = (s) => document.querySelector(s);
const esc = (s = '') => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {view:'tasks', runs:[], config:{missing:[]}, connections:[], organization:{}, role:'member', selected:null, source:null, csrf:'', drafts:{}, pendingMessages:{}};
const terminal = new Set(['completed','failed','cancelled','interrupted','idle']);
const providerNames = {linear:'Linear', slack:'Slack', notion:'Notion'};
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json', 'X-CSRF-Token':state.csrf, ...options.headers}});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The request could not be completed.');
  return body;
}
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(state.toast); state.toast = setTimeout(() => $('#toast').hidden = true, 5500); }
function statusLabel(status) { return `<span class="status ${esc(status)}">${esc(status==='idle'?'Ready':status.replaceAll('_',' '))}</span>`; }
function relative(date) { const min = Math.max(0, Math.floor((Date.now() - new Date(date)) / 60000)); return min < 1 ? 'Just now' : min < 60 ? `${min}m ago` : min < 1440 ? `${Math.floor(min/60)}h ago` : new Date(date).toLocaleDateString(); }
function stopStream(){ state.source?.close(); state.source = null; }
async function navigate(view) {
  stopStream(); state.view=view; state.selected=null;
  document.querySelectorAll('.nav-button').forEach(b => b.classList.toggle('active', b.dataset.view===view));
  $('#page-title').textContent = {tasks:'Sessions',connections:'Organization / Connections',runtime:'Runtime'}[view];
  history.replaceState(null,'',view==='tasks'?'#tasks':'#'+view);
  if(view==='tasks') await renderHome(); else if(view==='connections') await renderConnections(); else await renderRuntime();
}
async function refreshRuns(){ state.runs=await api('/api/runs'); $('#task-count').textContent=state.runs.length; }
async function renderHome(){
  await refreshRuns(); state.connections=await api('/api/connections');
  $('#content').innerHTML=`<div class="page-heading"><div><div class="eyebrow">YOUR AGENT WORKSPACE</div><h1>What should we work on?</h1><p class="subtext">Start a conversation with Moyai Devin. Follow the work and send the next step.</p></div></div>
  <form id="task-form" class="composer"><textarea id="prompt" name="prompt" aria-label="Task instructions" placeholder="Describe a task, paste an issue, or ask a question…" required minlength="3" maxlength="16000"></textarea><div class="compose-options"><input class="repo-input" id="repo" type="url" aria-label="Public GitHub repository URL" placeholder="＋ Public GitHub repository (optional)"><select id="mode" aria-label="Execution mode"><option value="demo">Demo run</option><option value="modal" ${state.config.cloud_ready?'selected':'disabled'}>Cloud session</option></select><button class="primary" type="submit">Start session</button></div><div class="compose-disclosure"><span id="mode-note">${state.config.cloud_ready?'Cloud mode · a real isolated machine for this task':'Demo mode · simulated activity, no AI or sandbox usage'}</span><div id="plugin-options"></div></div></form>
  <div class="suggestions"><button data-prompt="Find the cause of a failing test and propose a fix. Run the relevant tests and summarize what changed.">Investigate a failing test</button><button data-prompt="Read the repository and explain its architecture, entry points, and test setup. Make no changes.">Explore a repository</button><button data-prompt="Review recent issues and related team context. Summarize the evidence and propose next steps.">Research an issue</button></div>
  <div class="section-header"><h2>Recent sessions</h2><small>${state.runs.length} ${state.runs.length===1?'session':'sessions'}</small></div><div class="task-table">${state.runs.length?state.runs.map(r=>`<button class="task-row" data-run="${r.id}"><span><span class="task-title">${esc(r.prompt.split('\n')[0])}</span><span class="task-meta">${r.repo_url?esc(r.repo_url.replace('https://github.com/','')):'Independent workspace'}</span></span>${statusLabel(r.status)}<small>${r.mode==='demo'?'Demo':'Modal'}</small><small>${relative(r.created_at)}</small></button>`).join(''):'<div class="empty"><div class="empty-icon">▤</div><h3>A workspace for your next task</h3><p>Start a demo above to try the full workflow.</p></div>'}</div>
  <div class="home-footer"><span>Hermes Agent</span><span>Isolated Modal sandboxes</span><a href="#connections" id="connect-link">Connect your team’s apps</a></div>`;
  $('#task-form').onsubmit=submitTask;
  $('#plugin-options').innerHTML='<span title="Shared organization connections. Uncheck an app to exclude it from this task.">Connected apps for this task</span>'+state.connections.filter(c=>c.connected&&c.enabled).map(c=>`<label class="plugin-toggle"><input type="checkbox" name="plugin" value="${c.id}" checked>${providerNames[c.id]}</label>`).join('');
  $('#mode').onchange=()=>{ $('#mode-note').textContent=$('#mode').value==='demo'?'Demo mode · simulated activity, no AI or sandbox usage':'Cloud mode · a real isolated machine for this task'; };
  document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('#prompt').value=b.dataset.prompt;$('#prompt').focus();});
  document.querySelectorAll('[data-run]').forEach(b=>b.onclick=()=>openRun(b.dataset.run));
  $('#connect-link').onclick=e=>{e.preventDefault();navigate('connections').catch(showError);};
}
async function submitTask(e){
  e.preventDefault();const button=$('#task-form button[type="submit"]'); button.disabled=true;
  try{ const run=await api('/api/runs',{method:'POST',body:JSON.stringify({prompt:$('#prompt').value,repo_url:$('#repo').value,mode:$('#mode').value,plugins:[...document.querySelectorAll('[name="plugin"]:checked')].map(x=>x.value)})});await refreshRuns();await openRun(run.id); }
  catch(error){toast(error.message);button.disabled=false;}
}
async function openRun(id){
  stopStream();const run=await api(`/api/runs/${id}`);state.selected=id;state.view='tasks';$('#page-title').textContent='Tasks / Activity';history.replaceState(null,'','#run='+id);
  if(run.chat_enabled){renderChat(run);return;}
  $('#content').innerHTML=`<button class="back-button" id="back">‹ All tasks</button><div class="page-heading"><div><div class="eyebrow">${run.mode==='demo'?'DEMO WORKSPACE':'CLOUD WORKSPACE'}</div><h1>Task activity</h1></div><div class="toolbar">${terminal.has(run.status) && !run.active?'<button id="retry" class="small">Run again</button>':'<button id="cancel" class="small danger">Stop task</button>'}</div></div>
  <div class="task-layout"><section class="task-main"><div class="task-intro"><span class="badge">${run.mode==='demo'?'Demo':'Hermes Agent'}</span><p class="prompt">${esc(run.prompt)}</p></div><div class="task-tabs"><span>Activity</span></div><div class="timeline" id="timeline">${run.events.map(eventHTML).join('')}</div><div id="approvals"></div><div id="artifact-area"></div></section><aside class="details"><div class="card"><h3>Run details</h3><div class="detail-row"><span>Status</span><span id="run-status">${statusLabel(run.status)}</span></div><div class="detail-row"><span>Execution</span><span>${run.mode==='demo'?'Simulated':'Modal sandbox'}</span></div><div class="detail-row"><span>Agent</span><span>${run.mode==='demo'?'Not started':'Hermes'}</span></div><div class="detail-row"><span>Repository</span><span>${run.repo_url?esc(run.repo_url.replace('https://github.com/','')):'None'}</span></div><div class="detail-row"><span>Connections</span><span>${run.plugins.length?run.plugins.map(x=>providerNames[x]).join(', '):'None'}</span></div>${run.sandbox_id?`<div class="detail-row"><span>Sandbox</span><span>${esc(run.sandbox_id)}</span></div>`:''}</div><div class="note"><strong>${run.mode==='demo'?'A preview of the workflow':'An isolated workspace'}</strong>${run.mode==='demo'?'This run uses simulated events. No model, cloud machine, repository, or connected app is accessed.':'Moyai Devin works inside a dedicated Modal sandbox. Writes to connected apps require your approval.'}</div></aside></div>`;
  $('#back').onclick=()=>navigate('tasks').catch(showError);
  if($('#cancel'))$('#cancel').onclick=async()=>{try{await api(`/api/runs/${id}/cancel`,{method:'POST'});await openRun(id);}catch(e){toast(e.message);}};
  if($('#retry'))$('#retry').onclick=async()=>{await navigate('tasks');$('#prompt').value=run.prompt;$('#repo').value=run.repo_url;$('#mode').value=run.mode;$('#mode').dispatchEvent(new Event('change'));document.querySelectorAll('[name="plugin"]').forEach(input=>input.checked=run.plugins.includes(input.value));};
  renderApprovals(run.approvals || []);
  if(run.has_artifact)$('#artifact-area').innerHTML=`<div class="artifact-download"><a href="/api/runs/${id}/artifact">Download result and changes (.zip)</a></div>`;
  if(!terminal.has(run.status) || run.active){
    const cursor=run.events.at(-1)?.id||0; const source=new EventSource(`/api/runs/${id}/events?after=${cursor}`);state.source=source;
    source.onmessage=(e)=>{if(state.selected!==id)return;const event=JSON.parse(e.data);$('#timeline').insertAdjacentHTML('beforeend',eventHTML(event));if(event.kind==='approval') refreshApproval(id).catch(showError);};
    source.addEventListener('run-status',e=>{if(state.selected===id)$('#run-status').innerHTML=statusLabel(JSON.parse(e.data).status);});
    source.addEventListener('settled',()=>{source.close();if(state.selected===id)openRun(id).catch(showError);});
    source.onerror=()=>{if(source.readyState===EventSource.CLOSED)toast('Activity stream disconnected. Reopen this task to reconnect.');};
  }
}
function renderChat(run){
  const id=run.id;
  $('#page-title').textContent='Sessions / Chat';
  $('#content').innerHTML=`<button class="back-button" id="back">‹ All sessions</button><div class="page-heading"><div><div class="eyebrow">${run.mode==='demo'?'DEMO SESSION':'CLOUD SESSION'}</div><h1 class="session-title">${esc(run.prompt.split('\n')[0])}</h1></div><button id="stop-response" class="small danger">Stop response</button></div>
  <div class="chat-layout"><section class="chat-panel"><div class="conversation" id="conversation" role="log" aria-label="Conversation" aria-live="polite"></div><div id="approvals"></div><div class="chat-working" id="chat-working" role="status"></div><form id="message-form" class="reply-composer"><label for="followup">Message Moyai Devin</label><textarea id="followup" maxlength="16000" required placeholder="Ask a follow-up, add context, or give the next step…"></textarea><div class="reply-actions"><small id="queue-note">Your conversation and files stay with this session.</small><button type="submit" class="primary">Send message</button></div></form></section>
  <aside class="session-side"><div class="card"><div class="section-header"><h3>Session</h3><span id="run-status"></span></div><div class="detail-row"><span>Apps</span><span>${run.plugins.length?run.plugins.map(x=>providerNames[x]).join(', '):'None selected'}</span></div><div class="detail-row"><span>Workspace</span><span id="saved-workspace"></span></div><div id="artifact-area"></div><p class="session-help">Send a message any time. Follow-ups wait for the current response to finish. External writes need administrator approval.</p></div><div id="slack-context"></div><details class="card activity-panel" open><summary>Live progress</summary><div class="timeline" id="timeline">${run.events.filter(e=>!['chat','result'].includes(e.kind)).map(eventHTML).join('')}</div></details></aside></div>`;
  $('#back').onclick=()=>navigate('tasks').catch(showError);
  $('#followup').value=state.drafts[id]||'';
  $('#followup').oninput=()=>{state.drafts[id]=$('#followup').value;};
  $('#followup').onkeydown=e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){e.preventDefault();$('#message-form').requestSubmit();}};
  $('#stop-response').onclick=async()=>{try{await api(`/api/runs/${id}/cancel`,{method:'POST'});await refreshChat(id);}catch(e){toast(e.message);}};
  $('#message-form').onsubmit=async e=>{
    e.preventDefault();const content=$('#followup').value.trim();if(!content)return;
    const button=$('#message-form button');button.disabled=true;
    let pending=state.pendingMessages[id];if(!pending||pending.content!==content)pending=state.pendingMessages[id]={content,client_id:crypto.randomUUID()};
    try{await api(`/api/runs/${id}/messages`,{method:'POST',body:JSON.stringify(pending)});delete state.pendingMessages[id];if(state.selected===id&&$('#followup').value.trim()===content){$('#followup').value='';state.drafts[id]='';}await refreshChat(id);}
    catch(error){toast(error.message);}finally{if(state.selected===id)button.disabled=false;}
  };
  updateChat(run,true);
  const source=new EventSource(`/api/runs/${id}/events?after=${run.events.at(-1)?.id||0}`);state.source=source;
  source.onmessage=e=>{if(state.selected!==id)return;const event=JSON.parse(e.data);if(!['chat','result'].includes(event.kind))$('#timeline').insertAdjacentHTML('beforeend',eventHTML(event));if(['chat','approval','artifact','context'].includes(event.kind))refreshChat(id).catch(showError);};
  source.addEventListener('run-status',e=>{if(state.selected===id)updateChatStatus(JSON.parse(e.data));});
  source.onerror=()=>{if(state.selected===id)$('#chat-working').textContent='Reconnecting to live progress…';};
}
function updateChatStatus(run){
  $('#run-status').innerHTML=statusLabel(run.status);
  const busy=!terminal.has(run.status)||run.active;
  $('#stop-response').hidden=!busy;
  $('#stop-response').disabled=run.status==='stopping';
  $('#queue-note').textContent=busy?'Follow-ups will run after the current response.':'Your conversation and files stay with this session.';
  $('#chat-working').textContent=({idle:'Ready for your next message',queued:'Waiting to start…',provisioning:'Opening your cloud workspace…',running:'Moyai Devin is working…',saving:'Saving your conversation and workspace…',awaiting_approval:'Waiting for administrator approval',stopping:'Stopping the current response…',failed:'Response failed. You can send a follow-up.',cancelled:'Response stopped. You can continue from the last saved workspace.',interrupted:'Response interrupted. Send a new message to continue.'})[run.status]||'Finishing response…';
}
function updateChat(run,initial=false){
  const box=$('#conversation');const atBottom=initial||box.scrollHeight-box.scrollTop-box.clientHeight<80;
  box.innerHTML=run.messages.map(m=>`<article class="chat-message ${m.role}"><div class="message-label">${m.role==='user'?'You':'Moyai Devin'}<small>${m.role==='user'&&m.status!=='completed'?esc(m.status):''}</small></div><div class="message-content">${esc(m.content)}</div></article>`).join('');
  if(atBottom)box.scrollTop=box.scrollHeight;
  updateChatStatus(run);renderApprovals(run.approvals||[]);
  renderSlackContext(run.slack_source);
  $('#saved-workspace').textContent=run.mode==='demo'?'Simulated':run.snapshot_id?'Saved for follow-ups':'Not saved yet';
  $('#artifact-area').innerHTML=run.has_artifact?`<a class="session-download" href="/api/runs/${run.id}/artifact">Download latest files</a>`:'';
}
function renderSlackContext(source){
  const target=$('#slack-context');if(!target)return;
  if(!source){target.innerHTML='';return;}
  const ready=source.context_status==='ready', messages=source.messages||[];
  const status=ready?`${messages.length} messages from the ${source.kind}`:({pending:'Waiting to read the conversation…',fetching:'Reading the conversation…',unavailable:'Conversation could not be read',legacy:'This session started before automatic Slack context'})[source.context_status]||'Context unavailable';
  target.innerHTML=`<section class="card source-context"><h3>Slack context</h3><p>${esc(status)}</p>${source.permalink?`<a href="${esc(source.permalink)}" target="_blank" rel="noopener noreferrer">Open source conversation ↗</a>`:''}${source.warning?`<p class="source-warning">${esc(source.warning)}</p>`:''}${ready?`<details><summary>View included messages</summary><div class="source-messages">${messages.map(m=>`<article><small>${esc(m.user)} · ${new Date(Number(m.ts)*1000).toLocaleString()}</small><p>${esc(m.text)}${m.text_truncated?'…':''}</p></article>`).join('')}</div></details>`:''}</section>`;
}
async function refreshChat(id){const run=await api(`/api/runs/${id}`);if(state.selected===id&&$('#conversation'))updateChat(run);}
function eventHTML(event){const stamp=new Date(event.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});const detail=event.data?.command||event.data?.detail;return `<div class="event event-${esc(event.kind)}"><span class="event-marker">${event.kind==='result'?'✓':event.kind==='tool'?'⌘':'·'}</span><div class="event-heading"><strong>${esc(event.message)}</strong><time>${stamp}</time></div>${detail?`<details class="event-detail"><summary>Details</summary><pre>${esc(typeof detail==='string'?detail:JSON.stringify(detail,null,2))}</pre></details>`:''}</div>`;}
function renderApprovals(approvals){ if(!$('#approvals'))return;$('#approvals').innerHTML=approvals.filter(a=>a.status==='pending').map(a=>`<div class="approval"><h3>Approval needed: ${esc(a.tool)}</h3><pre>${esc(JSON.stringify(a.arguments,null,2))}</pre>${state.role==='admin'?`<div class="approval-actions"><button data-approval="${a.id}" data-decision="approve" class="primary small">Approve once</button><button data-approval="${a.id}" data-decision="deny" class="small">Deny</button></div>`:'<p>An organization administrator must approve this action.</p>'}</div>`).join('');document.querySelectorAll('[data-approval]').forEach(b=>b.onclick=async()=>{try{await api(`/api/approvals/${b.dataset.approval}`,{method:'POST',body:JSON.stringify({decision:b.dataset.decision})});await refreshApproval(state.selected);}catch(e){toast(e.message);}});}
async function refreshApproval(id){if(state.selected!==id)return;const run=await api(`/api/runs/${id}`);if(state.selected===id){renderApprovals(run.approvals);$('#run-status').innerHTML=statusLabel(run.status);}}
async function renderConnections(){
  [state.connections,state.organization]=await Promise.all([api('/api/connections'),api('/api/organization')]);
  const admin=state.role==='admin', org=state.organization, slack=org.slack_sessions;
  $('#content').innerHTML=`<div class="page-heading"><div><div class="eyebrow">${esc(org.name)} / ORGANIZATION SETTINGS</div><h1>Organization connections</h1><p class="subtext">Connect once. Share approved tools across your team's sessions.</p></div><span class="badge">${admin?'Administrator':'Member'}</span></div>
    <div class="org-summary"><div><strong>Shared with your organization</strong><p>Each session chooses which apps it uses. Credentials stay on the server.</p></div><span>${state.connections.filter(c=>c.connected).length} of 3 connected</span></div>
    <div class="connections-grid org-connections">${state.connections.map(c=>`<article class="connection-card"><div class="connection-top"><div class="app-icon ${c.id}">${providerNames[c.id][0]}</div><span class="connection-state ${c.connected&&c.enabled?'healthy':''}">${c.connected?(c.enabled?'Connected':'Paused'):'Not connected'}</span></div><h2>${providerNames[c.id]}</h2><p>${({linear:'Issues, requirements, and progress comments.',slack:'Conversation search and thread context.',notion:'Documentation, page context, and new notes.'})[c.id]}</p><dl class="connection-facts"><div><dt>Workspace</dt><dd>${esc(c.label||'—')}</dd></div><div><dt>Connection</dt><dd>${esc(c.identity)}</dd></div><div><dt>Access</dt><dd>${c.read_only?'Read only':'Writes need admin approval'}</dd></div></dl><small>${c.check_status==='healthy'?'Verified '+relative(c.checked_at):c.check_status==='needs_attention'?'Connection needs attention':'Ready for a connection check'}</small><div class="connection-action"><button data-manage="${c.id}">${admin?'Manage':'View access'}</button></div></article>`).join('')}</div>
    <div class="org-lower"><section class="card org-slack"><div class="section-header"><h2>Start sessions from Slack</h2><span class="connection-state ${slack.enabled?'healthy':''}">${slack.enabled?'Enabled':'Setup needed'}</span></div><p>Mention <strong>@Moyai Devin</strong> with a task in a channel the bot has joined. It reads the thread or nearby channel messages, starts work, and replies with a link to the session.</p><p class="subtext">${slack.enabled?esc(slack.audience)+' can start sessions using enabled organization apps.':'The Slack workspace bot must be installed before mentions can start sessions.'} Results and approvals stay in the web app.</p>${slack.last_session?`<small>Latest request ${relative(slack.last_session.created_at)} · ${slack.last_session.reply_status==='sent'?'Session link sent':'Reply '+esc(slack.last_session.reply_status)}</small>`:''}</section>
    <section class="card org-access"><h2>Organization access</h2><p>Administrators manage connections and approve external writes. Members can start sessions and use enabled apps.</p><p class="subtext">${org.member_access_configured?'Separate administrator and member sign-ins are enabled.':'Currently using the shared administrator sign-in. Separate member access is not configured.'}</p>${admin?`<form id="org-name-form"><label for="org-name">Organization name</label><div class="inline-field"><input id="org-name" maxlength="80" required value="${esc(org.name)}"><button type="submit">Save</button></div></form>`:''}</section></div>
    <div class="note org-note"><strong>Shared connection, same provider identity</strong>A shared connection uses the account that authorized it. Sharing it here does not turn a personal account into a service account or expand its provider permissions. Slack mentions only receive a session link; agent messages and edits still require administrator approval.</div>
    ${org.activity.length?`<section class="org-history"><h2>Connection activity</h2>${org.activity.map(a=>`<div><span>${esc(providerNames[a.provider]||'Organization')} · ${esc(a.action)}</span><small>${esc(a.actor)} · ${relative(a.created_at)}</small></div>`).join('')}</section>`:''}`;
  document.querySelectorAll('[data-manage]').forEach(b=>b.onclick=()=>manageConnection(b.dataset.manage));
  if($('#org-name-form'))$('#org-name-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/organization',{method:'PATCH',body:JSON.stringify({name:$('#org-name').value})});await renderConnections();toast('Organization updated.');}catch(err){toast(err.message);}};
}
function manageConnection(provider){
  const c=state.connections.find(c=>c.id===provider), name=providerNames[provider], admin=state.role==='admin';
  $('#connection-title').textContent=name+' · Organization access';
  $('#connection-body').innerHTML=`<p>${c.connected?'Connected to '+esc(c.label)+' using '+esc(c.identity.toLowerCase())+'.':'No shared connection is installed.'}</p><ul class="tool-list">${c.tools.map(t=>`<li>${esc(t.description)}</li>`).join('')}</ul>${admin?`<label class="policy-check"><input id="connection-enabled" type="checkbox" ${c.enabled?'checked':''}>Available to organization sessions</label><div class="field"><label for="connection-access">Allowed actions</label><select id="connection-access"><option value="approval" ${!c.read_only?'selected':''}>Read and request approval for writes</option><option value="read" ${c.read_only?'selected':''}>Read only</option></select></div><p class="subtext">Pausing access or switching to read only also cancels pending write approvals. An action already sent cannot be recalled.</p><button class="primary full" type="submit">Save access</button><div class="connection-controls">${c.connected?'<button id="connection-check" type="button">Check connection</button>':''}<button id="connection-reconnect" type="button">${c.connected?'Reconnect':'Connect'} ${name}</button>${c.connected?'<button id="connection-disconnect" class="danger" type="button">Disconnect</button>':''}</div>`:`<div class="note">${c.enabled?'Available to sessions.':'Paused for all sessions.'} ${c.read_only?'Read only.':'Writes require administrator approval.'} Ask an organization administrator to change this connection.</div>`}<div id="connection-error" role="alert"></div>`;
  $('#connection-form').onsubmit=async e=>{e.preventDefault();if(!admin)return;try{await api('/api/connections/'+provider+'/policy',{method:'PATCH',body:JSON.stringify({enabled:$('#connection-enabled').checked,read_only:$('#connection-access').value==='read'})});$('#connection-dialog').close();await renderConnections();toast(name+' access updated.');}catch(error){$('#connection-error').textContent=error.message;}};
  if($('#connection-reconnect'))$('#connection-reconnect').onclick=()=>{ $('#connection-dialog').close();connectionDialog(provider); };
  if($('#connection-check'))$('#connection-check').onclick=async()=>{const b=$('#connection-check');b.disabled=true;try{await api('/api/connections/'+provider+'/check',{method:'POST'});toast(name+' connection verified.');await renderConnections();}catch(error){$('#connection-error').textContent=error.message;}finally{b.disabled=false;}};
  if($('#connection-disconnect'))$('#connection-disconnect').onclick=async()=>{const b=$('#connection-disconnect');if(b.dataset.confirm!=='yes'){b.dataset.confirm='yes';b.textContent='Disconnect for everyone';return;}try{await api('/api/connections/'+provider,{method:'DELETE'});$('#connection-dialog').close();await renderConnections();toast(name+' disconnected.');}catch(error){$('#connection-error').textContent=error.message;}};
  $('#connection-dialog').showModal();
}
function connectionDialog(provider){
  const connection=state.connections.find(c=>c.id===provider),name=providerNames[provider];
  const hints={linear:'Authorize the Linear account or integration your organization should use. Its existing team permissions still apply.',slack:'Connect the Slack account whose conversations your organization can search. The workspace bot handles session mentions separately.',notion:'Connect Notion and choose the pages your organization can use in sessions.'};
  $('#connection-title').textContent='Connect '+name+' for your organization';
  $('#connection-body').innerHTML='<p>'+hints[provider]+'</p>'+(connection.oauth_configured?'<button class="primary full" id="oauth-connect" type="button">Continue with '+name+'</button><div class="divider">or use a token</div>':'<div class="note">One-click sign-in becomes available after an administrator configures this app’s OAuth client.</div>')+'<div class="field"><label for="connection-token">Access token</label><input id="connection-token" class="token-input" type="password" required minlength="8" maxlength="4096" autocomplete="off"><small>Validated with '+name+' and stored encrypted on the server.</small></div><div id="connection-error" role="alert"></div><button class="primary full" type="submit">Connect '+name+'</button>';
  $('#connection-form').onsubmit=async(e)=>{
    e.preventDefault();const button=$('#connection-form button[type="submit"]');button.disabled=true;
    try{await api('/api/connections/'+provider,{method:'POST',body:JSON.stringify({token:$('#connection-token').value})});$('#connection-token').value='';$('#connection-dialog').close();await renderConnections();toast(name+' connected.');}
    catch(error){$('#connection-error').textContent=error.message;button.disabled=false;}
  };
  if($('#oauth-connect'))$('#oauth-connect').onclick=async()=>{try{const result=await api('/api/connections/'+provider+'/oauth',{method:'POST'});window.location.assign(result.url);}catch(e){$('#connection-error').textContent=e.message;}};
  $('#connection-dialog').showModal();
}
async function renderRuntime(){state.config=await api('/api/config');const fields=['MODAL_TOKEN_ID','MODAL_TOKEN_SECRET','LITELLM_API_BASE','LITELLM_API_KEY','AGENT_MODEL',...state.config.missing.filter(x=>x.startsWith('PUBLIC_URL'))];$('#content').innerHTML=`<div class="page-heading"><div><div class="eyebrow">EXECUTION</div><h1>Runtime</h1><p class="subtext">One isolated workspace for every cloud task.</p></div><span class="badge">${state.config.cloud_ready?'Cloud ready':'Setup needed'}</span></div><div class="setup-grid"><div class="card"><h2>Modal + Hermes</h2><p class="subtext">Modal provides the machine. Hermes plans the work, uses tools, and returns the result.</p><ul class="config-list">${fields.map(key=>`<li><code>${key}</code><span class="${state.config.missing.includes(key)?'missing':'configured'}">${state.config.missing.includes(key)?'Not configured':'Configured'}</span></li>`).join('')}</ul></div><div class="card"><h2>Workspace settings</h2><div class="detail-row"><span>Concurrent tasks</span><span>${state.config.max_concurrent_runs}</span></div><div class="detail-row"><span>Time limit per run</span><span>${Math.round(state.config.run_timeout_seconds/60)} minutes</span></div><div class="detail-row"><span>Model</span><span>${esc(state.config.model||'Choose in .env')}</span></div><p class="subtext" style="margin-top:25px">Configure the missing values in the workspace’s .env file and restart the server. Cloud runs need a reachable HTTPS workspace address. Use a budget-limited LiteLLM key. See README.md for deployment and app setup.</p></div></div>`;}
function showError(error){toast(error.message);}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>navigate(b.dataset.view).catch(showError));
$('#new-task').onclick=()=>navigate('tasks').then(()=>$('#prompt').focus()).catch(showError);
$('.dialog-close').onclick=()=>$('#connection-dialog').close();
window.addEventListener('hashchange',()=>{
  if(!state.csrf)return;
  const linkedRun=location.hash.match(/^#run=([a-f0-9]{32})$/)?.[1];
  (linkedRun?openRun(linkedRun):navigate(location.hash==='#connections'?'connections':location.hash==='#runtime'?'runtime':'tasks')).catch(showError);
});
async function boot(){
  try{
    const session=await api('/api/session');state.csrf=session.csrf;state.role=session.role||'member';
    $('.rail-foot small').textContent=session.local?'Private · local preview':'Shared internal workspace';
    if(!session.authenticated){
      $('#content').innerHTML='<div class="login"><div class="card"><h1>Welcome back</h1><p class="subtext">Sign in to Moyai Devin.</p><form id="login-form"><div class="field"><label for="password">Workspace password</label><input id="password" type="password" autocomplete="current-password" required></div><button class="primary full">Sign in</button></form></div></div>';
      $('#login-form').onsubmit=async(e)=>{e.preventDefault();try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('#password').value})});await boot();}catch(error){toast(error.message);}};
      document.querySelectorAll('.rail button').forEach(b=>b.disabled=true);
      return;
    }
    document.querySelectorAll('.rail button').forEach(b=>b.disabled=false);
    state.config=await api('/api/config');
    if(!session.local){$('.rail-foot small').textContent=state.role==='admin'?'Organization admin':'Organization member';$('.rail-foot').insertAdjacentHTML('beforeend','<button id="logout" class="quiet" aria-label="Sign out">⏻</button>');$('#logout').onclick=async()=>{await api('/api/logout',{method:'POST'});location.reload();};}
    const linkedRun=location.hash.match(/^#run=([a-f0-9]{32})$/)?.[1]; if(linkedRun)await openRun(linkedRun);else await navigate(location.hash==='#connections'?'connections':location.hash==='#runtime'?'runtime':'tasks');
    if(new URLSearchParams(location.search).get('connection')){toast(location.search.includes('success')?'App connected.':'Connection cancelled.');history.replaceState(null,'','/#connections');}
    registerWebMCP();
  }catch(e){$('#content').innerHTML='<div class="error-banner">'+esc(e.message)+'</div>';}
}
function registerWebMCP(){
  if(!document.modelContext?.registerTool)return;
  const abort=new AbortController();window.addEventListener('pagehide',()=>abort.abort(),{once:true});
  const tools=[
    {name:'list_workspace_tasks',description:'List the latest tasks in this workspace.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true},execute:async()=>api('/api/runs')},
    {name:'create_demo_task',description:'Create a simulated task to preview the workspace; no model, cloud machine, or connected app is used.',inputSchema:{type:'object',properties:{prompt:{type:'string',minLength:3,maxLength:16000}},required:['prompt'],additionalProperties:false},annotations:{readOnlyHint:false},execute:async(input)=>{if(typeof input.prompt!=='string'||input.prompt.trim().length<3)throw Error('Enter a task of at least three characters.');const run=await api('/api/runs',{method:'POST',body:JSON.stringify({prompt:input.prompt,mode:'demo'})});await refreshRuns();await openRun(run.id);return {id:run.id,status:run.status,mode:run.mode};}}
  ];
  tools.forEach(tool=>{try{Promise.resolve(document.modelContext.registerTool(tool,{signal:abort.signal})).catch(()=>{});}catch{}});
}
boot();
