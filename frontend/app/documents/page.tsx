"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Document,
  Tag,
  deleteDocument,
  listDocuments,
  listTags,
  reprocessDocument,
  uploadDocument,
} from "../../lib/api";

const IN_PROGRESS: Document["status"][] = ["pending", "processing"];

export default function Home() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [filterTag, setFilterTag] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [tagInput, setTagInput] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(
    null,
  );
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [docs, tagList] = await Promise.all([
        listDocuments(filterTag || undefined),
        listTags(),
      ]);
      setDocuments(docs.documents);
      setTags(tagList);
    } catch (error) {
      setNotice({ kind: "error", text: `Could not load documents: ${error}` });
    }
  }, [filterTag]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Ingestion runs in the background, so poll while anything is still processing.
  const hasPending = useMemo(
    () => documents.some((doc) => IN_PROGRESS.includes(doc.status)),
    [documents],
  );
  useEffect(() => {
    if (!hasPending) return;
    const timer = setInterval(() => void refresh(), 2000);
    return () => clearInterval(timer);
  }, [hasPending, refresh]);

  const pendingTags = useMemo(() => {
    const typed = tagInput
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    return Array.from(new Set([...selectedTags, ...typed]));
  }, [selectedTags, tagInput]);

  async function handleUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setNotice(null);
    try {
      const result = await uploadDocument(file, pendingTags);
      setNotice({
        kind: "ok",
        text: result.duplicate
          ? `${result.document.filename}: ${result.message}`
          : `Uploaded ${result.document.filename}: ingestion started.`,
      });
      setFile(null);
      setTagInput("");
      setSelectedTags([]);
      if (fileInput.current) fileInput.current.value = "";
      await refresh();
    } catch (error) {
      setNotice({ kind: "error", text: `Upload failed: ${error}` });
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(doc: Document) {
    if (!confirm(`Delete "${doc.filename}" and all of its indexed passages?`)) return;
    try {
      await deleteDocument(doc.id);
      setNotice({ kind: "ok", text: `Deleted ${doc.filename}.` });
      await refresh();
    } catch (error) {
      setNotice({ kind: "error", text: `Delete failed: ${error}` });
    }
  }

  async function handleReprocess(doc: Document) {
    try {
      await reprocessDocument(doc.id);
      setNotice({ kind: "ok", text: `Re-processing ${doc.filename}.` });
      await refresh();
    } catch (error) {
      setNotice({ kind: "error", text: `Re-process failed: ${error}` });
    }
  }

  function toggleTag(name: string) {
    setSelectedTags((current) =>
      current.includes(name)
        ? current.filter((tag) => tag !== name)
        : [...current, name],
    );
  }

  return (
    <>
      <section className="panel">
        <h2>Upload a document</h2>
        {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}
        <form onSubmit={handleUpload}>
          <div className="row">
            <div>
              <label htmlFor="file">File (PDF, TXT or Markdown)</label>
              <input
                id="file"
                type="file"
                ref={fileInput}
                accept=".pdf,.txt,.md,.markdown"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </div>
            <div>
              <label htmlFor="tags">Tags (comma separated)</label>
              <input
                id="tags"
                type="text"
                placeholder="compliance, policy"
                value={tagInput}
                onChange={(event) => setTagInput(event.target.value)}
              />
            </div>
            <div style={{ flex: "0 0 auto" }}>
              <button type="submit" disabled={!file || busy}>
                {busy ? "Uploading…" : "Upload"}
              </button>
            </div>
          </div>
        </form>

        {tags.length > 0 && (
          <p style={{ marginBottom: 0 }}>
            <span className="muted">Existing tags, click to apply: </span>
            <br />
            {tags.map((tag) => (
              <span
                key={tag.name}
                className={`tag selectable ${
                  selectedTags.includes(tag.name) ? "selected" : ""
                }`}
                onClick={() => toggleTag(tag.name)}
              >
                {tag.name}
              </span>
            ))}
          </p>
        )}
      </section>

      <section className="panel">
        <div className="toolbar">
          <h2 style={{ margin: 0 }}>
            Documents <span className="muted">({documents.length})</span>
          </h2>
          <div>
            <label htmlFor="filter" style={{ display: "inline", marginRight: 8 }}>
              Filter by tag
            </label>
            <select
              id="filter"
              value={filterTag}
              onChange={(event) => setFilterTag(event.target.value)}
              style={{ width: "auto" }}
            >
              <option value="">All tags</option>
              {tags.map((tag) => (
                <option key={tag.name} value={tag.name}>
                  {tag.name} ({tag.document_count})
                </option>
              ))}
            </select>
          </div>
        </div>

        {documents.length === 0 ? (
          <p className="muted">
            No documents yet. Upload one above, or run <code>make seed</code> to load the
            sample corpus.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Filename</th>
                <th>Tags</th>
                <th>Status</th>
                <th>Passages</th>
                <th>Uploaded</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <strong>{doc.filename}</strong>
                    {doc.error && (
                      <div className="muted" style={{ color: "var(--danger)" }}>
                        {doc.error}
                      </div>
                    )}
                    {doc.status === "duplicate" && (
                      <div className="muted">{doc.summary}</div>
                    )}
                  </td>
                  <td>
                    {doc.tags.map((tag) => (
                      <span className="tag" key={tag}>
                        {tag}
                      </span>
                    ))}
                  </td>
                  <td>
                    <span className={`status ${doc.status}`}>{doc.status}</span>
                  </td>
                  <td>
                    {doc.chunk_count}
                    {doc.page_count ? (
                      <span className="muted"> · {doc.page_count}p</span>
                    ) : null}
                  </td>
                  <td className="muted">
                    {new Date(doc.created_at).toLocaleString()}
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {doc.status === "failed" && (
                      <button
                        className="link neutral"
                        onClick={() => handleReprocess(doc)}
                        style={{ marginRight: 10 }}
                      >
                        retry
                      </button>
                    )}
                    <button className="link" onClick={() => handleDelete(doc)}>
                      delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
