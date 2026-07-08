/** Debounced auto-compile state machine for the live .tex preview.
 *
 *  Kept free of React (and pdf.js) so it can be unit-tested with fake timers.
 *  Design constraints: the server caps concurrent compiles at 2 on a 512MB VM,
 *  so this never runs more than one compile at a time and coalesces bursts —
 *  while a compile is in flight, at most one follow-up buffer is queued and
 *  the latest input wins. */

export interface CompileSchedulerOpts {
  compile: (tex: string, signal: AbortSignal) => Promise<Uint8Array>;
  /** A compile finished and is current: `tex` is the exact buffer it rendered. */
  onResult: (pdf: Uint8Array, tex: string) => void;
  /** `fatal` (daily quota hit) pauses auto-compiling until the next flush(). */
  onError: (msg: string, fatal: boolean) => void;
  onStateChange: (compiling: boolean) => void;
  debounceMs?: number;
}

const DEFAULT_DEBOUNCE_MS = 1800;

function isFatal(e: unknown): boolean {
  return typeof e === "object" && e !== null && (e as { status?: number }).status === 429;
}

export class CompileScheduler {
  private lastCompiledTex: string | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight: AbortController | null = null;
  private generation = 0;
  private pending: string | null = null;
  private paused = false;
  private disposed = false;

  constructor(private opts: CompileSchedulerOpts) {}

  private get debounceMs(): number {
    return this.opts.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  }

  /** Feed the current buffer; compiles on the trailing edge of a typing burst. */
  input(tex: string): void {
    if (this.disposed || this.paused) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.start(tex);
    }, this.debounceMs);
  }

  /** Compile immediately (initial load, drag-drop, manual Recompile).
   *  Re-enables auto-compiling after a fatal (quota) pause. */
  flush(tex: string): void {
    if (this.disposed) return;
    this.paused = false;
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.start(tex, true);
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.inFlight?.abort();
  }

  private start(tex: string, force = false): void {
    if (!force && tex === this.lastCompiledTex) return;
    if (this.inFlight) {
      // Coalesce: the in-flight result is stale by definition (the buffer
      // changed), so abort the HTTP wait and queue the newest buffer. The
      // finally handler launches it once the aborted call settles.
      this.pending = tex;
      this.inFlight.abort();
      return;
    }
    void this.run(tex);
  }

  private async run(tex: string): Promise<void> {
    const gen = ++this.generation;
    const ac = new AbortController();
    this.inFlight = ac;
    this.opts.onStateChange(true);
    try {
      const pdf = await this.opts.compile(tex, ac.signal);
      if (!this.disposed && gen === this.generation && !ac.signal.aborted) {
        this.lastCompiledTex = tex;
        this.opts.onResult(pdf, tex);
      }
    } catch (e) {
      if (!this.disposed && gen === this.generation && !ac.signal.aborted) {
        const fatal = isFatal(e);
        if (fatal) this.paused = true;
        this.opts.onError(e instanceof Error ? e.message : String(e), fatal);
      }
    } finally {
      if (this.inFlight === ac) this.inFlight = null;
      const next = this.pending;
      this.pending = null;
      if (!this.disposed && !this.paused && next !== null) {
        void this.run(next);
      } else if (!this.disposed) {
        this.opts.onStateChange(false);
      }
    }
  }
}
