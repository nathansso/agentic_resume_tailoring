import { useEffect, useState } from "react";

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

interface Props {
  label: string;
  /** Show a running elapsed-time counter (for tasks started by the user just now). */
  showElapsed?: boolean;
}

/** Indeterminate progress bar for long-running server calls (ingest, analyze, tailor). */
export function ProgressBar({ label, showElapsed = true }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!showElapsed) return;
    const id = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(id);
  }, [showElapsed]);

  return (
    <div className="flex w-full flex-col gap-1.5" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-accent">{label}</span>
        {showElapsed && (
          <span className="text-sm tabular-nums text-muted-foreground">
            {formatElapsed(elapsed)}
          </span>
        )}
      </div>
      <div className="relative h-1 overflow-hidden rounded-full bg-secondary">
        <div className="absolute inset-y-0 w-[40%] animate-sweep rounded-full bg-primary" />
      </div>
    </div>
  );
}
