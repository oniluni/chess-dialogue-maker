/** Shapes exchanged between the UI and the local LLM proxy. */

export interface LlmProviderInfo {
  id: string;
  label: string;
  local: boolean;
  needsKey: boolean;
  configured: boolean;
  defaultModel: string;
  suggested: string[];
}

export interface LlmModelInfo {
  id: string;
  label: string;
}

export interface LlmChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface LlmChatRequest {
  provider: string;
  model: string;
  messages: LlmChatMessage[];
  schema?: Record<string, unknown>;
  temperature?: number;
}

export interface LlmChatResponse {
  text: string;
}

export interface LlmErrorBody {
  error: "llm_error";
  title: string;
  hint: string;
}
