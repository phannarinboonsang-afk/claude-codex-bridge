import { TestBed } from '@angular/core/testing';
import { AgentChatService } from './agent-chat.service';
import { AgentChatApiService } from './agent-chat-api.service';
import { vi } from 'vitest';

describe('Independent Agent Chat', () => {
  let api: any;
  let service: AgentChatService;
  let persisted: any[];

  beforeEach(() => {
    localStorage.clear();
    persisted=[];
    api = {
      sessions: vi.fn().mockResolvedValue([]),
      create: vi.fn(async (chat_model: string, agent: string) => ({session_id: 'a', chat_model, agent, title: 'New Chat'})),
      messages: vi.fn(async (id: string) => persisted.filter(message => message.session_id===id)),
      cancel: vi.fn().mockResolvedValue({cancel_requested: true}),
      stream: vi.fn(async (body: any, emit: any) => {
        emit({type:'status',status:'planning',run_id:'run',session_id:body.session_id,chat_model:body.chat_model,agent:body.agent});
        const agentName=body.agent==='codex' ? 'codex' : 'claude-code';
        const rows=[
          {message_id:body.message_id,session_id:body.session_id,speaker_type:'user',speaker_name:'user',content:body.message,status:'queued'},
          {message_id:'gpt-plan',session_id:body.session_id,speaker_type:'model',speaker_name:'gpt',content:'I will ask the selected agent.',status:'completed'},
          {message_id:'agent-result',session_id:body.session_id,speaker_type:'agent',speaker_name:agentName,content:'AGENT_RESULT',status:'completed'},
          {message_id:'gpt-final',session_id:body.session_id,speaker_type:'model',speaker_name:'gpt',content:'FINAL',status:'completed'}
        ];
        for (const row of rows) { persisted.push(row); emit({type:'message',...row}); }
        emit({type:'status',status:'completed',run_id:'run',session_id:body.session_id});
        emit({type:'done',status:'completed',run_id:'run',session_id:body.session_id});
      })
    };
    TestBed.configureTestingModule({providers: [AgentChatService, {provide: AgentChatApiService, useValue: api}]});
    service = TestBed.inject(AgentChatService);
  });

  for (const model of ['gpt', 'claude'] as const) for (const agent of ['claude', 'codex'] as const) {
    it(model + ' + ' + agent, async () => {
      await service.newChat(model, agent);
      await service.send('hello');
      expect(api.create).toHaveBeenCalledWith(model, agent);
      expect(api.stream.mock.calls[0][0]).toMatchObject({chat_model: model, agent, session_id: 'a'});
      expect(service.messages().at(-1)?.content).toBe('FINAL');
      expect(service.messages().map(message=>message.speaker_name)).toEqual([
        'user','gpt',agent==='codex'?'codex':'claude-code','gpt'
      ]);
    });
  }


  it('keeps different agents in one shared session across turns', async () => {
    await service.newChat('gpt','codex');
    await service.send('ask Codex');
    await service.send('now ask Claude Code','gpt','claude');
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.stream.mock.calls.map((call:any[])=>call[0].session_id)).toEqual(['a','a']);
    expect(api.stream.mock.calls[1][0]).toMatchObject({chat_model:'gpt',agent:'claude'});
    expect(service.active()?.agent).toBe('claude');
    expect(service.messages().map(message=>message.speaker_name)).toEqual([
      'user','gpt','codex','gpt','user','gpt','claude-code','gpt'
    ]);
  });

  it('de-duplicates optimistic user events and ignores unknown event types', async () => {
    await service.newChat('gpt','codex');
    api.stream.mockImplementation(async (body:any,emit:any) => {
      const row={message_id:body.message_id,session_id:body.session_id,speaker_type:'user',speaker_name:'user',content:body.message,status:'queued'};
      persisted.push(row); emit({type:'message',...row});
      emit({type:'future_extension',content:'SHOULD_NOT_RENDER'});
      emit({type:'status',status:'completed',run_id:'r'});
      emit({type:'done',status:'completed',run_id:'r'});
    });
    await service.send('hello');
    expect(service.messages()).toHaveLength(1);
    expect(service.messages()[0].speaker_name).toBe('user');
  });

  it('restores persisted transcript for the last opened session after reload', async () => {
    const session={session_id:'a',title:'A',chat_model:'gpt',agent:'codex'};
    api.sessions.mockResolvedValue([session]);
    await service.newChat('gpt','codex');
    await service.send('hello');
    expect(persisted).toHaveLength(4);
    service.active.set(null); service.messages.set([]);
    await service.load();
    expect(service.active()?.session_id).toBe('a');
    expect(service.messages().map(message=>message.speaker_name)).toEqual(['user','gpt','codex','gpt']);
  });

  it('uses API cancellation for the active run', async () => {
    await service.newChat('gpt', 'codex');
    service.runId.set('run'); service.status.set('running');
    await service.stop();
    expect(api.cancel).toHaveBeenCalledWith('a','run');
  });

  it('uses safe failure messages and isolates sessions', async () => {
    await service.newChat('gpt','codex');
    api.stream.mockRejectedValue(new Error('sensitive internal path'));
    await service.send('hello');
    expect(service.error()).not.toContain('sensitive');
    await service.open({session_id:'b',title:'B',chat_model:'claude',agent:'claude'});
    expect(service.messages()).toEqual([]);
  });

  it('ignores stale session message responses', async () => {
    let resolveA: any;
    const row=(session_id:string,content:string)=>({message_id:content,session_id,speaker_type:'model',speaker_name:'gpt',content});
    api.messages.mockImplementation((id: string) => id === 'a' ? new Promise(resolve => resolveA=resolve) : Promise.resolve([row('b','B_ONLY')]));
    const opening=service.open({session_id:'a',title:'A',chat_model:'gpt',agent:'codex'});
    await service.open({session_id:'b',title:'B',chat_model:'claude',agent:'claude'});
    resolveA([row('a','A_ONLY')]); await opening;
    expect(service.active()?.session_id).toBe('b');
    expect(service.messages()[0].content).toBe('B_ONLY');
  });
});
