import { useState, useEffect, type FormEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { login, getAuthCapabilities } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { AuthLayout, inputClass, buttonClass, linkClass } from "../components/AuthLayout";

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
    <AuthLayout
      title="Welcome back"
      subtitle="Sign in to keep tailoring."
      footer={
        <>
          No account? <Link to="/register" className={linkClass}>Create one</Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <input
          className={inputClass}
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
        />
        <input
          className={inputClass}
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button className={`${buttonClass} mt-1`} type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {resetEnabled && (
        <p className="mt-4 text-center text-sm">
          <Link to="/forgot-password" className={linkClass}>Forgot password?</Link>
        </p>
      )}
    </AuthLayout>
  );
}
