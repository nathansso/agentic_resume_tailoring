import type { ChatMsg } from "../types";
import { json, errorMessage } from "./http";

export async function loadHistory(jobId: string): Promise<ChatMsg[]> {
  return json(await fetch(`/api/chat/${jobId}/history`, { credentials: "include" }));
}

export async function sendMessage(jobId: string, message: string): Promise<string> {
  const res = await fetch(`/api/chat/${jobId}/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Chat failed (${res.status})`));
  }
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let content = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    for (const line of chunk.split("\n")) {
      if (line.startsWith("data: ")) {
        try {
          const payload = JSON.parse(line.slice(6)) as { content: string; done: boolean };
          content = payload.content;
        } catch {}
      }
    }
  }
  return content;
}
