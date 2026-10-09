import { Injectable } from '@angular/core';
import { AgentChatSession, AgentChatMessage, AgentChatEvent, ChatModelChoice, AgentChoice } from '../models/agent-chat.model';

@Injectable({providedIn: 'root'})
export class AgentChatApiService {
  private async request(path: string, body?: unknown, method?: string): Promise<any> {
    const response = await fetch('/api/agent/' + path, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      credentials: 'same-origin',
      headers: body === undefined ? {} : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) throw new Error('Agent Chat request failed.');
    return response.json();
  }
  sessions(): Promise<AgentChatSession[]> { return this.request('sessions'); }
  create(chat_model: ChatModelChoice, agent: AgentChoice): Promise<AgentChatSession> {
    return this.request('sessions', {chat_model, agent});
  }
  messages(id: string): Promise<AgentChatMessage[]> { return this.request('sessions/' + encodeURIComponent(id) + '/messages'); }
  cancel(session_id: string, run_id: string): Promise<any> { return this.request('chat/cancel', {session_id, run_id}); }
  async stream(body: unknown, emit: (event: AgentChatEvent) => void, signal: AbortSignal): Promise<void> {
    const response = await fetch('/api/agent/chat/stream', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body), signal
    });
    if (!response.ok || !response.body) throw new Error('Agent Chat request failed.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      while (true) {
        const {done, value} = await reader.read();
        buffer += decoder.decode(value, {stream: !done}).replace(/\r\n/g, '\n');
        let end: number;
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const frame = buffer.slice(0,end); buffer = buffer.slice(end+2);
          const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
          if (data) emit(JSON.parse(data));
        }
        if (done) break;
      }
    } finally { reader.releaseLock(); }
  }
}
