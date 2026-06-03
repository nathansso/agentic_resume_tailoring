import type { CSSProperties } from "react";
import { useAuth } from "../context/AuthContext";
import { colors, font } from "../theme";

export function MainPage() {
  const { user, logout } = useAuth();

  return (
    <div style={s.page}>
      <header style={s.header}>
        <span style={s.brand}>ART</span>
        <div style={s.headerRight}>
          <span style={s.userName}>{user?.name}</span>
          <button onClick={logout} style={s.signOut}>sign out</button>
        </div>
      </header>
      <div style={s.body}>
        <aside style={s.sidebar}>
          {/* Phase 2: session/job list */}
        </aside>
        <main style={s.main}>
          <p style={s.placeholder}>
            Dashboard coming in Phase 2 — chat, jobs, and data explorer.
          </p>
        </main>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: {
    display: "flex", flexDirection: "column",
    height: "100vh", overflow: "hidden",
    background: colors.background, color: colors.text,
  },
  header: {
    display: "flex", alignItems: "center",
    justifyContent: "space-between",
    height: "2.25rem",
    padding: "0 1rem",
    background: colors.boost,
    borderBottom: `1px solid ${colors.primary}`,
    flexShrink: 0,
  },
  brand: {
    fontWeight: 700, fontSize: font.size.base,
    color: colors.accent, letterSpacing: "0.1em",
  },
  headerRight: { display: "flex", alignItems: "center", gap: "1rem" },
  userName: { color: colors.textMuted, fontSize: font.size.sm },
  signOut: {
    padding: "0.125rem 0.5rem",
    borderRadius: 0,
    background: "transparent",
    color: colors.text,
    border: `1px solid ${colors.primary}`,
    cursor: "pointer",
    fontSize: font.size.sm,
    fontFamily: "inherit",
  },
  body: { display: "flex", flex: 1, overflow: "hidden" },
  sidebar: {
    width: "calc(32ch + 1.5rem)",
    borderRight: `1px solid ${colors.primary}`,
    background: colors.surface,
    padding: "1rem 0.75rem",
    overflowY: "auto",
    flexShrink: 0,
  },
  main: {
    flex: 1,
    background: colors.background,
    padding: "1.5rem",
    overflowY: "auto",
  },
  placeholder: { color: colors.textMuted, fontSize: font.size.base, maxWidth: "72ch" },
};
