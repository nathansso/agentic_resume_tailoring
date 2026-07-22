import { useState, useEffect, useRef, type KeyboardEvent } from "react";
import type { ChatMsg } from "../types";
import { cn } from "../lib/utils";
import { loadHistory, sendMessage } from "../api/chat";
import { chatHPadding, messageGap } from "../lib/paneResize";

const WELCOME = "Welcome to ARTie — your agentic resume tailoring assistant.\n\nI can help you:\n  • Ingest your resume, GitHub, or LinkedIn data\n  • View your skills, experiences, and projects\n  • Analyze job descriptions and find skill gaps\n  • Tailor your resume for specific roles\n\nTry: \"show my skills\" or type /ingest to get started.";

interface Props {
  jobId: string | null;
  onViewChange: (view: string) => void;
  /** Fires after each assistant reply — lets the Job workspace resync job state
      the chat may have changed (analyze, re-tailor, JD paste). */
  onAssistantReply?: () => void;
  /** Replaces the generic welcome when the chat history is empty (job-scoped
      chats pass state-aware guidance). */
  welcome?: string;
  /** Assistant "briefing" bubbles pinned above the history (job insights:
      skills match, tailoring changes, scores). Rendered, not stored, so they
      track live job state. */
  contextMessages?: string[];
}

function dayLabel(iso: string): string {
  return iso.slice(0, 10);
}

export function ChatPanel({ jobId, onViewChange, onAssistantReply, welcome, contextMessages }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [historyEmpty, setHistoryEmpty] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // Message spacing widens as the column narrows (#90).
  const [panelWidth, setPanelWidth] = useState(0);
  const effectiveJobId = jobId ?? "landing";

  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      setPanelWidth(entries[entries.length - 1].contentRect.width);
    });
    ro.observe(el);
    setPanelWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    setMessages([]);
    setHistoryEmpty(false);
    setError(null);
    loadHistory(effectiveJobId)
      .then(msgs => {
        setMessages(msgs);
        setHistoryEmpty(msgs.length === 0);
      })
      .catch(() => {
        setHistoryEmpty(true);
      });
  }, [effectiveJobId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;

    // Handle slash commands client-side
    if (text === "/ingest") { onViewChange("ingest"); setInput(""); return; }
    if (text === "/profile") { onViewChange("profile"); setInput(""); return; }
    if (text === "/data") { onViewChange("data"); setInput(""); return; }

    setInput("");
    const userMsg: ChatMsg = { role: "user", content: text, created_at: new Date().toISOString() };
    setMessages(prev => [...prev, userMsg]);
    setSending(true);
    setError(null);

    try {
      const reply = await sendMessage(effectiveJobId, text);
      const botMsg: ChatMsg = { role: "assistant", content: reply, created_at: new Date().toISOString() };
      setMessages(prev => [...prev, botMsg]);
      onAssistantReply?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chat failed");
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  // The welcome and insight briefings are rendered (not stored) so they track
  // job-state changes live.
  const rendered: Array<{ type: "msg"; msg: ChatMsg } | { type: "day"; label: string }> = [];
  if (historyEmpty) {
    rendered.push({
      type: "msg",
      msg: { role: "assistant", content: welcome ?? WELCOME, created_at: new Date().toISOString() },
    });
  }
  for (const content of contextMessages ?? []) {
    rendered.push({
      type: "msg",
      msg: { role: "assistant", content, created_at: new Date().toISOString() },
    });
  }
  let lastDay = "";
  for (const msg of messages) {
    const day = dayLabel(msg.created_at);
    if (day && day !== lastDay && day !== new Date().toISOString().slice(0, 10)) {
      rendered.push({ type: "day", label: day });
      lastDay = day;
    }
    rendered.push({ type: "msg", msg });
  }

  const bubble = (isUser: boolean) =>
    cn(
      "max-w-[72ch] rounded-lg px-3.5 py-2.5",
      isUser
        ? "self-end bg-secondary"
        : "self-start border-l-[3px] border-primary bg-card"
    );

  return (
    <div className="flex h-full flex-col overflow-hidden" ref={panelRef}>
      <div
        className="flex flex-1 flex-col overflow-y-auto p-4"
        style={{
          // Computed from the live pane width (#90) — not expressible as utilities.
          gap: `${messageGap(panelWidth)}px`,
          // Reclaim side padding before the bubble is forced to wrap tighter (#90).
          paddingLeft: `${chatHPadding(panelWidth)}px`,
          paddingRight: `${chatHPadding(panelWidth)}px`,
        }}
      >
        {rendered.map((item, i) => {
          if (item.type === "day") {
            return (
              <div key={`day-${i}`} className="py-1 text-center text-sm italic text-muted-foreground">
                {item.label}
              </div>
            );
          }
          const { msg } = item;
          const isUser = msg.role === "user";
          return (
            <div key={i} className={bubble(isUser)}>
              <pre className="m-0 whitespace-pre-wrap break-words font-sans leading-relaxed">
                {msg.content}
              </pre>
            </div>
          );
        })}
        {sending && (
          <div className={bubble(false)}>
            <pre className="m-0 whitespace-pre-wrap break-words font-sans leading-relaxed text-muted-foreground">
              …
            </pre>
          </div>
        )}
        {error && <p className="my-1 text-sm text-destructive">{error}</p>}
        <div ref={bottomRef} />
      </div>

      <div className="flex flex-shrink-0 gap-2 border-t border-border bg-card px-4 py-3">
        <textarea
          className="flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message ARTie… (Enter to send, Shift+Enter for newline)"
          rows={3}
          disabled={sending}
        />
        <button
          className="self-end rounded-md bg-primary px-5 py-2.5 font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={handleSend}
          disabled={sending || !input.trim()}
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
