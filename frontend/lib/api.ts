/**
 * Thin client for the management REST API.
 *
 * All calls run in the browser, so the base URL must be a public one; it is injected at
 * build time via NEXT_PUBLIC_API_URL and defaults to the local compose setup.
 */

// Render's `fromService`/`property: host` (used in render.yaml) yields a bare hostname
// with no scheme, so a missing "http(s)://" is coerced to https rather than left to break
// every fetch call.
const RAW_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_URL = /^https?:\/\//.test(RAW_API_URL) ? RAW_API_URL : `https://${RAW_API_URL}`;

export type DocumentStatus =
  | "pending"
  | "processing"
  | "ready"
  | "failed"
  | "duplicate";

export interface Document {
  id: string;
  filename: string;
  title: string | null;
  tags: string[];
  status: DocumentStatus;
  chunk_count: number;
  page_count: number | null;
  byte_size: number;
  content_type: string;
  summary: string | null;
  error: string | null;
  duplicate_of: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentList {
  documents: Document[];
  total: number;
  limit: number;
  offset: number;
}

export interface Tag {
  name: string;
  document_count: number;
}

export interface UploadResult {
  document: Document;
  duplicate: boolean;
  tags_added: string[];
  message: string;
}

const TOKEN_KEY = "cortex_token";

/** Read the stored login token, if any. Guarded for SSR: this module also runs server-side. */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

/** Authorization header for the current login, or {} when logged out. */
export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers: { ...authHeader(), ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    // The API returns {"detail": "..."} for handled errors; fall back to the status text.
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

/* -------------------------------------------------------------------- auth */

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  role: "user" | "admin";
  free_runs_remaining: number;
}

export interface AuthResult {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export function signup(email: string, username: string, password: string): Promise<AuthResult> {
  return request<AuthResult>("/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password }),
  });
}

export function login(identifier: string, password: string): Promise<AuthResult> {
  return request<AuthResult>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, password }),
  });
}

export function getMe(): Promise<AuthUser> {
  return request<AuthUser>("/auth/me");
}

export function listDocuments(tag?: string): Promise<DocumentList> {
  const params = new URLSearchParams({ limit: "100", sort: "recent" });
  if (tag) params.append("tags", tag);
  return request<DocumentList>(`/documents?${params.toString()}`);
}

export function listTags(): Promise<Tag[]> {
  return request<Tag[]>("/tags");
}

export function uploadDocument(file: File, tags: string[]): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  // Repeated fields rather than one comma-joined value: the API accepts both, but this
  // keeps tags containing commas intact.
  tags.forEach((tag) => form.append("tags", tag));
  return request<UploadResult>("/documents", { method: "POST", body: form });
}

export function deleteDocument(id: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/documents/${id}`, { method: "DELETE" });
}

export function reprocessDocument(id: string): Promise<Document> {
  return request<Document>(`/documents/${id}/reprocess`, { method: "POST" });
}

export function updateDocumentTags(id: string, tags: string[]): Promise<Document> {
  return request<Document>(`/documents/${id}/tags`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
}

/* ------------------------------------------------------------------ chat */

export interface ChatModel {
  id: string;
  label: string;
  provider: string;
  provider_label: string;
  description: string;
  available: boolean;
  requires_env_var: string;
}

export interface ChatModelList {
  models: ChatModel[];
  default: string | null;
}

export interface Citation {
  filename: string | null;
  heading_path: string | null;
  page: number | null;
  document_id: string | null;
  chunk_id: string | null;
  relevance: number | null;
}

/** Events streamed by POST /chat, mirroring kb.agent.events. */
export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool_call"; name: string; arguments: Record<string, unknown> }
  | {
      type: "tool_result";
      name: string;
      is_error: boolean;
      result_count: number | null;
      citations: Citation[];
      guidance: string | null;
    }
  | { type: "error"; message: string }
  | { type: "done"; stop_reason: string | null };

export function listChatModels(): Promise<ChatModelList> {
  return request<ChatModelList>("/chat/models");
}

export interface ChatHealth {
  mcp_server: string;
  mcp_server_url: string;
  tools: string[];
  models_available: number;
}

/**
 * Reaches all the way to the MCP server, so calling this is also how the free-tier
 * deploy gets woken up: a plain page load only touches the API, never the MCP server
 * itself, so without this the first real chat message is what pays the cold-start cost.
 */
export function chatHealth(): Promise<ChatHealth> {
  return request<ChatHealth>("/chat/health");
}

/**
 * Stream one chat turn.
 *
 * Uses fetch + a ReadableStream rather than EventSource because EventSource
 * cannot issue a POST, and the conversation history has to go in the body.
 */
export async function* streamChat(
  model: string,
  messages: { role: string; content: string }[],
  apiKey?: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeader() },
    body: JSON.stringify({ model, messages, api_key: apiKey || undefined }),
    signal,
  });

  if (!response.ok || !response.body) {
    // The API returns {"detail": "..."} for handled errors (e.g. 402 quota exhausted,
    // 401 not logged in); surface that instead of a bare status code where possible.
    let detail = `Chat request failed: ${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; keep any partial frame buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        yield JSON.parse(line.slice(6)) as ChatEvent;
      } catch {
        /* ignore a frame we cannot parse rather than killing the stream */
      }
    }
  }
}
