import { useState, type FormEvent, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { forgotPassword } from "../api/auth";
import { colors, font } from "../theme";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const msg = await forgotPassword(email);
      setMessage(msg);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send reset email");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>Reset password</h1>
        <p style={s.subtitle}>Enter your email and we'll send you a reset link.</p>
        {message ? (
          <p style={s.success}>{message}</p>
        ) : (
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
            {error && <p style={s.error}>{error}</p>}
            <button style={s.button} type="submit" disabled={submitting}>
              {submitting ? "Sending…" : "Send reset link"}
            </button>
          </form>
        )}
        <p style={s.hint}>
          <Link to="/login" style={s.link}>Back to sign in</Link>
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
    margin: 0, fontSize: font.size.xl, fontWeight: 700,
    letterSpacing: "0.03em", color: colors.accent,
  },
  subtitle: {
    margin: "0.5rem 0 1.5rem",
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
  success: { margin: "0 0 0.5rem", color: colors.text, fontSize: font.size.sm },
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
