import type { User } from "../types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function login(username: string, password: string): Promise<User> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
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

export async function getGithubStatus(): Promise<{ connected: boolean; oauth_configured: boolean }> {
  return json(await fetch("/api/auth/github/status", { credentials: "include" }));
}

export async function disconnectGithub(): Promise<void> {
  await json(await fetch("/api/auth/github", { method: "DELETE", credentials: "include" }));
}
