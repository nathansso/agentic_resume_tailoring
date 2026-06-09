import { useState, type FormEvent, type CSSProperties, type ChangeEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { register } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { colors, font } from "../theme";

interface FormState {
  firstName: string;
  lastName: string;
  email: string;
  username: string;
  password: string;
  confirm: string;
}

const INITIAL: FormState = { firstName: "", lastName: "", email: "", username: "", password: "", confirm: "" };

const PW_RULES: { test: (pw: string) => boolean; msg: string }[] = [
  { test: pw => pw.length >= 6,          msg: "Password must be at least 6 characters" },
  { test: pw => /[A-Z]/.test(pw),        msg: "Password must contain an uppercase letter" },
  { test: pw => /[a-z]/.test(pw),        msg: "Password must contain a lowercase letter" },
  { test: pw => /[0-9]/.test(pw),        msg: "Password must contain a number" },
  { test: pw => /[^A-Za-z0-9]/.test(pw), msg: "Password must contain a special character" },
];

function getPasswordErrors(pw: string): string[] {
  return PW_RULES.filter(r => !r.test(pw)).map(r => r.msg);
}

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
    const pwErrors = getPasswordErrors(form.password);
    if (pwErrors.length > 0) { setError(pwErrors[0]); return; }
    if (form.password !== form.confirm) {
      setError("Passwords do not match");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const fullName = `${form.firstName.trim()} ${form.lastName.trim()}`.trim();
      const user = await register(fullName, form.email, form.username, form.password);
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
          <div style={s.nameRow}>
            <input style={{ ...s.input, ...s.nameInput }} placeholder="First name" value={form.firstName} onChange={onChange("firstName")} autoComplete="given-name" required />
            <input style={{ ...s.input, ...s.nameInput }} placeholder="Last name" value={form.lastName} onChange={onChange("lastName")} autoComplete="family-name" required />
          </div>
          <input style={s.input} type="email" placeholder="Email" value={form.email} onChange={onChange("email")} autoComplete="email" required />
          <input style={s.input} placeholder="Username" value={form.username} onChange={onChange("username")} autoComplete="username" required />
          <input style={s.input} type="password" placeholder="Password" value={form.password} onChange={onChange("password")} autoComplete="new-password" required />
          {form.password.length > 0 && getPasswordErrors(form.password).map(msg => (
            <p key={msg} style={s.pwHint}>{msg}</p>
          ))}
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
  page: {
    display: "flex", alignItems: "center", justifyContent: "center",
    minHeight: "100vh", background: colors.background,
  },
  card: {
    background: colors.surface,
    padding: "2.5rem",
    borderRadius: 0,
    border: `1px solid ${colors.primary}`,
    width: "100%", maxWidth: "400px",
    color: colors.text,
  },
  title: {
    margin: "0 0 1.5rem", fontSize: font.size.xl,
    fontWeight: 700, color: colors.accent,
  },
  form: { display: "flex", flexDirection: "column", gap: "0.625rem" },
  nameRow: { display: "flex", gap: "0.625rem" },
  nameInput: { flex: 1, minWidth: 0 },
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
  pwHint: { margin: "-0.25rem 0 0", color: colors.error, fontSize: "0.75rem" },
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
