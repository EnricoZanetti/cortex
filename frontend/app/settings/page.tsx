"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "../../lib/auth-context";

const API_KEY_STORAGE = "cortex_api_key";

export function getStoredApiKey(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(API_KEY_STORAGE) ?? "";
}

export default function SettingsPage() {
  const { user, loading, refresh } = useAuth();
  const router = useRouter();
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.push("/login");
  }, [loading, user, router]);

  useEffect(() => {
    setApiKey(getStoredApiKey());
  }, []);

  if (loading || !user) return null;

  function save(event: React.FormEvent) {
    event.preventDefault();
    window.localStorage.setItem(API_KEY_STORAGE, apiKey.trim());
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function clear() {
    window.localStorage.removeItem(API_KEY_STORAGE);
    setApiKey("");
  }

  return (
    <section className="panel" style={{ maxWidth: 480, margin: "0 auto" }}>
      <h2>Settings</h2>

      <div className="notice ok" style={{ marginBottom: 16 }}>
        Signed in as <strong>{user.username}</strong> ({user.email})
        {user.role === "admin" ? (
          <>; admin account, unlimited chat turns.</>
        ) : (
          <>; {user.free_runs_remaining} free chat turn(s) remaining.</>
        )}
      </div>

      <h3 style={{ marginBottom: 4 }}>Your own API key</h3>
      <p className="muted">
        Once your free turns run out, add a personal API key for the chosen model's
        provider to keep chatting on your own account. Stored only in this browser;
        never sent anywhere except with your own chat requests.
      </p>
      <form onSubmit={save}>
        <label htmlFor="apiKey">Personal API key</label>
        <input
          id="apiKey"
          type="password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder="sk-..."
        />
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit">Save</button>
          <button type="button" className="secondary" onClick={clear}>
            Clear
          </button>
        </div>
        {saved && <div className="notice ok">Saved.</div>}
      </form>

      <button
        type="button"
        className="link neutral"
        style={{ marginTop: 16 }}
        onClick={() => void refresh()}
      >
        Refresh account status
      </button>
    </section>
  );
}
