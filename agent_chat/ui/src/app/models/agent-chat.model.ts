export type ChatModelChoice = 'gpt' | 'claude';
export type AgentChoice = 'claude' | 'codex';
export type AgentChatSpeakerType = 'user' | 'model' | 'agent';

export interface AgentChatSession {
  session_id: string; title: string; chat_model: ChatModelChoice; agent: AgentChoice;
}

export interface AgentChatMessage {
  message_id: string;
  session_id: string;
  speaker_type: AgentChatSpeakerType;
  speaker_name: string;
  content: string;
  created_at?: number;
  status?: string;
  bridge_task_id?: string | null;
  chat_model?: ChatModelChoice;
  agent?: AgentChoice;
  stage?: string | null;
  role?: 'user' | 'assistant';
}

export interface AgentChatEvent {
  type: string;
  run_id?: string;
  session_id?: string;
  status?: string;
  stage?: string;
  chat_model?: ChatModelChoice;
  agent?: AgentChoice;
  error_code?: string;
  message?: string;
  message_id?: string;
  speaker_type?: AgentChatSpeakerType;
  speaker_name?: string;
  content?: string;
  created_at?: number;
  bridge_task_id?: string | null;
}
