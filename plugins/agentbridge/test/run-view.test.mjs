import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {RunView} from '../src/control.mjs';

test('semantic identities, escaped content, exact controls and cleanup',async()=>{
 const dom=new JSDOM('<div id="status"></div><div id="error"></div><div id="controls"></div><div id="gate"></div><textarea id="input"></textarea><button id="send"></button><main id="transcript"></main>');
 const calls=[]; const state={run:{run_id:'r',state:'PAUSED',current_agent:'codex',round_number:2,version:7},transcript:['gpt','codex','claude-code'].map(speaker_name=>({speaker_name,speaker_type:speaker_name==='gpt'?'model':'agent',content:'<img onerror="bad">'}))};
 const view=new RunView(dom.window.document,async(name,args)=>{calls.push({name,args});return {structuredContent:state};});
 view.receive(state);
 assert.match(dom.window.document.body.textContent,/GPT/); assert.match(dom.window.document.body.textContent,/Codex/); assert.match(dom.window.document.body.textContent,/Claude Code/);
 assert.equal(dom.window.document.querySelectorAll('img').length,0);
 await view.act('continue_run'); assert.equal(calls[0].args.expected_version,7); assert.equal(calls[0].args.run_id,'r');
 view.dispose(); assert.equal(view.timer,null);
});

test('approval is explicit exact pending gate; errors visible',async()=>{
 const dom=new JSDOM('<div id="status"></div><div id="error"></div><div id="controls"></div><div id="gate"></div><textarea id="input"></textarea><button id="send"></button><main id="transcript"></main>');
 const calls=[]; const view=new RunView(dom.window.document,async(name,args)=>{calls.push({name,args});throw Error('offline');});
 view.receive({run:{run_id:'r',state:'WAITING_FOR_OWNER',version:8,round_number:1,current_agent:'claude',owner_gate:{gate_id:'g',digest:'d',action:'extend_rounds',command:'extend rounds by 5',impact:'Five more rounds'}},transcript:[]});
 assert.equal(calls.length,0);
 await view.act('approve_owner_gate'); assert.equal(calls[0].args.gate_id,'g'); assert.equal(calls[0].args.gate_digest,'d');
 assert.match(dom.window.document.querySelector('#error').textContent,/failed/i);view.dispose();
});
