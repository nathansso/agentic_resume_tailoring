import { useState, useEffect, type FormEvent, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { resetPassword } from "../api/auth";
import { colors, font } from "../theme";

const MIN_PASSWORD_LENGTH = 8;

type Tokens = { accessToken: string; refreshToken: string };

/** Parse the Supabase recovery tokens out of the URL fragment (#access_token=…). */
function parseRecoveryHash(): { tokens: Tokens | null; error: string | null } {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return { tokens: null, error: null };
  const params = new URLSearchParams(hash);
  const errorDescription = params.get("error_description");
  if (errorDescription) return { tokens: null, error: errorDescription.replace(/\+/g, " ") };
  const accessToken = params.get("access_token");
  const refreshToken = params.get("refresh_token");
  const type = params.get("type");
  if (accessToken && refreshToken && type === "recovery") {
    return { tokens: { accessToken, refreshToken }, error: null };
  }
  return { tokens: null, error: null };
}

export function ResetPasswordPage() {
  const [tokens, setTokens] = useState<Tokens | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const { tokens: parsed, error: parseError } = parseRecoveryHash();
    setTokens(parsed);
    setLinkError(parseError);
    // Strip the tokens from the address bar so they aren't left in history.
    if (parsed || parseError) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    if (!tokens) return;
    setSubmitting(true);
    try {
      await resetPassword(tokens.accessToken, tokens.refreshToken, password);
      setDone(true);
      setTimeout(() => navigate("/login"), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset password");
    } finally {
      setSubmitting(false);
    }
  }

  const invalidLink = !tokens;

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>Set a new password</h1>
        {done ? (
          <p style={s.success}>Your password has been updated. Redirecting to sign in…</p>
        ) : invalidLink ? (
          <>
            <p style={s.error}>
              {linkError ?? "This reset link is invalid or has expired. Request a new one."}
            </p>
            <p style={s.hint}>
              <Link to="/forgot-password" style={s.link}>Request a new link</Link>
            </p>
          </>
        ) : (
          <>
            <p style={s.subtitle}>Choose a password with at least {MIN_PASSWORD_LENGTH} characters.</p>
            <form onSubmit={handleSubmit} style={s.form}>
              <input
                style={s.input}
                type="password"
                placeholder="New password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                required
              />
              <input
                style={s.input}
                type="password"
                placeholder="Confirm new password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                required
              />
              {error && <p style={s.error}>{error}</p>}
              <button style={s.button} type="submit" disabled={submitting}>
                {submitting ? "Updating…" : "Update password"}
              </button>
            </form>
          </>
        )}
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
  error: { margin: "0.75rem 0 0", color: colors.error, fontSize: font.size.sm },
  success: { margin: "0.75rem 0 0", color: colors.text, fontSize: font.size.sm },
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
