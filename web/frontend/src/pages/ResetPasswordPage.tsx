import { useState, useEffect, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { resetPassword } from "../api/auth";
import { AuthLayout, inputClass, buttonClass, linkClass } from "../components/AuthLayout";

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

  if (done) {
    return (
      <AuthLayout title="Set a new password">
        <p className="text-sm leading-relaxed text-muted-foreground">
          Your password has been updated. Redirecting to sign in…
        </p>
      </AuthLayout>
    );
  }

  if (invalidLink) {
    return (
      <AuthLayout
        title="Set a new password"
        footer={<Link to="/forgot-password" className={linkClass}>Request a new link</Link>}
      >
        <p className="text-sm leading-relaxed text-destructive">
          {linkError ?? "This reset link is invalid or has expired. Request a new one."}
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Set a new password"
      subtitle={`Choose a password with at least ${MIN_PASSWORD_LENGTH} characters.`}
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <input
          className={inputClass}
          type="password"
          placeholder="New password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
        <input
          className={inputClass}
          type="password"
          placeholder="Confirm new password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          required
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button className={`${buttonClass} mt-1`} type="submit" disabled={submitting}>
          {submitting ? "Updating…" : "Update password"}
        </button>
      </form>
    </AuthLayout>
  );
}
