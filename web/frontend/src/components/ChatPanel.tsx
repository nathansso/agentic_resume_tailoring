import { useState, useEffect, useRef, type CSSProperties, type KeyboardEvent } from "react";
import type { ChatMsg } from "../types";
import { colors, font } from "../theme";
import { loadHistory, sendMessage } from "../api/chat";
import { messageGap } from "../lib/paneResize";

const WELCOME = "Welcome to ART — your agentic resume tailoring assistant.\n\nI can help you:\n  • Ingest your resume, GitHub, or LinkedIn data\n  • View your skills, experiences, and projects\n  • Analyze job descriptions and find skill gaps\n  • Tailor your resume for specific roles\n\nTry: \"show my skills\" or type /ingest to get started.";

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

  return (
    <div style={s.panel} ref={panelRef}>
      <div style={{ ...s.scroll, gap: `${messageGap(panelWidth)}px` }}>
        {rendered.map((item, i) => {
          if (item.type === "day") {
            return <div key={`day-${i}`} style={s.daySep}>{item.label}</div>;
          }
          const { msg } = item;
          const isUser = msg.role === "user";
          return (
            <div key={i} style={isUser ? s.userMsg : s.botMsg}>
              <pre style={s.msgText}>{msg.content}</pre>
            </div>
          );
        })}
        {sending && (
          <div style={s.botMsg}>
            <pre style={{ ...s.msgText, color: colors.textMuted }}>…</pre>
          </div>
        )}
        {error && <p style={s.errorMsg}>{error}</p>}
        <div ref={bottomRef} />
      </div>

      <div style={s.inputRow}>
        <textarea
          style={s.textarea}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Message ART… (Enter to send, Shift+Enter for newline)"
          rows={3}
          disabled={sending}
        />
        <button style={s.sendBtn} onClick={handleSend} disabled={sending || !input.trim()}>
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  panel: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    overflow: "hidden",
  },
  scroll: {
    flex: 1,
    overflowY: "auto",
    padding: "1rem",
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
  },
  daySep: {
    textAlign: "center",
    color: colors.textMuted,
    fontSize: font.size.sm,
    fontStyle: "italic",
    padding: "0.25rem 0",
  },
  userMsg: {
    alignSelf: "flex-end",
    maxWidth: "72ch",
    background: "rgba(48,54,61,0.5)",
    padding: "0.5rem 0.75rem",
    borderRight: `2px solid ${colors.primary}`,
  },
  botMsg: {
    alignSelf: "flex-start",
    maxWidth: "72ch",
    background: colors.surface,
    padding: "0.5rem 0.75rem",
    borderLeft: `3px solid ${colors.accent}`,
  },
  msgText: {
    margin: 0,
    fontFamily: "inherit",
    fontSize: font.size.base,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    color: colors.text,
    lineHeight: 1.6,
  },
  errorMsg: {
    color: colors.error,
    fontSize: font.size.sm,
    margin: "0.25rem 0",
  },
  inputRow: {
    display: "flex",
    gap: "0.5rem",
    padding: "0.75rem 1rem",
    borderTop: `1px solid ${colors.primary}`,
    background: colors.surface,
    flexShrink: 0,
  },
  textarea: {
    flex: 1,
    background: colors.background,
    border: `1px solid ${colors.primary}`,
    color: colors.text,
    fontSize: font.size.base,
    fontFamily: "inherit",
    padding: "0.5rem 0.75rem",
    resize: "none",
    outline: "none",
    borderRadius: 0,
    lineHeight: 1.5,
  },
  sendBtn: {
    background: colors.accent,
    color: colors.background,
    border: "none",
    fontWeight: 700,
    fontSize: font.size.base,
    fontFamily: "inherit",
    padding: "0.5rem 1rem",
    cursor: "pointer",
    borderRadius: 0,
    alignSelf: "flex-end",
  },
};
