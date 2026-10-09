import { Component, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentChatService } from '../../services/agent-chat.service';
import { ThemeService } from '../../services/theme.service';
import { ChatModelChoice, AgentChoice, AgentChatSession } from '../../models/agent-chat.model';

@Component({
  selector: 'app-root', standalone: true, imports: [CommonModule, FormsModule],
  template: `
    <main class="agent-workspace">
      <header><h1>Agent Chat</h1><button (click)="theme.toggleTheme()">Theme</button></header>
      <section class="selectors">
        <label>Chat Model <select [(ngModel)]="model" [disabled]="chat.busy()" aria-label="Chat Model"><option value="gpt">GPT</option><option value="claude">Claude</option></select></label>
        <label>Agent <select [(ngModel)]="agent" [disabled]="chat.busy()" aria-label="Agent"><option value="claude">Claude Code</option><option value="codex">Codex</option></select></label>
        <button (click)="newChat()" [disabled]="chat.busy()">New Chat</button>
      </section>
      <div class="layout"><nav aria-label="Agent chat sessions"><button *ngFor="let session of chat.sessions()" (click)="open(session)" [disabled]="chat.busy()">{{session.title}} ยท {{session.chat_model}} / {{session.agent}}</button></nav>
      <section class="conversation">
        <p class="selection" *ngIf="chat.active() as session">Chat Model: {{session.chat_model === 'gpt' ? 'GPT' : 'Claude'}} ยท Agent: {{session.agent === 'claude' ? 'Claude Code' : 'Codex'}}</p>
        <article *ngFor="let message of chat.messages()" class="bubble" [class.user]="message.speaker_type === 'user'" [class.model]="message.speaker_type === 'model'" [class.agent]="message.speaker_type === 'agent'"><strong>{{speakerLabel(message)}}</strong><p>{{message.content}}</p><small class="message-status" *ngIf="message.status && message.status !== 'completed'">{{messageStatusLabel(message)}}</small></article>
        <p class="run-status" role="status">{{chat.status()}}</p><p role="alert" *ngIf="chat.error()">{{chat.error()}}</p>
        <form (ngSubmit)="send()"><textarea [(ngModel)]="draft" name="message" aria-label="Message" [disabled]="chat.busy() || !chat.active()" maxlength="18000"></textarea>
        <button type="submit" [disabled]="chat.busy() || !chat.active() || !draft.trim()">Send</button>
        <button type="button" (click)="chat.stop()" [disabled]="!chat.busy() || !chat.runId()">Stop</button></form>
      </section></div>
    </main>
  `,
  styles: [`
    :host {display:block;height:100vh;overflow:auto;color:var(--text-primary,#222);background:var(--bg-primary,#fff)}
    .agent-workspace {max-width:1200px;margin:auto;padding:24px} header,.selectors {display:flex;gap:16px;align-items:center;flex-wrap:wrap}
    header h1 {flex:1} .layout {display:grid;grid-template-columns:230px 1fr;gap:20px;margin-top:20px}
    nav button {display:block;width:100%;margin-bottom:8px} button,select {padding:10px;border-radius:8px}
    textarea {width:100%;min-height:90px;padding:12px;box-sizing:border-box} .bubble {padding:14px;border:1px solid #aaa;border-radius:12px;margin:12px 0}
    .bubble p {white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:0} .bubble.user {border-color:#999} .bubble.model {border-color:#5989ac} .bubble.agent {border-color:#8b69b4} .message-status,.run-status {display:inline-block;margin-top:8px;padding:3px 8px;border-radius:999px;background:#eee;color:#555;font-size:.85rem} [role=alert] {color:#bd3838}
    @media(max-width:700px){.layout{grid-template-columns:1fr}nav{max-height:160px;overflow:auto}}
  `]
})
export class AgentChatWorkspaceComponent implements OnInit {
  chat = inject(AgentChatService); theme = inject(ThemeService);
  model: ChatModelChoice = 'gpt'; agent: AgentChoice = 'claude'; draft = '';
  ngOnInit(): void { void this.chat.load().then(() => this.syncSelection()); }
  private syncSelection(): void {
    const active = this.chat.active();
    if (active) { this.model = active.chat_model; this.agent = active.agent; }
  }
  async open(session: AgentChatSession): Promise<void> {
    await this.chat.open(session);
    this.syncSelection();
  }
  async newChat(): Promise<void> {
    await this.chat.newChat(this.model, this.agent);
    this.syncSelection();
  }
  speakerLabel(message: {speaker_type: string; speaker_name: string}): string {
    if (message.speaker_type === 'user') return 'You';
    if (message.speaker_type === 'model') return message.speaker_name === 'gpt' ? 'GPT' : 'Claude';
    if (message.speaker_name === 'codex') return 'Codex';
    if (message.speaker_name === 'claude-code') return 'Claude Code';
    return 'Agent';
  }
  messageStatusLabel(message: {speaker_type: string; speaker_name: string; agent?: string; status?: string}): string {
    const who = message.speaker_type === 'user'
      ? (message.agent === 'claude' ? 'Claude Code' : 'Codex')
      : this.speakerLabel(message);
    const status: Record<string,string> = {
      queued:'queued', running:'running', cancelled:'cancelled', failed:'failed',
      provider_timeout:'timed out', bridge_timeout:'timed out'
    };
    return `${who} ? ${status[message.status ?? ''] ?? 'status updated'}`;
  }
  send(): void { const text = this.draft; this.draft=''; void this.chat.send(text,this.model,this.agent); }
}
