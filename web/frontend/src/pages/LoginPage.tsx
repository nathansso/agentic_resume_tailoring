import { useState, type FormEvent, type CSSProperties } from "react";
import { useNavigate, Link } from "react-router-dom";
import { login } from "../api/auth";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const user = await login(username, password);
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
            placeholder="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
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
        <p style={s.hint}>
          No account? <Link to="/register" style={s.link}>Create one</Link>
        </p>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: { display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "#0f172a" },
  card: { background: "#1e293b", padding: "2.5rem", borderRadius: "12px", width: "100%", maxWidth: "360px", color: "#f1f5f9" },
  title: { margin: 0, fontSize: "2rem", fontWeight: 700, letterSpacing: "-0.5px" },
  subtitle: { margin: "0.25rem 0 1.75rem", color: "#94a3b8", fontSize: "0.875rem" },
  form: { display: "flex", flexDirection: "column", gap: "0.75rem" },
  input: { padding: "0.625rem 0.875rem", borderRadius: "6px", border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9", fontSize: "0.875rem", outline: "none" },
  error: { margin: 0, color: "#f87171", fontSize: "0.8rem" },
  button: { padding: "0.625rem", borderRadius: "6px", background: "#6366f1", color: "#fff", fontWeight: 600, border: "none", cursor: "pointer", fontSize: "0.875rem" },
  hint: { marginTop: "1.25rem", textAlign: "center", color: "#94a3b8", fontSize: "0.8rem" },
  link: { color: "#818cf8" },
};
