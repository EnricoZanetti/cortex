"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "../../lib/auth-context";

export default function SignupPage() {
  const { signup } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signup(email, username, password);
      router.push("/chat");
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" style={{ maxWidth: 420, margin: "0 auto" }}>
      <h2>Sign up</h2>
      <p className="muted">
        Get a few free chat turns on us. After that, add your own API key in Settings to
        keep going.
      </p>
      {error && <div className="notice error">{error}</div>}
      <form onSubmit={onSubmit}>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="text"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <label htmlFor="username">Username</label>
        <input
          id="username"
          type="text"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          minLength={3}
          maxLength={32}
          pattern="[A-Za-z0-9_-]+"
          title="Letters, numbers, underscores and hyphens only."
          required
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          minLength={8}
          required
        />
        <button type="submit" disabled={busy}>
          {busy ? "Signing up…" : "Sign up"}
        </button>
      </form>
      <p className="muted" style={{ marginTop: 12 }}>
        Already have an account? <Link href="/login">Log in</Link>
      </p>
    </section>
  );
}
