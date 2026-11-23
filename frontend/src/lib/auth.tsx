import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type { Role, User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  needsSetup: boolean;
  login: (email: string, password: string) => Promise<void>;
  setup: (email: string, name: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (role: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };
const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    (async () => {
      try {
        setUser(await api.get<User>("/auth/me"));
      } catch {
        try {
          const s = await api.get<{ needs_setup: boolean }>("/auth/setup-status");
          setNeedsSetup(s.needs_setup);
        } catch {
          /* backend unreachable; login page will show errors */
        }
      } finally {
        setLoading(false);
      }
    })();
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api.post<{ user: User }>("/auth/login", { email, password });
    setUser(r.user);
  }, []);

  const setup = useCallback(async (email: string, name: string, password: string) => {
    const r = await api.post<{ user: User }>("/auth/setup", { email, name, password });
    setNeedsSetup(false);
    setUser(r.user);
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout").catch(() => undefined);
    setUser(null);
  }, []);

  const can = useCallback((role: Role) => !!user && RANK[user.role] >= RANK[role], [user]);

  return (
    <AuthContext.Provider value={{ user, loading, needsSetup, login, setup, logout, can }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
