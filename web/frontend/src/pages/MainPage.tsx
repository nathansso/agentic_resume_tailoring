import type { CSSProperties } from "react";
import { useAuth } from "../context/AuthContext";

export function MainPage() {
  const { user, logout } = useAuth();

  return (
    <div style={s.page}>
      <header style={s.header}>
        <span style={s.brand}>ART</span>
        <div style={s.headerRight}>
          <span style={s.userName}>{user?.name}</span>
          <button onClick={logout} style={s.signOut}>Sign out</button>
        </div>
      </header>
      <main style={s.main}>
        <p style={s.placeholder}>
          Dashboard coming in Phase 2 — chat, jobs, and data explorer.
        </p>
      </main>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: { display: "flex", flexDirection: "column", minHeight: "100vh", background: "#0f172a", color: "#f1f5f9" },
  header: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "1rem 1.5rem", borderBottom: "1px solid #1e293b" },
  brand: { fontWeight: 700, fontSize: "1.25rem", letterSpacing: "-0.5px" },
  headerRight: { display: "flex", alignItems: "center", gap: "1rem" },
  userName: { color: "#94a3b8", fontSize: "0.875rem" },
  signOut: { padding: "0.375rem 0.75rem", borderRadius: "6px", background: "#1e293b", color: "#f1f5f9", border: "1px solid #334155", cursor: "pointer", fontSize: "0.8rem" },
  main: { flex: 1, padding: "2rem 1.5rem" },
  placeholder: { color: "#64748b" },
};
