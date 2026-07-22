import { useState, type FormEvent, type ChangeEvent } from "react";
import { useNavigate, Link } from "react-router-dom";
import { register } from "../api/auth";
import { useAuth } from "../context/AuthContext";
import { AuthLayout, inputClass, buttonClass, linkClass } from "../components/AuthLayout";

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
  const [confirmEmail, setConfirmEmail] = useState<string | null>(null);
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
      const { user, emailConfirmationRequired } = await register(
        fullName, form.email, form.username, form.password
      );
      if (emailConfirmationRequired) {
        // No session was issued — the user must confirm via the emailed link
        // before any authenticated request will succeed. Do NOT log them in
        // (that would drop them into a cookieless app that 401s everywhere).
        setConfirmEmail(user.email);
        return;
      }
      setUser(user);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (confirmEmail) {
    return (
      <AuthLayout
        title="Confirm your email"
        footer={
          <>
            Already confirmed? <Link to="/login" className={linkClass}>Sign in</Link>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-muted-foreground">
          We sent a confirmation link to{" "}
          <strong className="text-foreground">{confirmEmail}</strong>. Click it to
          activate your account, then sign in to finish setting up your profile.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Start with whatever you have — you can add the rest later."
      footer={
        <>
          Already have an account? <Link to="/login" className={linkClass}>Sign in</Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex gap-3">
          <input className={inputClass} placeholder="First name" value={form.firstName} onChange={onChange("firstName")} autoComplete="given-name" required />
          <input className={inputClass} placeholder="Last name" value={form.lastName} onChange={onChange("lastName")} autoComplete="family-name" required />
        </div>
        <input className={inputClass} type="email" placeholder="Email" value={form.email} onChange={onChange("email")} autoComplete="email" required />
        <input className={inputClass} placeholder="Username" value={form.username} onChange={onChange("username")} autoComplete="username" required />
        <input className={inputClass} type="password" placeholder="Password" value={form.password} onChange={onChange("password")} autoComplete="new-password" required />
        {form.password.length > 0 && getPasswordErrors(form.password).map(msg => (
          <p key={msg} className="-mt-1 text-xs text-destructive">{msg}</p>
        ))}
        <input className={inputClass} type="password" placeholder="Confirm password" value={form.confirm} onChange={onChange("confirm")} autoComplete="new-password" required />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button className={`${buttonClass} mt-1`} type="submit" disabled={submitting}>
          {submitting ? "Creating account…" : "Create account"}
        </button>
      </form>
    </AuthLayout>
  );
}
