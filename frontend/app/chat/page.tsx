"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatEvent, ChatModel, Citation, listChatModels, streamChat } from "../../lib/api";

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

const SUGGESTIONS = [
  "How quickly must I report a suspicious transaction?",
  "How much annual leave can I carry over?",
  "What are the instruction cut-off times for cross-border payments?",
  "What documents do we have about onboarding?",
];

export default function ChatPage() {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState<string>("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const availableModels = useMemo(() => models.filter((entry) => entry.available), [models]);

  const send = useCallback(
    async (question: string) => {
      if (!question.trim() || busy || !model) return;

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
    [busy, model, turns],
  );

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  return (
    <section className="panel chat-panel">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>Ask the knowledge base</h2>
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
            <div className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  className="secondary"
                  onClick={() => void send(suggestion)}
                  disabled={busy || availableModels.length === 0}
                >
                  {suggestion}
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

            {turn.content && <div className="bubble-text">{turn.content}</div>}
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
          disabled={busy || availableModels.length === 0}
        />
        {busy ? (
          <button type="button" className="secondary" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim() || availableModels.length === 0}>
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
