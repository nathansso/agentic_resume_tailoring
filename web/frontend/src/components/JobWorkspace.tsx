import { useEffect, useRef, useState } from "react";
import type { JobDetail, TailorResult } from "../types";
import { cn } from "../lib/utils";
import { saveDescription, analyzeJob, tailorJob, getJob, exportUrl } from "../api/jobs";
import { ChatPanel } from "./ChatPanel";
import { ProgressBar } from "./ProgressBar";
import { ResumeSplit, type ResumeView } from "./ResumeSplit";
import { ResizeDivider } from "./ResizeDivider";
import {
  chatWidthFromPointer,
  minResumeWidth,
  readStoredNumber,
  MIN_CHAT_WIDTH,
  MIN_PREVIEW_ONLY_WIDTH,
} from "../lib/paneResize";
import { jobInsightMessages } from "../lib/insightMessages";
import { jobWelcome } from "../lib/welcome";

const CHAT_WIDTH_KEY = "art:jobs:chatWidth";

interface Props {
  job: JobDetail;
  /** Created this session with a JD — kick off analyze + tailor automatically. */
  autoStart: boolean;
  onJobUpdate: (job: JobDetail) => void;
  onViewChange: (view: string) => void;
}

type Phase = "idle" | "analyzing" | "tailoring";

// Module-level so React StrictMode's double effect-fire (and remounts while a
// chain is still running) can't launch the pipeline twice for the same job.
const startedChains = new Set<string>();

function statusColor(status: string): string {
  if (status === "exported" || status === "tailored") return "text-success";
  if (status === "analyzed") return "text-warning";
  return "text-muted-foreground";
}

function scoreColor(score: number): string {
  if (score >= 70) return "text-success";
  if (score >= 50) return "text-warning";
  return "text-destructive";
}

const actionBtn =
  "self-start rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50";

export function JobWorkspace({ job, autoStart, onJobUpdate, onViewChange }: Props) {
  const [descInput, setDescInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  // Pane layout starts on Preview (compiled resume front and center); the chat
  // column absorbs the hidden source pane's width until Split is chosen.
  const [paneView, setPaneView] = useState<ResumeView>("preview");
  // Manual chat-column width (px). `null` keeps the automatic sizing below;
  // once the user drags the divider it becomes a persisted fixed width (#90).
  const columnsRef = useRef<HTMLDivElement>(null);
  const [chatWidth, setChatWidth] = useState<number | null>(() =>
    readStoredNumber(localStorage.getItem(CHAT_WIDTH_KEY)),
  );
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    setPaneView("preview");
  }, [job.job_id]);

  function handleChatDrag(clientX: number) {
    const el = columnsRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setDragging(true);
    const minResume = minResumeWidth(paneView);
    setChatWidth(chatWidthFromPointer(clientX, rect.left, rect.width, MIN_CHAT_WIDTH, minResume));
  }

  // Clicking "Split" condenses the chat to its floor so the freed width flows
  // into the resume area and the source pane can slide out to fill it (the
  // editor auto-expands in ResumeSplit). Persisted like a manual drag, so it
  // sticks; the chat divider's double-click still resets to automatic sizing.
  function handlePaneView(next: ResumeView) {
    if (next === "split" && paneView !== "split") {
      setChatWidth(MIN_CHAT_WIDTH);
      localStorage.setItem(CHAT_WIDTH_KEY, String(MIN_CHAT_WIDTH));
    }
    setPaneView(next);
  }

  // Keep a manually-set chat width valid as the window resizes or the pane
  // layout changes — e.g. switching to Split needs more resume room, so an
  // over-wide chat is pulled back in rather than squashing the preview (#90).
  useEffect(() => {
    const el = columnsRef.current;
    if (!el) return;
    const reclamp = () =>
      setChatWidth(w => {
        if (w == null) return w;
        const rect = el.getBoundingClientRect();
        const minResume = minResumeWidth(paneView);
        return chatWidthFromPointer(rect.left + w, rect.left, rect.width, MIN_CHAT_WIDTH, minResume);
      });
    reclamp();
    const ro = new ResizeObserver(reclamp);
    ro.observe(el);
    return () => ro.disconnect();
  }, [paneView]);

  function persistChatWidth() {
    setDragging(false);
    setChatWidth(w => {
      if (w != null) localStorage.setItem(CHAT_WIDTH_KEY, String(Math.round(w)));
      return w;
    });
  }

  function resetChatWidth() {
    localStorage.removeItem(CHAT_WIDTH_KEY);
    setChatWidth(null);
  }

  const tailored = job.status === "tailored" || job.status === "exported";
  const budgetUsed = job.retailor_count >= job.retailor_limit;
  // Single visible resume pane keeps its exact Split-mode size — (100% − 400px)/2
  // — pinned to the far right; the chat slides to claim the remainder.
  const chatWide = tailored && paneView !== "split";

  async function runChain(startAt: "analyze" | "tailor") {
    setError(null);
    try {
      let detail = job;
      if (startAt === "analyze") {
        setPhase("analyzing");
        detail = await analyzeJob(job.job_id);
        onJobUpdate(detail);
      }
      setPhase("tailoring");
      const result: TailorResult = await tailorJob(job.job_id);
      onJobUpdate({ ...detail, ...result } as JobDetail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      setPhase("idle");
    }
  }

  // Auto-run analyze + tailor for jobs created with a pasted JD (issue #70).
  useEffect(() => {
    if (!autoStart || startedChains.has(job.job_id)) return;
    if (job.status === "created" && job.description) {
      startedChains.add(job.job_id);
      runChain("analyze");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, autoStart]);

  async function handleSaveDescAndRun() {
    if (!descInput.trim()) return;
    setError(null);
    setPhase("analyzing");
    try {
      const updated = await saveDescription(job.job_id, descInput);
      onJobUpdate(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save description");
      setPhase("idle");
      return;
    }
    startedChains.add(job.job_id);
    await runChain("analyze");
  }

  function refreshJob() {
    // Chat can change job state (re-tailor, analyze, JD paste) — resync.
    getJob(job.job_id).then(onJobUpdate).catch(() => {});
  }

  const phaseLabel =
    phase === "analyzing"
      ? "Analyzing job description… (~60s)"
      : "Tailoring resume… this may take 1–2 minutes";

  const exportLink =
    "rounded-md border border-border px-2 py-0.5 text-sm font-semibold text-accent no-underline transition-colors hover:bg-secondary";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex flex-shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4 py-2.5">
        <div className="flex min-w-0 items-baseline gap-2.5">
          <h2 className="truncate font-bold tracking-tight">{job.title}</h2>
          <span className="whitespace-nowrap text-sm text-muted-foreground">{job.company}</span>
        </div>
        <div className="flex flex-shrink-0 items-baseline gap-3">
          <span className={cn("text-sm font-semibold", statusColor(job.status))}>
            [{job.status}]
          </span>
          {job.ats_score !== null && (
            <span className={cn("font-bold", scoreColor(job.ats_score))}>
              ATS: {Math.round(job.ats_score)}%
            </span>
          )}
          <span className={cn("text-sm", budgetUsed ? "text-destructive" : "text-muted-foreground")}>
            Tailor runs: {job.retailor_count}/{job.retailor_limit}
          </span>
          {tailored && (
            <span className="flex items-baseline gap-2">
              <span className="text-sm text-muted-foreground">Export:</span>
              <a
                href={exportUrl(job.job_id, "pdf")}
                className={exportLink}
                download
                title={job.has_manual_edits ? "Includes your manual .tex edits" : undefined}
              >
                PDF
              </a>
              <a
                href={exportUrl(job.job_id, "tex")}
                className={exportLink}
                download
                title={job.has_manual_edits ? "Includes your manual .tex edits" : undefined}
              >
                LaTeX
              </a>
              <a
                href={exportUrl(job.job_id, "docx")}
                className={exportLink}
                download
                title={job.has_manual_edits ? "DOCX is generated from the AI-tailored content and ignores manual .tex edits" : undefined}
              >
                DOCX
              </a>
            </span>
          )}
        </div>
      </div>

      {/* Three panes: chat (with insight briefings) | .tex editor | preview */}
      <div className="flex min-h-0 flex-1" ref={columnsRef}>
        <div
          className={cn(
            "flex min-h-0 flex-none flex-col",
            !dragging && "transition-[width] duration-[250ms] ease-out"
          )}
          style={{
            minWidth: MIN_CHAT_WIDTH,
            // Undragged width. When a single resume pane is showing (chatWide),
            // the chat claims the extra room — but never so much that the preview
            // drops below its legibility floor, so cap it at columns − floor. Once
            // the user drags, chatWidth takes over (already floor-clamped).
            width:
              chatWidth != null
                ? `${chatWidth}px`
                : chatWide
                  ? `min(calc(50% + 200px), calc(100% - ${MIN_PREVIEW_ONLY_WIDTH}px))`
                  : "400px",
          }}
        >
          <div className="flex min-h-0 flex-1 flex-col">
            <ChatPanel
              jobId={job.job_id}
              welcome={jobWelcome(job)}
              contextMessages={jobInsightMessages(job)}
              onViewChange={onViewChange}
              onAssistantReply={refreshJob}
            />
          </div>
        </div>

        <ResizeDivider
          ariaLabel="Resize chat and resume panes"
          onDrag={handleChatDrag}
          onDragEnd={persistChatWidth}
          onReset={resetChatWidth}
        />

        <div className="flex min-w-0 flex-1 flex-col gap-2 overflow-hidden p-2">
          {error && (
            <div className="flex flex-shrink-0 flex-col gap-2">
              <p className="text-sm text-destructive">{error}</p>
              <button
                className={actionBtn}
                onClick={() => {
                  if (job.has_manual_edits &&
                      !window.confirm("Re-tailoring will discard your manual .tex edits. Continue?")) return;
                  runChain(job.status === "created" ? "analyze" : "tailor");
                }}
              >
                Retry
              </button>
            </div>
          )}

          {phase !== "idle" && <ProgressBar label={phaseLabel} />}

          {tailored && (
            // Remount after each re-tailor so the editor reseeds from the fresh output
            <ResumeSplit
              key={`${job.job_id}:${job.retailor_count}`}
              jobId={job.job_id}
              view={paneView}
              onViewChange={handlePaneView}
              onEditsChanged={refreshJob}
            />
          )}

          {/* No JD yet: paste panel */}
          {phase === "idle" && !job.description && (
            <>
              <p className="text-sm text-muted-foreground">
                Paste the full job description to analyze and tailor automatically.
              </p>
              <textarea
                className="resize-y rounded-md border border-input bg-background px-3 py-2 leading-relaxed outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary"
                value={descInput}
                onChange={e => setDescInput(e.target.value)}
                rows={12}
                placeholder="Paste job description here…"
                autoFocus
              />
              <button
                className="self-start rounded-md bg-primary px-4 py-2 font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                onClick={handleSaveDescAndRun}
                disabled={!descInput.trim()}
              >
                Save & Run
              </button>
            </>
          )}

          {/* JD present but nothing tailored yet and no pipeline running */}
          {phase === "idle" && !tailored && job.description && !error && (
            <p className="text-sm text-muted-foreground">
              No tailored resume yet — ask the chat to “tailor”.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
