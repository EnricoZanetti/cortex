"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatEvent, ChatModel, Citation, chatHealth, listChatModels, streamChat } from "../../lib/api";

/** One tool invocation, shown inline so the user sees the agent's reasoning path. */
interface ToolActivity {
  name: string;
  arguments: Record<string, unknown>;
  resultCount: number | null;
  isError: boolean;
  done: boolean;
}

interface Turn {
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  citations: Citation[];
  error: string | null;
}

/** A pre-written question plus a hint on which MCP tool(s) it is designed to exercise. */
interface Suggestion {
  question: string;
  hint: string;
}

const GENERAL_SUGGESTIONS: Suggestion[] = [
  {
    question: "What documents do we have about onboarding?",
    hint: "Tool: list_documents; browses inventory metadata (filenames, tags) without reading content.",
  },
  {
    question: "What topics does the knowledge base cover?",
    hint: "Tool: list_tags; lists the curated topic vocabulary (compliance, product, hr, onboarding...) and how many documents carry each.",
  },
  {
    question: "Summarise the AML policy for me.",
    hint: "Tool: get_document_summary; returns a document's title, tags and section outline without a full retrieval pass.",
  },
  {
    question: "Only using compliance policies, what triggers enhanced due diligence?",
    hint: "Tool: search_by_tag; restricts retrieval to documents tagged 'compliance', after list_tags confirms the tag exists.",
  },
];

const SPECIFIC_SUGGESTIONS: Suggestion[] = [
  {
    question: "How quickly must I report a suspicious transaction?",
    hint: "Tool: search; hybrid semantic and keyword search across the whole corpus; finds the AML policy's 24-hour SAR rule.",
  },
  {
    question: "What are the instruction cut-off times for cross-border payments?",
    hint: "Tool: search; pulls the cut-off table from the Vault Product Manual.",
  },
  {
    question: "How much annual leave can I carry over?",
    hint: "Tool: search; matches the HR FAQ export's leave carry-over answer.",
  },
  {
    question: "When is the capital adequacy return due, and who owns it?",
    hint: "Tool: search; reads the filing table inside the Regulatory Filing Calendar PDF.",
  },
  {
    question: "Compare the custody fee rate across all three Vault account tiers.",
    hint: "Tool: search_by_document; targets the Vault Fee Schedule specifically instead of the whole corpus.",
  },
];

export default function ChatPage() {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState<string>("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The knowledge base runs on a free-tier deploy that spins down when idle. This tracks
  // that separately from `loadError` so a cold start reads as "getting ready", not a fault.
  const [kbReady, setKbReady] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listChatModels()
      .then((data) => {
        setModels(data.models);
        if (data.default) setModel(data.default);
      })
      .catch((error) => setLoadError(String(error)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    // Loading this page is the only thing that reaches the MCP server before the user
    // sends a message, so it also has to be what wakes it: poll health until it answers
    // "ok" rather than making the user's first question absorb the cold-start delay.
    const check = () => {
      chatHealth()
        .then((health) => {
          if (cancelled) return;
          if (health.mcp_server === "ok") {
            setKbReady(true);
          } else {
            timer = setTimeout(check, 4000);
          }
        })
        .catch(() => {
          if (!cancelled) timer = setTimeout(check, 4000);
        });
    };
    check();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const availableModels = useMemo(() => models.filter((entry) => entry.available), [models]);

  const send = useCallback(
    async (question: string) => {
      if (!question.trim() || busy || !model || !kbReady) return;

      const history = [
        ...turns.map((turn) => ({ role: turn.role, content: turn.content })),
        { role: "user", content: question },
      ];

      setTurns((current) => [
        ...current,
        { role: "user", content: question, tools: [], citations: [], error: null },
        { role: "assistant", content: "", tools: [], citations: [], error: null },
      ]);
      setInput("");
      setBusy(true);

      const controller = new AbortController();
      abortRef.current = controller;

      // Mutate only the final (assistant) turn as events arrive.
      const patch = (fn: (turn: Turn) => Turn) =>
        setTurns((current) =>
          current.map((turn, index) => (index === current.length - 1 ? fn(turn) : turn)),
        );

      try {
        for await (const event of streamChat(model, history, controller.signal)) {
          applyEvent(event, patch);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          patch((turn) => ({ ...turn, error: String(error) }));
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    [busy, model, turns, kbReady],
  );

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  return (
    <section className="panel chat-panel">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>Ask Cortex</h2>
        <div>
          <label htmlFor="model" style={{ display: "inline", marginRight: 8 }}>
            Model
          </label>
          <select
            id="model"
            value={model}
            onChange={(event) => setModel(event.target.value)}
            style={{ width: "auto" }}
            disabled={busy}
          >
            {models.map((entry) => (
              <option key={entry.id} value={entry.id} disabled={!entry.available}>
                {entry.provider_label} · {entry.label}
                {entry.available ? "" : ` (set ${entry.requires_env_var})`}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!kbReady && (
        <div className="notice ok">Getting things ready, this can take up to a minute…</div>
      )}
      {loadError && <div className="notice error">Could not load models: {loadError}</div>}
      {!loadError && models.length > 0 && availableModels.length === 0 && (
        <div className="notice error">
          No chat model is configured. Set at least one of{" "}
          {[...new Set(models.map((entry) => entry.requires_env_var))].join(", ")} in the
          environment and restart the API.
        </div>
      )}

      <div className="chat-log">
        {turns.length === 0 && (
          <div className="chat-empty">
            <p className="muted">
              Ask a question in plain language. The assistant searches the uploaded
              documents through the MCP server and answers with citations.
            </p>
            <div className="muted" style={{ marginTop: 8 }}>
              General: what the knowledge base contains
            </div>
            <div className="suggestions">
              {GENERAL_SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion.question}
                  className="secondary"
                  title={suggestion.hint}
                  onClick={() => void send(suggestion.question)}
                  disabled={busy || !kbReady || availableModels.length === 0}
                >
                  {suggestion.question}
                </button>
              ))}
            </div>

            <div className="muted" style={{ marginTop: 12 }}>
              Specific: facts pulled from a single passage or document
            </div>
            <div className="suggestions">
              {SPECIFIC_SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion.question}
                  className="secondary"
                  title={suggestion.hint}
                  onClick={() => void send(suggestion.question)}
                  disabled={busy || !kbReady || availableModels.length === 0}
                >
                  {suggestion.question}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, index) => (
          <div key={index} className={`bubble ${turn.role}`}>
            <div className="bubble-role">{turn.role === "user" ? "You" : "Assistant"}</div>

            {turn.tools.length > 0 && (
              <div className="tools">
                {turn.tools.map((tool, toolIndex) => (
                  <span
                    key={toolIndex}
                    className={`tool-chip ${tool.isError ? "err" : tool.done ? "ok" : "running"}`}
                    title={JSON.stringify(tool.arguments, null, 2)}
                  >
                    {tool.name}
                    {tool.resultCount !== null ? ` · ${tool.resultCount}` : ""}
                  </span>
                ))}
              </div>
            )}

            {turn.content && (
              <div className="bubble-text">
                {turn.role === "assistant" ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.content}</ReactMarkdown>
                ) : (
                  <span style={{ whiteSpace: "pre-wrap" }}>{turn.content}</span>
                )}
              </div>
            )}
            {turn.role === "assistant" && !turn.content && !turn.error && busy && (
              <div className="muted">Thinking…</div>
            )}
            {turn.error && <div className="notice error">{turn.error}</div>}

            {turn.citations.length > 0 && (
              <div className="citations">
                <div className="muted">Sources</div>
                {turn.citations.map((citation, citationIndex) => (
                  <div key={citationIndex} className="citation">
                    <strong>{citation.filename}</strong>
                    {citation.heading_path ? ` · ${citation.heading_path}` : ""}
                    {citation.page ? ` · p${citation.page}` : ""}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <form
        className="chat-input"
        onSubmit={(event) => {
          event.preventDefault();
          void send(input);
        }}
      >
        <input
          type="text"
          value={input}
          placeholder="Ask about a policy, a process, or a product…"
          onChange={(event) => setInput(event.target.value)}
          disabled={busy || !kbReady || availableModels.length === 0}
        />
        {busy ? (
          <button type="button" className="secondary" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim() || !kbReady || availableModels.length === 0}>
            Send
          </button>
        )}
      </form>
    </section>
  );
}

/** Fold one streamed event into the in-progress assistant turn. */
function applyEvent(event: ChatEvent, patch: (fn: (turn: Turn) => Turn) => void) {
  switch (event.type) {
    case "text":
      patch((turn) => ({ ...turn, content: turn.content + event.text }));
      break;
    case "tool_call":
      patch((turn) => ({
        ...turn,
        tools: [
          ...turn.tools,
          {
            name: event.name,
            arguments: event.arguments,
            resultCount: null,
            isError: false,
            done: false,
          },
        ],
      }));
      break;
    case "tool_result":
      patch((turn) => {
        const tools = [...turn.tools];
        // Complete the most recent pending call with this tool's name.
        for (let index = tools.length - 1; index >= 0; index -= 1) {
          if (tools[index].name === event.name && !tools[index].done) {
            tools[index] = {
              ...tools[index],
              done: true,
              isError: event.is_error,
              resultCount: event.result_count,
            };
            break;
          }
        }
        // Deduplicate citations: several searches often return the same passage.
        const seen = new Set(turn.citations.map((c) => c.chunk_id));
        const added = event.citations.filter((c) => c.chunk_id && !seen.has(c.chunk_id));
        return { ...turn, tools, citations: [...turn.citations, ...added] };
      });
      break;
    case "error":
      patch((turn) => ({ ...turn, error: event.message }));
      break;
    case "done":
      break;
  }
}
