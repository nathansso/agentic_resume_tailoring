import { createContext, useContext, useState, useEffect, type ReactNode } from "react";
import type { User } from "../types";
import { getAuthCapabilities, getMe, logout as apiLogout } from "../api/auth";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  /** `"none"` in `art ui` local mode (#204): no login, no sign-out. */
  authMode: string;
  /** Shorthand for `authMode === "none"`. */
  localMode: boolean;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState("local");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // A failed capabilities call must not strand the app on its splash screen.
    const caps = getAuthCapabilities().catch(() => ({ auth_mode: "local" }));
    Promise.all([getMe(), caps]).then(([u, caps]) => {
      setUser(u);
      setAuthMode(caps.auth_mode);
      setLoading(false);
    });
  }, []);

  async function logout() {
    await apiLogout();
    setUser(null);
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, authMode, localMode: authMode === "none", setUser, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
