import { Injectable, inject, signal } from '@angular/core';
import { AgentChatApiService } from './agent-chat-api.service';
import { AgentChatMessage, AgentChatSession, ChatModelChoice, AgentChoice, AgentChatEvent } from '../models/agent-chat.model';

const ACTIVE_SESSION_KEY = 'agent-chat-active-session-v1';
const BUSY_STATUSES = ['planning','queued','running','formatting','cancelling'];

@Injectable({providedIn: 'root'})
export class AgentChatService {
  private api = inject(AgentChatApiService);
  sessions = signal<AgentChatSession[]>([]);
  active = signal<AgentChatSession | null>(null);
  messages = signal<AgentChatMessage[]>([]);
  status = signal('idle');
  runId = signal<string | null>(null);
  error = signal('');
  private controller: AbortController | null = null;
  private openGeneration = 0;

  busy(): boolean { return BUSY_STATUSES.includes(this.status()); }

  async load(): Promise<void> {
    try {
      const sessions = await this.api.sessions();
      this.sessions.set(sessions);
      const savedId = localStorage.getItem(ACTIVE_SESSION_KEY);
      const saved = sessions.find(session => session.session_id === savedId);
      if (saved) await this.open(saved);
    } catch { this.error.set('Unable to load Agent Chat.'); }
  }

  async newChat(model: ChatModelChoice, agent: AgentChoice): Promise<void> {
    if (this.busy()) return;
    try {
      const chat = await this.api.create(model,agent);
      this.sessions.update(v => [chat,...v]);
      await this.open(chat);
    } catch { this.error.set('Unable to create Agent Chat.'); }
  }

  async open(chat: AgentChatSession): Promise<void> {
    if (this.busy()) return;
    const generation = ++this.openGeneration;
    this.active.set(chat);
    this.messages.set([]);
    this.runId.set(null);
    this.error.set('');
    this.status.set('idle');
    try { localStorage.setItem(ACTIVE_SESSION_KEY, chat.session_id); } catch { /* optional reload convenience */ }
    try {
      const messages = await this.api.messages(chat.session_id);
      if (generation === this.openGeneration) this.messages.set(messages);
    } catch { if (generation === this.openGeneration) this.error.set('Unable to load messages.'); }
  }

  private upsertMessage(message: AgentChatMessage): void {
    const session = this.active();
    if (!session || message.session_id !== session.session_id) return;
    this.messages.update(current => {
      const index = current.findIndex(item => item.message_id === message.message_id);
      if (index < 0) return [...current, message];
      const updated = [...current];
      updated[index] = message;
      return updated;
    });
  }

  private consume(event: AgentChatEvent): void {
    if (!['message','status','done','error'].includes(event.type)) return;
    if (event.run_id) this.runId.set(event.run_id);
    if (event.type === 'message') {
      if (!event.message_id || !event.session_id || !event.speaker_type ||
          !event.speaker_name || typeof event.content !== 'string') return;
      this.upsertMessage({
        message_id: event.message_id,
        session_id: event.session_id,
        speaker_type: event.speaker_type,
        speaker_name: event.speaker_name,
        content: event.content,
        created_at: event.created_at,
        status: event.status,
        bridge_task_id: event.bridge_task_id,
        chat_model: event.chat_model,
        agent: event.agent,
        stage: event.stage
      });
      return;
    }
    if (event.status) this.status.set(event.status);
    if (event.session_id && event.chat_model && event.agent) {
      const current = this.active();
      if (current?.session_id === event.session_id &&
          (current.chat_model !== event.chat_model || current.agent !== event.agent)) {
        const updated = {...current, chat_model: event.chat_model, agent: event.agent};
        this.active.set(updated);
        this.sessions.update(items => items.map(item => item.session_id === updated.session_id ? updated : item));
      }
    }
    if (event.type === 'error' && event.message &&
        !this.messages().some(message => message.content === event.message)) {
      this.error.set(event.message);
    }
  }

  private async refreshActiveMessages(sessionId: string): Promise<void> {
    if (this.active()?.session_id !== sessionId) return;
    try {
      const messages = await this.api.messages(sessionId);
      if (this.active()?.session_id === sessionId) this.messages.set(messages);
    } catch { /* streamed transcript remains visible if refresh is unavailable */ }
  }

  async send(content: string, chatModel?: ChatModelChoice, selectedAgent?: AgentChoice): Promise<void> {
    const chat = this.active();
    if (!chat || this.busy() || !content.trim()) return;
    const model = chatModel ?? chat.chat_model;
    const agent = selectedAgent ?? chat.agent;
    if (chat.chat_model !== model || chat.agent !== agent) {
      const updated = {...chat, chat_model: model, agent};
      this.active.set(updated);
      this.sessions.update(items => items.map(item => item.session_id === updated.session_id ? updated : item));
    }
    const messageId = crypto.randomUUID();
    this.upsertMessage({
      message_id: messageId,
      session_id: chat.session_id,
      speaker_type: 'user',
      speaker_name: 'user',
      content,
      status: 'queued',
      chat_model: chat.chat_model,
      agent: chat.agent,
      stage: 'planning',
      role: 'user'
    });
    this.error.set('');
    this.status.set('planning');
    this.runId.set(null);
    const controller = new AbortController();
    this.controller = controller;
    try {
      await this.api.stream({
        session_id:chat.session_id,chat_model:model,agent,
        message:content,message_id:messageId
      }, event => this.consume(event), controller.signal);
      if (this.busy()) {
        this.status.set('failed');
        this.error.set('Agent Chat connection ended before completion.');
      }
    } catch {
      this.status.set('failed');
      this.error.set('Agent Chat is unavailable. Please try again.');
    } finally {
      this.controller = null;
      await this.refreshActiveMessages(chat.session_id);
    }
  }

  async stop(): Promise<void> {
    const chat = this.active(), run = this.runId();
    if (!chat || !run || !this.busy()) return;
    this.status.set('cancelling');
    try {
      await this.api.cancel(chat.session_id,run);
    } catch { this.error.set('Cancellation could not be confirmed.'); }
  }
}
