import type { User } from "../types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    credentials: "include",
  });
  const data = await json<{ user: User }>(res);
  return data.user;
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}

export async function register(
  name: string,
  email: string,
  username: string,
  password: string
): Promise<User> {
  const res = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, username, password }),
    credentials: "include",
  });
  const data = await json<{ user: User }>(res);
  return data.user;
}

export async function getMe(): Promise<User | null> {
  const res = await fetch("/api/auth/me", { credentials: "include" });
  if (!res.ok) return null;
  return res.json() as Promise<User>;
}

export async function getAuthCapabilities(): Promise<{ password_reset_enabled: boolean; auth_mode: string }> {
  const res = await fetch("/api/auth/capabilities", { credentials: "include" });
  if (!res.ok) return { password_reset_enabled: false, auth_mode: "local" };
  return res.json();
}

export async function forgotPassword(email: string): Promise<string> {
  const res = await fetch("/api/auth/forgot-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
    credentials: "include",
  });
  const data = await json<{ message?: string }>(res);
  return data.message ?? "If an account exists for that email, a reset link has been sent.";
}

export async function resetPassword(
  accessToken: string,
  refreshToken: string,
  newPassword: string
): Promise<void> {
  const res = await fetch("/api/auth/reset-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      access_token: accessToken,
      refresh_token: refreshToken,
      new_password: newPassword,
    }),
    credentials: "include",
  });
  await json<{ ok: boolean }>(res);
}

export async function getGithubStatus(): Promise<{ connected: boolean; oauth_configured: boolean; github_username: string | null }> {
  return json(await fetch("/api/auth/github/status", { credentials: "include" }));
}

export async function disconnectGithub(): Promise<void> {
  await json(await fetch("/api/auth/github", { method: "DELETE", credentials: "include" }));
}
