import { TestBed } from '@angular/core/testing';
import { AgentChatWorkspaceComponent } from './agent-chat-workspace.component';
import { AgentChatApiService } from '../../services/agent-chat-api.service';
import { ThemeService } from '../../services/theme.service';
import { signal } from '@angular/core';

describe('Agent Chat workspace', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports:[AgentChatWorkspaceComponent],
      providers:[
        {provide:AgentChatApiService,useValue:{sessions:async()=>[],create:async(chat_model:string,agent:string)=>({session_id:'synthetic',title:'New',chat_model,agent}),messages:async()=>[]}},
        {provide:ThemeService,useValue:{toggleTheme:()=>{},theme:signal('dark')}}
      ]
    }).compileComponents();
  });
  it('renders two independent selectors', () => {
    const fixture=TestBed.createComponent(AgentChatWorkspaceComponent);fixture.detectChanges();
    const root=fixture.nativeElement as HTMLElement;
    expect(root.querySelector('[aria-label="Chat Model"]')).toBeTruthy();
    expect(root.querySelector('[aria-label="Agent"]')).toBeTruthy();
    expect(root.querySelector('app-chat-area')).toBeNull();
  });
  it('persists selection and shows active pair', async () => {
    const fixture=TestBed.createComponent(AgentChatWorkspaceComponent);fixture.detectChanges();
    await fixture.componentInstance.chat.newChat('claude','codex');fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.selection').textContent).toContain('Claude');
    expect(fixture.nativeElement.querySelector('.selection').textContent).toContain('Codex');
  });
  it('renders result as escaped text', () => {
    const fixture=TestBed.createComponent(AgentChatWorkspaceComponent);
    fixture.componentInstance.chat.messages.set([{message_id:'x',session_id:'s',speaker_type:'model',speaker_name:'gpt',role:'assistant',content:'<img src=x onerror=alert(1)>'}]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.bubble img')).toBeNull();
    expect(fixture.nativeElement.querySelector('.bubble').textContent).toContain('<img');
  });


  it('shows distinct identities for You, GPT, Codex, and Claude Code', () => {
    const fixture=TestBed.createComponent(AgentChatWorkspaceComponent);
    fixture.componentInstance.chat.messages.set([
      {message_id:'u',session_id:'s',speaker_type:'user',speaker_name:'user',content:'User text'},
      {message_id:'g',session_id:'s',speaker_type:'model',speaker_name:'gpt',content:'GPT text'},
      {message_id:'c',session_id:'s',speaker_type:'agent',speaker_name:'codex',content:'Codex text'},
      {message_id:'cc',session_id:'s',speaker_type:'agent',speaker_name:'claude-code',content:'Claude Code text'}
    ]);
    fixture.detectChanges();
    const labels=[...fixture.nativeElement.querySelectorAll('.bubble strong')].map((node:HTMLElement)=>node.textContent?.trim());
    expect(labels).toEqual(['You','GPT','Codex','Claude Code']);
  });

  it('renders terminal task status apart from conversation content', () => {
    const fixture=TestBed.createComponent(AgentChatWorkspaceComponent);
    fixture.componentInstance.chat.messages.set([
      {message_id:'u',session_id:'s',speaker_type:'user',speaker_name:'user',content:'Run task',status:'cancelled',agent:'codex'}
    ]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.message-status')?.textContent).toContain('Codex');
    expect(fixture.nativeElement.querySelector('.bubble p')?.textContent).toBe('Run task');
  });
});
