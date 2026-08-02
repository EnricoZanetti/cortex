/**
 * Thin client for the management REST API.
 *
 * All calls run in the browser, so the base URL must be a public one; it is injected at
 * build time via NEXT_PUBLIC_API_URL and defaults to the local compose setup.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    cache: "no-store",
    ...init,
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
