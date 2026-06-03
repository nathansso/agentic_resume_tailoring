import { useState, type FormEvent, type CSSProperties, type ChangeEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { register } from "../api/auth";
import { useAuth } from "../context/AuthContext";

interface FormState {
  name: string;
  email: string;
  username: string;
  password: string;
  confirm: string;
}

const INITIAL: FormState = { name: "", email: "", username: "", password: "", confirm: "" };

export function RegisterPage() {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  function onChange(field: keyof FormState) {
    return (e: ChangeEvent<HTMLInputElement>) =>
      setForm((prev) => ({ ...prev, [field]: e.target.value }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (form.password !== form.confirm) {
      setError("Passwords do not match");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const user = await register(form.name, form.email, form.username, form.password);
      setUser(user);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>Create Account</h1>
        <form onSubmit={handleSubmit} style={s.form}>
          <input style={s.input} placeholder="Full name" value={form.name} onChange={onChange("name")} required />
          <input style={s.input} type="email" placeholder="Email" value={form.email} onChange={onChange("email")} autoComplete="email" required />
          <input style={s.input} placeholder="Username" value={form.username} onChange={onChange("username")} autoComplete="username" required />
          <input style={s.input} type="password" placeholder="Password" value={form.password} onChange={onChange("password")} autoComplete="new-password" required />
          <input style={s.input} type="password" placeholder="Confirm password" value={form.confirm} onChange={onChange("confirm")} autoComplete="new-password" required />
          {error && <p style={s.error}>{error}</p>}
          <button style={s.button} type="submit" disabled={submitting}>
            {submitting ? "Creating account…" : "Create Account"}
          </button>
        </form>
        <p style={s.hint}>
          Already have an account? <Link to="/login" style={s.link}>Sign in</Link>
        </p>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  page: { display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "#0f172a" },
  card: { background: "#1e293b", padding: "2.5rem", borderRadius: "12px", width: "100%", maxWidth: "360px", color: "#f1f5f9" },
  title: { margin: "0 0 1.5rem", fontSize: "1.5rem", fontWeight: 700 },
  form: { display: "flex", flexDirection: "column", gap: "0.75rem" },
  input: { padding: "0.625rem 0.875rem", borderRadius: "6px", border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9", fontSize: "0.875rem", outline: "none" },
  error: { margin: 0, color: "#f87171", fontSize: "0.8rem" },
  button: { padding: "0.625rem", borderRadius: "6px", background: "#6366f1", color: "#fff", fontWeight: 600, border: "none", cursor: "pointer", fontSize: "0.875rem" },
  hint: { marginTop: "1.25rem", textAlign: "center", color: "#94a3b8", fontSize: "0.8rem" },
  link: { color: "#818cf8" },
};
