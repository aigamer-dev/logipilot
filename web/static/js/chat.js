(()=>{
const $=s=>document.querySelector(s);
// No backend configured (static hosting such as GitHub Pages): run in labelled demo mode.
const noBackend=window.LOGIPILOT_API===null;
const mock=new URLSearchParams(location.search).has('mock');
if(noBackend)document.addEventListener('DOMContentLoaded',()=>{const b=document.createElement('div');b.className='demo-banner';b.setAttribute('role','status');b.innerHTML='<b>Preview only:</b> this static copy has no agent behind it. Run the full agent locally in five minutes: see the <a href="https://github.com/aigamer-dev/logipilot#14-quickstart">README quickstart</a>.';document.querySelector('main').prepend(b)});
const store={get:k=>{try{return sessionStorage.getItem(k)}catch{return null}},set:(k,v)=>{try{sessionStorage.setItem(k,v)}catch{}}};
let sid=store.get('lp_sid');if(!sid){sid=crypto.randomUUID();store.set('lp_sid',sid)}
let files=[],busy=false,rec=null;
const msgs=$('#msgs'),msg=$('#msg'),log=$('#log');
const LABEL={RELEASED:'Resolved',NEEDS_INPUT:'Needs info',ESCALATED:'Sent to a human',REFUSED:'Declined',FAILED:'Failed'};
const STEPS=['Checking guardrails','Vaulting personal data','Decomposing request','Verifying contracts','Calling specialist agents','Verifying result'];
function el(t,c,txt){const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e}
function md(s){const h=window.marked?marked.parse(String(s||'')):null;
 return window.DOMPurify&&h!=null?DOMPurify.sanitize(h):String(s||'')} // fallback goes through textContent below
function setMd(node,s){if(window.marked&&window.DOMPurify)node.innerHTML=md(s);else node.textContent=String(s||'')}
function scroll(){log.scrollTop=log.scrollHeight}
function hideEmpty(){$('#empty').hidden=true}
/* attachments */
function renderChips(){const ul=$('#chips');ul.replaceChildren();files.forEach((f,i)=>{const li=el('li',null,f.name);
 const b=el('button',null,'×');b.type='button';b.setAttribute('aria-label','Remove '+f.name);b.onclick=()=>{files.splice(i,1);renderChips()};li.append(b);ul.append(li)})}
$('#attach').onclick=()=>$('#file').click();
$('#file').onchange=e=>{files.push(...e.target.files);e.target.value='';renderChips()};
/* voice */
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;let sr=null;
function dictate(){const b=$('#mic'),st=$('#micstat');
 if(sr){sr.stop();return}
 const base=msg.value?msg.value.trimEnd()+' ':'';sr=new SR();sr.interimResults=true;sr.continuous=true;sr.lang=navigator.language;
 sr.onresult=e=>{let t='';for(const r of e.results)t+=r[0].transcript;msg.value=base+t;msg.dispatchEvent(new Event('input'))};
 sr.onerror=e=>{st.textContent='Dictation error: '+e.error};
 sr.onend=()=>{sr=null;b.setAttribute('aria-pressed','false');if(!st.textContent.startsWith('Dictation error'))st.textContent=''};
 try{sr.start();b.setAttribute('aria-pressed','true');st.textContent='Listening… press the mic again to stop.'}catch{sr=null}}
$('#mic').onclick=async()=>{const b=$('#mic'),st=$('#micstat');
 if(SR){dictate();return}
 if(rec){rec.stop();return}
 if(!navigator.mediaDevices||!window.MediaRecorder){st.textContent='Voice recording is not supported in this browser.';return}
 try{const stream=await navigator.mediaDevices.getUserMedia({audio:true});
  const mt=['audio/webm','audio/ogg'].find(t=>MediaRecorder.isTypeSupported(t))||'';
  const chunks=[];rec=new MediaRecorder(stream,mt?{mimeType:mt}:{});
  rec.ondataavailable=e=>chunks.push(e.data);
  rec.onstop=()=>{stream.getTracks().forEach(t=>t.stop());const type=rec.mimeType||'audio/webm';
   files.push(new File(chunks,'voice-note.'+(type.includes('ogg')?'ogg':'webm'),{type}));rec=null;
   b.setAttribute('aria-pressed','false');st.textContent='Voice note attached.';renderChips()};
  rec.start();b.setAttribute('aria-pressed','true');st.textContent='Recording… press the mic again to stop.';
 }catch{st.textContent='Microphone access was denied or unavailable.'}};
/* composer */
msg.addEventListener('input',()=>{msg.style.height='auto';msg.style.height=Math.min(msg.scrollHeight,160)+'px';msg.style.overflowY=msg.scrollHeight>160?'auto':'hidden'});
msg.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#form').requestSubmit()}});
$('#form').onsubmit=e=>{e.preventDefault();send(msg.value.trim(),files.slice())};
document.querySelectorAll('.prompt').forEach(b=>b.onclick=()=>send(b.textContent,[]));
$('#newchat').onclick=()=>{sid=crypto.randomUUID();store.set('lp_sid',sid);msgs.replaceChildren();$('#empty').hidden=false;files=[];renderChips();msg.focus()};
/* network */
async function api(text,fl){
 if(noBackend)return noBackendEnv();
 if(mock){await new Promise(r=>setTimeout(r,1600));return mockEnv(text)}
 const fd=new FormData();fd.append('session_id',sid);fd.append('message',text);fl.forEach(f=>fd.append('files',f,f.name));
 const r=await fetch(window.LOGIPILOT_API+'/api/chat',{method:'POST',body:fd});
 let j=null;try{j=await r.json()}catch{}
 if(!r.ok)throw new Error((j&&j.detail)||('Request failed ('+r.status+')'));
 return j}
async function send(text,fl){
 if(busy||(!text&&!fl.length))return;
 hideEmpty();busy=true;$('#send').disabled=true;
 const u=el('div','msg user'),ub=el('div','bubble');ub.append(el('div',null,text||'(attachment)'));
 if(fl.length)ub.append(el('div','files','📎 '+fl.map(f=>f.name).join(', ')));
 u.append(ub);msgs.append(u);msg.value='';msg.style.height='auto';files=[];renderChips();
 await run(text,fl)}
async function run(text,fl){
 const t=el('div','msg bot'),tb=el('div','bubble typing');const lbl=el('span',null,STEPS[0]);
 tb.append(el('i'),el('i'),el('i'),lbl);t.append(tb);msgs.append(t);scroll();
 let n=0;const iv=setInterval(()=>{n=Math.min(n+1,STEPS.length-1);lbl.textContent=STEPS[n]+'…'},1800);
 try{const env=await api(text,fl);t.remove();render(env)}
 catch(err){t.remove();const m=el('div','msg bot'),b=el('div','bubble err');b.setAttribute('role','alert');
  b.append(el('div',null,'Something went wrong: '+err.message));const rb=el('button','btn sm','Retry');rb.type='button';rb.style.marginTop='10px';
  rb.onclick=async()=>{m.remove();busy=true;$('#send').disabled=true;await run(text,fl);fin()};b.append(rb);m.append(b);msgs.append(m)}
 clearInterval(iv);fin();scroll()}
function fin(){busy=false;$('#send').disabled=false}
/* render answer */
function render(env){
 const m=el('div','msg bot'),b=el('div','bubble');const a=el('div','md');setMd(a,env.answer);b.append(a);
 const meta=el('div','meta'),st=env.status;meta.append(el('span','pill s-'+st,LABEL[st]||st));
 const up=el('button','fb','👍'),dn=el('button','fb','👎');
 up.type=dn.type='button';up.setAttribute('aria-label','Helpful');dn.setAttribute('aria-label','Not helpful');up.setAttribute('aria-pressed','false');dn.setAttribute('aria-pressed','false');
 const cm=el('div','cm');cm.hidden=true;const ci=el('input');ci.type='text';ci.placeholder='What went wrong? (optional)';ci.setAttribute('aria-label','Feedback comment');
 const cs=el('button','btn sm','Submit');cs.type='button';cm.append(ci,cs);
 const fb=async(rating,comment)=>{try{if(!mock){const r=await fetch(window.LOGIPILOT_API+'/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:env.turn_id,session_id:sid,rating,comment:comment||null})});if(!r.ok)throw 0}
  up.disabled=dn.disabled=true;cs.disabled=ci.disabled=true;meta.append(el('span','files','Thanks for the feedback.'))}catch{meta.append(el('span','files','Could not send feedback.'))}};
 up.onclick=()=>{up.setAttribute('aria-pressed','true');fb(1)};
 dn.onclick=()=>{dn.setAttribute('aria-pressed','true');cm.hidden=false;ci.focus();dn.disabled=up.disabled=true};
 cs.onclick=()=>{fb(-1,ci.value.trim());cm.hidden=true};
 meta.append(up,dn);b.append(meta,cm,detail(env));m.append(b);msgs.append(m)}
function detail(env){
 const d=document.createElement('details'),s=el('summary',null,'How I handled this');d.append(s);const h=el('div','hi');
 const foot=[];if(env.pii_vaulted)foot.push(env.pii_vaulted+' PII items vaulted');foot.push('Latency '+env.latency_ms+' ms');
 (env.contracts||[]).forEach(c=>{const k=el('div','k');k.append(el('strong',null,c.fragment));
  k.append(el('div','files',`${c.category} · Jev confidence ${(c.confidence*100).toFixed(0)}% · ${c.attempts} attempt${c.attempts===1?'':'s'} · ${c.status}`));
  const hist=c.history||[];if(hist.length)k.append(el('div','life',[hist[0].from,...hist.map(x=>x.to)].join(' → ')));
  if(c.verification){const v=c.verification;k.append(el('div','files',`Verifier: resolves ${v.resolves}, grounded ${v.grounded}, policy ${v.policy_ok} · ${v.passed?'passed':'failed'}`))}
  if((c.actions||[]).length){const ul=el('ul');c.actions.forEach(x=>ul.append(el('li',null,`${x.kind} (${x.action_id})`)));k.append(ul)}
  h.append(k)});
 const sec=(t,items)=>{if(!items.length)return;const w=el('div');w.append(el('h4',null,t));const ul=el('ul');items.forEach(i=>ul.append(i));w.append(ul);h.append(w)};
 sec('Trace',(env.trace||[]).map(x=>{const li=el('li',null,`${x.step}: ${x.detail} `);li.append(el('span','ms',x.ms+' ms'));return li}));
 sec('Self-healing incidents',(env.incidents||[]).map(x=>el('li',null,`${x.component}: ${x.error} → ${x.recovery}`)));
 sec('Guardrail flags',(env.guardrail_flags||[]).map(x=>el('li',null,x)));
 h.append(el('div','files',foot.join(' · ')));d.append(h);return d}
/* health */
const COMP=['tools_api','mcp','jev','llm','firestore'];const NAME={llm:'Gemma 4'};
async function health(){const ul=$('#health');let j=null;
 try{j=noBackend?{components:Object.fromEntries(COMP.map(c=>[c,{ok:false,detail:'no backend (static preview)'}]))}:mock?{components:Object.fromEntries(COMP.map(c=>[c,{ok:true,detail:'mock'}]))}:await (await fetch(window.LOGIPILOT_API+'/api/health')).json()}catch{}
 ul.replaceChildren();COMP.forEach(c=>{const x=j&&j.components&&j.components[c];const li=el('li');
  const dot=el('i','dot'+(x?(x.ok?' ok':' bad'):''));li.title=(NAME[c]||c)+': '+(x?(x.ok?'ok':'down')+' - '+(x.detail||''):'unknown');
  li.append(dot,el('span',null,NAME[c]||c));li.setAttribute('aria-label',li.title);ul.append(li)})}
health();setInterval(health,30000);
/* mock */
// Static hosting with no API: say so plainly instead of inventing a shipment answer.
function noBackendEnv(){return{session_id:sid,turn_id:'static-'+Date.now(),status:'FAILED',answer:'**This page is a static preview with no agent behind it**, so I can\'t answer yet: nothing you type here reaches LogiPilot.\n\nRun the real agent locally in two commands (it fetches the keys automatically):\n\n```bash\ngit clone https://github.com/aigamer-dev/logipilot && cd logipilot\ncp .env.judges .env && scripts/run_all.sh   # then open http://localhost:8000\n```\n\nSee the [README](https://github.com/aigamer-dev/logipilot#readme) for details.',contracts:[],trace:[],incidents:[],guardrail_flags:[],pii_vaulted:0,latency_ms:0}}
function mockEnv(t){return{session_id:sid,turn_id:'mock-'+Date.now(),answer:`**LP1001** is delayed.\n\n- Last scan: Pune hub\n- New ETA: tomorrow 6 PM\n\nYou said: _${t.replace(/[<>]/g,'')}_`,status:'RELEASED',
 contracts:[{contract_id:'c1',fragment:'Where is LP1001?',category:'delay',confidence:.93,status:'RELEASED',history:['ISSUED>ACCEPTED','ACCEPTED>EXECUTING','EXECUTING>VERIFIED','VERIFIED>RELEASED'].map(p=>({from:p.split('>')[0],to:p.split('>')[1],by:'jev',at:''})),attempts:1,verification:{resolves:.95,grounded:.9,policy_ok:1,passed:true},actions:[{action_id:'ACT-1',kind:'notify_customer'}]}],
 trace:[{step:'guardrails',detail:'clean',ms:12},{step:'specialist:delay',detail:'tracking lookup',ms:430}],incidents:[{component:'mcp',error:'timeout',recovery:'retried, ok'}],guardrail_flags:[],pii_vaulted:2,latency_ms:1420}}
})();
