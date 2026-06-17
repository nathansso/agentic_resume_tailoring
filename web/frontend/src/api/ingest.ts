async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function ingestResume(file: File): Promise<{ result: string }> {
  const form = new FormData();
  form.append("file", file);
  return json(await fetch("/api/ingest/resume", {
    method: "POST",
    body: form,
    credentials: "include",
  }));
}

export async function ingestGithub(username: string): Promise<{ result: string }> {
  return json(await fetch("/api/ingest/github", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
    credentials: "include",
  }));
}

export async function ingestGithubRepo(repoRef: string): Promise<{ result: string }> {
  return json(await fetch("/api/ingest/github/repo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_ref: repoRef }),
    credentials: "include",
  }));
}

export async function ingestLinkedin(url: string): Promise<{ result: string }> {
  return json(await fetch("/api/ingest/linkedin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
    credentials: "include",
  }));
}

export async function ingestLinkedinPdf(file: File): Promise<{ result: string }> {
  const form = new FormData();
  form.append("file", file);
  return json(await fetch("/api/ingest/linkedin/pdf", {
    method: "POST",
    body: form,
    credentials: "include",
  }));
}
