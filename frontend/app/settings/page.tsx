"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ChatModel,
  ProviderId,
  clearStoredApiKey,
  getStoredApiKeys,
  listChatModels,
  setStoredApiKey,
} from "../../lib/api";

const PROVIDERS: { id: ProviderId; label: string }[] = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "google", label: "Google" },
];

export default function SettingsPage() {
  const [models, setModels] = useState<ChatModel[]>([]);
  const [drafts, setDrafts] = useState<Partial<Record<ProviderId, string>>>({});
  const [saved, setSaved] = useState<Partial<Record<ProviderId, string>>>({});
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setSaved(getStoredApiKeys());
    listChatModels()
      .then((data) => setModels(data.models))
      .catch(() => setModels([]));
  }, []);

  const serverStatus = useMemo(() => {
    const status: Partial<Record<ProviderId, { available: boolean; envVar: string }>> = {};
    for (const model of models) {
      const id = model.provider as ProviderId;
      if (!status[id]) {
        status[id] = { available: model.available, envVar: model.requires_env_var };
      } else if (model.available) {
        status[id]!.available = true;
      }
    }
    return status;
  }, [models]);

  function handleSave(provider: ProviderId) {
    const value = drafts[provider] ?? "";
    setStoredApiKey(provider, value);
    setSaved(getStoredApiKeys());
    setDrafts((current) => ({ ...current, [provider]: "" }));
    setNotice(`${label(provider)} key saved to this browser.`);
  }

  function handleClear(provider: ProviderId) {
    clearStoredApiKey(provider);
    setSaved(getStoredApiKeys());
    setNotice(`${label(provider)} key removed from this browser.`);
  }

  function label(provider: ProviderId): string {
    return PROVIDERS.find((entry) => entry.id === provider)?.label ?? provider;
  }

  return (
    <section className="panel" style={{ maxWidth: 640 }}>
      <h2>Settings</h2>
      <p className="muted" style={{ marginTop: 0 }}>
        Add your own API key for a provider to unlock its models in the chat picker on
        this device, without an operator having to configure it server-side. Keys are
        stored only in this browser&apos;s local storage: they are sent to the API with a
        chat request and used for that request only, never written to a database or log.
      </p>

      {notice && (
        <div className="notice ok" style={{ marginBottom: 16 }}>
          {notice}
        </div>
      )}

      {PROVIDERS.map((provider) => {
        const status = serverStatus[provider.id];
        const hasStoredKey = Boolean(saved[provider.id]);
        return (
          <div
            key={provider.id}
            className="row"
            style={{ borderTop: "1px solid var(--border)", paddingTop: 16, marginTop: 16 }}
          >
            <div>
              <label htmlFor={`key-${provider.id}`}>{provider.label} API key</label>
              <input
                id={`key-${provider.id}`}
                type="password"
                placeholder={hasStoredKey ? "Saved (hidden)" : "sk-..."}
                value={drafts[provider.id] ?? ""}
                onChange={(event) =>
                  setDrafts((current) => ({ ...current, [provider.id]: event.target.value }))
                }
                autoComplete="off"
              />
              <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
                {hasStoredKey
                  ? "A personal key is saved in this browser and will be used for this provider's models."
                  : status?.available
                    ? `A server key is already configured (${status.envVar}); this is optional.`
                    : `Not configured server-side (${status?.envVar ?? "no key set"}). Add your own key to use this provider here.`}
              </p>
            </div>
            <div style={{ display: "flex", gap: 8, flex: "0 0 auto" }}>
              <button
                type="button"
                onClick={() => handleSave(provider.id)}
                disabled={!(drafts[provider.id] ?? "").trim()}
              >
                Save
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => handleClear(provider.id)}
                disabled={!hasStoredKey}
              >
                Clear
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
