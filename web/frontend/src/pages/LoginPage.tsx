import { useState, useEffect, type FormEvent, type CSSProperties } from "react";
import { useNavigate, Link } from "react-router-dom";
import { login, getAuthCapabilities } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { colors, font } from "../theme";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [resetEnabled, setResetEnabled] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    getAuthCapabilities()
      .then((c) => setResetEnabled(c.password_reset_enabled))
      .catch(() => setResetEnabled(false));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const user = await login(email, password);
      setUser(user);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>ART</h1>
        <p style={s.subtitle}>Agentic Resume Tailoring</p>
        <form onSubmit={handleSubmit} style={s.form}>
          <input
            style={s.input}
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
          <input
            style={s.input}
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          {error && <p style={s.error}>{error}</p>}
          <button style={s.button} type="submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign In"}
          </button>
        </form>
        {resetEnabled && (
          <p style={s.hint}>
            <Link to="/forgot-password" style={s.link}>Forgot password?</Link>
          </p>
        )}
        <p style={s.hint}>
          No account? <Link to="/register" style={s.link}>Create one</Link>
        </p>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: {
    display: "flex", alignItems: "center", justifyContent: "center",
    minHeight: "100vh", background: colors.background,
  },
  card: {
    background: colors.surface,
    padding: "2.5rem",
    borderRadius: 0,
    border: `1px solid ${colors.primary}`,
    width: "100%", maxWidth: "360px",
    color: colors.text,
  },
  title: {
    margin: 0, fontSize: font.size.xxl, fontWeight: 700,
    letterSpacing: "0.05em", color: colors.accent,
  },
  subtitle: {
    margin: "0.25rem 0 1.75rem",
    color: colors.textMuted, fontSize: font.size.sm,
  },
  form: { display: "flex", flexDirection: "column", gap: "0.625rem" },
  input: {
    padding: "0.5rem 0.75rem",
    borderRadius: 0,
    border: `1px solid ${colors.primary}`,
    background: colors.background,
    color: colors.text,
    fontSize: font.size.base,
    outline: "none",
    fontFamily: "inherit",
  },
  error: { margin: 0, color: colors.error, fontSize: font.size.sm },
  button: {
    padding: "0.5rem",
    borderRadius: 0,
    background: colors.accent,
    color: colors.background,
    fontWeight: 700,
    border: "none",
    cursor: "pointer",
    fontSize: font.size.base,
    fontFamily: "inherit",
    letterSpacing: "0.03em",
  },
  hint: {
    marginTop: "1.25rem", textAlign: "center",
    color: colors.textMuted, fontSize: font.size.sm,
  },
  link: { color: colors.accent },
};
