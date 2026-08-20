"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import {
  AuthUser,
  getMe,
  getToken,
  login as apiLogin,
  setToken as storeToken,
  signup as apiSignup,
} from "./api";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  signup: (email: string, username: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      return;
    }
    try {
      setUser(await getMe());
    } catch {
      // Token expired or invalid server-side: drop it rather than loop on 401s.
      storeToken(null);
      setUser(null);
    }
  }, []);

  useEffect(() => {
    void refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (identifier: string, password: string) => {
    const result = await apiLogin(identifier, password);
    storeToken(result.access_token);
    setUser(result.user);
  }, []);

  const signup = useCallback(async (email: string, username: string, password: string) => {
    const result = await apiSignup(email, username, password);
    storeToken(result.access_token);
    setUser(result.user);
  }, []);

  const logout = useCallback(() => {
    storeToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
