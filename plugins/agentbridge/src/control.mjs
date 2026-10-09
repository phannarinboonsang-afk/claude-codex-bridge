const labels={user:'You',gpt:'GPT',codex:'Codex','claude-code':'Claude Code',orchestrator:'System'};
const terminal=new Set(['DONE','STOPPED','FAILED','BLOCKED']);
export class RunView {
 constructor(document,call){this.doc=document;this.call=call;this.timer=null;this.disposed=false;this.delay=5000;this.busy=false;
  document.querySelector('#send').onclick=()=>this.act('send_owner_input',{message:document.querySelector('#input').value});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)this.clear();else this.schedule();});
 }
 clear(){if(this.timer)clearTimeout(this.timer);this.timer=null;}
 dispose(){this.disposed=true;this.clear();}
 receive(data){if(this.disposed||!data?.run||!Array.isArray(data.transcript))return;this.data=data;this.delay=5000;
  const r=data.run;this.doc.querySelector('#status').textContent=`${r.state} · GPT ↔ ${r.current_agent==='claude'?'Claude Code':'Codex'} · Round ${r.round_number}`;
  this.doc.querySelector('#error').textContent='';const timeline=this.doc.querySelector('#transcript');timeline.replaceChildren();
  for(const m of data.transcript){const article=this.doc.createElement('article');article.className=m.speaker_type==='system'?'system':'message';const label=this.doc.createElement('strong');label.textContent=labels[m.speaker_name]??'Unknown participant';const text=this.doc.createElement('pre');text.textContent=m.content;article.append(label,text);timeline.append(article);}
  const controls=this.doc.querySelector('#controls');controls.replaceChildren();
  const actions=[['Refresh','get_run']];if(r.state==='PAUSED')actions.push(['Continue','continue_run']);
  if(['RUNNING','IDLE'].includes(r.state))actions.push(['Pause','pause_run'],['Interrupt','interrupt_run']);
  if(!terminal.has(r.state))actions.push(['Stop','stop_run']);
  for(const [label,name] of actions){const b=this.doc.createElement('button');b.textContent=label;b.onclick=()=>this.act(name);controls.append(b);}
  const gate=this.doc.querySelector('#gate');gate.replaceChildren();
  if(r.state==='WAITING_FOR_OWNER'&&r.owner_gate){const p=this.doc.createElement('pre');p.textContent=`Requested: ${r.owner_gate.action}\n${r.owner_gate.command}\nImpact: ${r.owner_gate.impact}`;const b=this.doc.createElement('button');b.textContent='Approve this exact action';b.onclick=()=>this.act('approve_owner_gate');gate.append(p,b);}
  this.doc.querySelector('#send').disabled=terminal.has(r.state);this.schedule();
 }
 schedule(){this.clear();if(this.disposed||this.doc.hidden||!this.data||terminal.has(this.data.run.state))return;this.timer=setTimeout(()=>this.act('get_run'),this.delay);}
 async act(name,extra={}){if(this.busy||!this.data||this.disposed)return;this.busy=true;this.clear();
  const r=this.data.run;const args={run_id:r.run_id};
  if(name!=='get_run'){Object.assign(args,{expected_version:r.version,idempotency_key:globalThis.crypto.randomUUID()},extra);if(name==='approve_owner_gate')Object.assign(args,{gate_id:r.owner_gate.gate_id,gate_digest:r.owner_gate.digest});}
  try{const result=await this.call(name,args);if(result.isError)throw Error('Tool failed');this.receive(result.structuredContent);}
  catch{this.doc.querySelector('#error').textContent='Request failed. Refresh to reconcile current run before retrying.';this.delay=Math.min(this.delay*2,30000);}
  finally{this.busy=false;this.schedule();}
 }
}
