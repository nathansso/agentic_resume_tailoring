import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompileScheduler } from "./compileScheduler";

interface Harness {
  scheduler: CompileScheduler;
  calls: { tex: string; signal: AbortSignal }[];
  results: { pdf: Uint8Array; tex: string }[];
  errors: { msg: string; fatal: boolean }[];
  states: boolean[];
  /** Settle the promise for the i-th compile call (compiles only settle manually,
   *  so tests control exactly when an in-flight call finishes). */
  resolve: (i: number, pdf?: Uint8Array) => Promise<void>;
  reject: (i: number, err: unknown) => Promise<void>;
}

function makeHarness(): Harness {
  const calls: Harness["calls"] = [];
  const settlers: { resolve: (pdf: Uint8Array) => void; reject: (e: unknown) => void }[] = [];
  const results: Harness["results"] = [];
  const errors: Harness["errors"] = [];
  const states: boolean[] = [];

  const scheduler = new CompileScheduler({
    compile: (tex, signal) => {
      calls.push({ tex, signal });
      return new Promise<Uint8Array>((resolve, reject) => {
        settlers.push({ resolve, reject });
      });
    },
    onResult: (pdf, tex) => results.push({ pdf, tex }),
    onError: (msg, fatal) => errors.push({ msg, fatal }),
    onStateChange: c => states.push(c),
  });

  return {
    scheduler, calls, results, errors, states,
    resolve: async (i, pdf = new Uint8Array([1])) => {
      settlers[i].resolve(pdf);
      await vi.advanceTimersByTimeAsync(0);
    },
    reject: async (i, err) => {
      settlers[i].reject(err);
      await vi.advanceTimersByTimeAsync(0);
    },
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("CompileScheduler", () => {
  it("compiles on the trailing edge of a typing burst (one compile per burst)", async () => {
    const h = makeHarness();
    h.scheduler.input("a");
    await vi.advanceTimersByTimeAsync(1000);
    h.scheduler.input("ab");
    await vi.advanceTimersByTimeAsync(1000);
    h.scheduler.input("abc");
    expect(h.calls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1800);
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0].tex).toBe("abc");
    await h.resolve(0);
    expect(h.results[0].tex).toBe("abc");
  });

  it("skips compiling when the buffer matches the last compiled tex", async () => {
    const h = makeHarness();
    h.scheduler.input("same");
    await vi.advanceTimersByTimeAsync(1800);
    await h.resolve(0);
    expect(h.calls).toHaveLength(1);

    h.scheduler.input("changed");
    h.scheduler.input("same"); // user undid their edit before the debounce fired
    await vi.advanceTimersByTimeAsync(1800);
    expect(h.calls).toHaveLength(1);
  });

  it("coalesces inputs while a compile is in flight — latest wins, in-flight aborted", async () => {
    const h = makeHarness();
    h.scheduler.flush("v1");
    expect(h.calls).toHaveLength(1);

    // Two edits settle their debounce while v1 is still compiling: both queue
    // as the single pending buffer, latest winning.
    h.scheduler.input("v2");
    await vi.advanceTimersByTimeAsync(1800);
    h.scheduler.input("v3");
    await vi.advanceTimersByTimeAsync(1800);
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0].signal.aborted).toBe(true);

    // The in-flight call settles (aborted fetches reject; result is discarded
    // either way) and only the latest pending buffer compiles next.
    await h.reject(0, new DOMException("aborted", "AbortError"));
    expect(h.calls).toHaveLength(2);
    expect(h.calls[1].tex).toBe("v3");
    await h.resolve(1);
    expect(h.results).toHaveLength(1);
    expect(h.results[0].tex).toBe("v3");
    expect(h.errors).toHaveLength(0); // the aborted call surfaces no error
  });

  it("discards a stale result that resolves after being superseded", async () => {
    const h = makeHarness();
    h.scheduler.flush("old");
    h.scheduler.input("new");
    await vi.advanceTimersByTimeAsync(1800); // "old" aborted, "new" pending
    await h.resolve(0, new Uint8Array([9])); // stale result arrives anyway
    expect(h.results).toHaveLength(0);       // …and is ignored
    expect(h.calls).toHaveLength(2);         // "new" launched from the settle
    await h.resolve(1);
    expect(h.results).toHaveLength(1);
    expect(h.results[0].tex).toBe("new");
  });

  it("pauses auto-compiling after a fatal (429) error; flush re-enables", async () => {
    const h = makeHarness();
    h.scheduler.flush("v1");
    const quota = Object.assign(new Error("Daily preview-compile limit reached"), { status: 429 });
    await h.reject(0, quota);
    expect(h.errors).toEqual([{ msg: "Daily preview-compile limit reached", fatal: true }]);

    h.scheduler.input("v2");
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.calls).toHaveLength(1); // paused — no auto compile

    h.scheduler.flush("v2"); // manual Recompile re-enables
    expect(h.calls).toHaveLength(2);
    await h.resolve(1);
    expect(h.results[0].tex).toBe("v2");
  });

  it("treats non-429 failures as recoverable and keeps auto-compiling", async () => {
    const h = makeHarness();
    h.scheduler.flush("broken");
    const err = Object.assign(new Error("Undefined control sequence"), { status: 422 });
    await h.reject(0, err);
    expect(h.errors).toEqual([{ msg: "Undefined control sequence", fatal: false }]);

    h.scheduler.input("fixed");
    await vi.advanceTimersByTimeAsync(1800);
    expect(h.calls).toHaveLength(2);
  });

  it("reports one continuous busy window across a coalesced burst", async () => {
    const h = makeHarness();
    h.scheduler.flush("v1");
    expect(h.states).toEqual([true]);
    h.scheduler.input("v2");
    await vi.advanceTimersByTimeAsync(1800); // aborts v1, queues v2
    await h.reject(0, new DOMException("aborted", "AbortError")); // v2 starts
    await h.resolve(1);
    expect(h.states[h.states.length - 1]).toBe(false);
    expect(h.states.filter(sx => !sx)).toHaveLength(1); // never flipped false mid-burst
  });

  it("does nothing after dispose", async () => {
    const h = makeHarness();
    h.scheduler.flush("v1");
    h.scheduler.dispose();
    await h.resolve(0);
    expect(h.results).toHaveLength(0);
    h.scheduler.input("v2");
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.calls).toHaveLength(1);
  });
});
