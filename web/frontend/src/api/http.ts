export function detailMessage(detail: unknown): string | null {
  // FastAPI errors carry `detail` as a string, but 422 validation errors carry
  // an array of {loc, msg, type} objects — rendering those raw shows "[object Object]".
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (typeof d === "object" && d !== null && "msg" in d ? String((d as { msg: unknown }).msg) : null))
      .filter((m): m is string => m !== null);
    if (msgs.length > 0) return msgs.join("; ");
  }
  return null;
}

export async function errorMessage(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return detailMessage((body as { detail?: unknown }).detail) ?? fallback;
}

export async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Request failed (${res.status})`));
  }
  return res.json() as Promise<T>;
}
