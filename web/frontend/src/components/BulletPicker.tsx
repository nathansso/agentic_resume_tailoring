import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode,
} from "react";
import { listJobs } from "../api/jobs";
import { getEvidenceView } from "../api/profile";
import { useJobSkills } from "../hooks/useJobSkills";
import {
  clampBudget, coverage, DEFAULT_BUDGET, rankBullets, resolveKept, resumePreview, toggleKept,
  type Coverage, type CoverageRow, type CoverStatus, type PickerBullet, type PickerModel, type PreviewSection,
} from "../lib/bulletPicker";
import { dateRange, highlightSegments, linkPath } from "../lib/evidenceMap";
import { cn } from "../lib/utils";
import type { EvidenceKind, EvidenceView, JobListItem } from "../types";
import { ItemEditor } from "./EvidencePanel";

/*
 * The bullet picker (issue #319): for one job, the user's bullets ranked by how much each
 * adds to the job's required-skill coverage, a bullet-count budget with a cut line, keep
 * and drop toggles, a coverage column with a line from every bullet to the skills it
 * cites, and a résumé preview of what is kept. `lib/bulletPicker.ts` ranks and counts;
 * this file draws it and owns the job choice, the budget, the kept set (UI state only:
 * nothing is saved or sent to tailoring) and the panel. The only inline geometry is the
 * link paths, measured from the laid-out rows so they follow wrapped text.
 */

const KIND_DOT: Record<EvidenceKind, string> = {
  exp: "bg-graph-experience rounded-[2px]",
  proj: "bg-graph-project rotate-45 rounded-[2px] scale-90",
  edu: "bg-graph-education rounded-full",
};

const STATUS_LABEL: Record<CoverStatus, string> = {
  covered: "covered by a kept bullet",
  coverable: "a bullet could cover it, none is kept",
  claimed: "on your profile, but no bullet names it",
  missing: "not on your profile",
};

const hint =
  "The budget counts bullets, not lines. Tailoring's page check counts lines (a 60-line budget, no bullet over 2 lines), so a kept set can still run over.";

/** Run `fn` on Enter or Space, the keys a checkbox or button answers to. */
function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

interface LinkPath { key: string; bullet: string; req: string; d: string }

export function BulletPicker({ defaultJobId = null, onChanged }: { defaultJobId?: string | null; onChanged?: () => void }) {
  const [view, setView] = useState<EvidenceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(defaultJobId);
  const [budget, setBudget] = useState(DEFAULT_BUDGET);
  const [override, setOverride] = useState<ReadonlySet<string> | null>(null);
  const [showRest, setShowRest] = useState(false);
  const [focusReq, setFocusReq] = useState<string | null>(null);
  const [openItem, setOpenItem] = useState<string | null>(null);
  const [paths, setPaths] = useState<LinkPath[]>([]);
  const { skills: jobSkills, error: skillsError } = useJobSkills(jobId);

  const board = useRef<HTMLDivElement>(null);
  const rowEls = useRef(new Map<string, HTMLElement>());
  const reqEls = useRef(new Map<string, HTMLElement>());

  const reload = useCallback(async () => {
    try {
      setView(await getEvidenceView());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load your bullets");
    }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {
    listJobs().then((list) => {
      setJobs(list);
      setJobId((cur) => (cur && list.some((j) => j.job_id === cur) ? cur : list[0]?.job_id ?? null));
    }).catch((e) => setError(e instanceof Error ? e.message : "Failed to load jobs"));
  }, []);

  const model: PickerModel | null = useMemo(
    () => (view && jobSkills && jobSkills.length > 0 ? rankBullets(view, jobSkills) : null),
    [view, jobSkills],
  );
  const kept = useMemo(() => (model ? resolveKept(model, budget, override) : new Set<string>()), [model, budget, override]);
  const cover: Coverage | null = useMemo(() => (model ? coverage(model, kept) : null), [model, kept]);
  const preview = useMemo(() => (model ? resumePreview(model, kept) : []), [model, kept]);
  const job = jobs?.find((j) => j.job_id === jobId) ?? null;

  const chooseJob = (id: string) => { setJobId(id); setOverride(null); setFocusReq(null); };
  const toggle = (id: string) => model && setOverride(toggleKept(kept, id));

  // The lines from each bullet to the skills it cites, measured from the laid-out rows.
  const measure = useCallback(() => {
    const el = board.current;
    if (!el || !model) { setPaths([]); return; }
    const box = el.getBoundingClientRect();
    const next: LinkPath[] = [];
    for (const b of model.ranked) {
      const row = rowEls.current.get(b.id);
      if (!row) continue;
      const r = row.getBoundingClientRect();
      for (const req of b.covers) {
        const target = reqEls.current.get(req);
        if (!target) continue;
        const t = target.getBoundingClientRect();
        next.push({
          key: `${b.id}>${req}`, bullet: b.id, req,
          d: linkPath(r.right - box.left + 2, r.top - box.top + 18, t.left - box.left - 2, t.top - box.top + t.height / 2),
        });
      }
    }
    setPaths((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }, [model]);
  useLayoutEffect(measure, [measure, budget, showRest]);
  useEffect(() => {
    const el = board.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure, model]);

  if (error) return <p className="p-4 text-sm text-destructive">{error}</p>;
  if (!view || jobs === null) return <p className="p-4 text-sm text-muted-foreground">Loading…</p>;
  if (jobs.length === 0) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        No saved jobs yet. Add a job from the sidebar and the picker will rank your bullets against it.
      </p>
    );
  }

  const editing = openItem ? view.items.find((i) => i.key === openItem) : undefined;
  const select = (key: string | null) => {
    if (key === null) { setOpenItem(null); return; }
    if (view.items.some((i) => i.key === key)) { setOpenItem(key); return; }
    const req = model?.matches.find((m) => m.skills.some((s) => s.key === key));   // a skill link in the editor
    if (req) setFocusReq(req.id);
  };

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Toolbar
          jobs={jobs} jobId={jobId} onJob={chooseJob} budget={budget} onBudget={(n) => setBudget(clampBudget(n))}
          cover={cover} kept={kept.size} custom={override !== null} onReset={() => setOverride(null)}
        />
        <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">
          {skillsError ? (
            <p className="text-[13px] text-destructive">{skillsError}</p>
          ) : jobSkills === null ? (
            <p className="text-[13px] text-muted-foreground">Loading the job…</p>
          ) : !model || !cover ? (
            <p className="text-[13px] text-muted-foreground">
              This job has no requirements yet. Open it with your agent, or analyze it from its page, and your bullets will be ranked here.
            </p>
          ) : (
            <>
              <NotCoverable cover={cover} />
              <div ref={board} className="relative grid min-w-[44rem] grid-cols-[minmax(0,1fr)_4.5rem_14rem]">
                <ol className="min-w-0 space-y-0.5" aria-label="Bullets, best first">
                  {model.ranked.map((b) => (
                    <BulletRow
                      key={b.id} bullet={b} kept={kept.has(b.id)} model={model}
                      dim={focusReq !== null && !b.covers.includes(focusReq)}
                      cut={b.rank === budget && b.rank < model.ranked.length}
                      onToggle={() => toggle(b.id)} onOpen={() => setOpenItem(b.item.key)}
                      refEl={(el) => { if (el) rowEls.current.set(b.id, el); else rowEls.current.delete(b.id); }}
                    />
                  ))}
                  <li className="flex items-center gap-2 px-2 pb-1.5 pt-4 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
                    {model.rest.length > 0 && (
                      <button
                        type="button" onClick={() => setShowRest((v) => !v)} aria-expanded={showRest}
                        className="rounded border border-border px-1.5 py-px normal-case tracking-normal hover:bg-muted"
                      >
                        {showRest ? "Hide" : "Show"}
                      </button>
                    )}
                    {model.rest.length} bullet{model.rest.length === 1 ? "" : "s"} name none of this job's skills
                  </li>
                  {showRest && model.rest.map((b) => (
                    <BulletRow
                      key={b.id} bullet={b} kept={kept.has(b.id)} model={model} dim={false} cut={false}
                      onToggle={() => toggle(b.id)} onOpen={() => setOpenItem(b.item.key)}
                    />
                  ))}
                </ol>
                <div aria-hidden="true" />
                <CoverageColumn
                  cover={cover} focus={focusReq} onFocus={(id) => setFocusReq((cur) => (cur === id ? null : id))}
                  refEl={(id, el) => { if (el) reqEls.current.set(id, el); else reqEls.current.delete(id); }}
                />
                {/* Link paths come from measured row and skill positions at runtime, so they are drawn in pixels. */}
                <svg className="pointer-events-none absolute inset-0 h-full w-full overflow-visible" aria-hidden="true">
                  {paths.map((p) => {
                    const on = kept.has(p.bullet);
                    const lit = focusReq === null || focusReq === p.req;
                    return (
                      <path
                        key={p.key} d={p.d} fill="none"
                        strokeWidth={on ? 1.6 : 1}
                        className={cn(
                          on ? "stroke-graph-evidence" : "stroke-graph-claim",
                          !lit ? "opacity-10" : on ? "opacity-70" : "opacity-25",
                        )}
                      />
                    );
                  })}
                </svg>
              </div>
            </>
          )}
        </div>
      </div>
      <aside
        aria-label="Details"
        className="max-h-[50vh] w-full shrink-0 overflow-y-auto border-t border-border bg-card p-4 lg:max-h-none lg:w-[24rem] lg:border-l lg:border-t-0"
      >
        {editing ? (
          <>
            <button
              type="button" onClick={() => setOpenItem(null)}
              className="mb-3 rounded-md border border-border bg-background px-2 py-0.5 text-[12px] font-medium hover:bg-muted"
            >
              ← Résumé preview
            </button>
            <ItemEditor
              key={editing.key} view={view} item={editing} onSelect={select}
              onSaved={async () => { await reload(); onChanged?.(); }}
            />
          </>
        ) : (
          <Preview
            sections={preview} count={kept.size} job={job} ready={model !== null}
            onOpen={(key) => setOpenItem(key)}
          />
        )}
      </aside>
    </div>
  );
}

// ── toolbar ────────────────────────────────────────────────────────────────────

function Count({ n, of, label, tone }: { n: number | string; of?: number | string; label: string; tone?: "proven" }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className={cn("font-mono text-[12px] font-medium tabular-nums", tone === "proven" ? "text-accent" : "text-foreground")}>
        {n}{of !== undefined ? ` of ${of}` : ""}
      </span>
      {label}
    </span>
  );
}

function Toolbar(props: {
  jobs: JobListItem[]; jobId: string | null; onJob: (id: string) => void;
  budget: number; onBudget: (n: number) => void; cover: Coverage | null; kept: number;
  custom: boolean; onReset: () => void;
}) {
  const { cover } = props;
  const stepper = "h-6 w-6 rounded-md border border-border bg-background text-[14px] leading-none hover:bg-muted disabled:opacity-40";
  return (
    <div className="shrink-0 space-y-2 px-4 pb-2 pt-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <label className="inline-flex items-center gap-2 text-[13px] text-muted-foreground">
          Job
          <select
            value={props.jobId ?? ""} onChange={(e) => props.onJob(e.target.value)}
            className="max-w-[18rem] rounded-md border border-border bg-background px-2 py-1.5 text-[13px] text-foreground"
          >
            {props.jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.title} · {j.company}</option>)}
          </select>
        </label>
        <span className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground">
          Page budget
          <button type="button" className={stepper} aria-label="Fewer bullets" onClick={() => props.onBudget(props.budget - 1)}>−</button>
          <b className="min-w-[1.25rem] text-center font-mono font-medium tabular-nums text-foreground" aria-live="polite">{props.budget}</b>
          <button type="button" className={stepper} aria-label="More bullets" onClick={() => props.onBudget(props.budget + 1)}>+</button>
          bullets
        </span>
        {props.custom && (
          <button
            type="button" onClick={props.onReset}
            className="rounded-md border border-border px-2 py-1 text-[12px] hover:bg-muted"
          >
            Use ranked picks
          </button>
        )}
      </div>
      {cover && (
        // Required, weighted required and preferred stay three separate counts: never one score, never mixed with the ATS composite.
        <p className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[13px] text-muted-foreground" aria-label="Coverage">
          <Count n={props.kept} label="kept" />
          <Count n={cover.required.covered} of={cover.required.total} label="required covered" tone="proven" />
          <Count n={cover.required.weightCovered} of={cover.required.weightTotal} label="weighted" />
          <Count n={cover.preferred.covered} of={cover.preferred.total} label="preferred covered" />
        </p>
      )}
      <p className="text-[12px] text-muted-foreground">{hint}</p>
    </div>
  );
}

/** Required skills no bullet can cover, named, so a gap is never silent. */
function NotCoverable({ cover }: { cover: Coverage }) {
  if (cover.claimed.length === 0 && cover.missing.length === 0) return null;
  const names = (rows: CoverageRow[]) => rows.map((r) => r.label).join(", ");
  return (
    <p className="mb-3 rounded-md border border-graph-gap/40 p-3 text-[13px] text-muted-foreground" role="note">
      {cover.claimed.length > 0 && (
        <span>
          No bullet can cover <b className="font-semibold text-graph-gap">{names(cover.claimed)}</b>: it is on your profile,
          but nothing you wrote names it.{" "}
        </span>
      )}
      {cover.missing.length > 0 && (
        <span>
          <b className="font-semibold text-graph-gap">{names(cover.missing)}</b>{" "}
          {cover.missing.length > 1 ? "are" : "is"} not on your profile.
        </span>
      )}
    </p>
  );
}

// ── the ranked list ────────────────────────────────────────────────────────────

function BulletRow({ bullet, kept, model, dim, cut, onToggle, onOpen, refEl }: {
  bullet: PickerBullet & { rank?: number; gain?: string[] }; kept: boolean; model: PickerModel; dim: boolean; cut: boolean;
  onToggle: () => void; onOpen: () => void; refEl?: (el: HTMLElement | null) => void;
}) {
  const ranked = bullet.rank !== undefined;
  const name = (id: string) => model.matches.find((m) => m.id === id)?.skills[0]?.name ?? model.matches.find((m) => m.id === id)?.row.name ?? id;
  const label = bullet.item.kind === "exp" ? bullet.item.org || bullet.item.title : bullet.item.title;
  return (
    <>
      <li className={cn("rounded-lg px-2 py-2 transition-opacity duration-150 motion-reduce:transition-none", kept && "bg-graph-evidence/10", dim && "opacity-30")}>
        <div ref={refEl} className="grid grid-cols-[1.25rem_1.5rem_minmax(0,1fr)] gap-x-2">
          <span
            role="checkbox" aria-checked={kept} tabIndex={0}
            aria-label={`Keep: ${bullet.text.length > 80 ? `${bullet.text.slice(0, 80)}…` : bullet.text}`}
            onClick={onToggle} onKeyDown={onActivate(onToggle)}
            className={cn(
              "mt-0.5 grid h-4 w-4 cursor-pointer place-items-center rounded-[4px] border-[1.5px]",
              kept ? "border-graph-evidence bg-graph-evidence" : "border-muted-foreground",
            )}
          >
            {kept && (
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <path d="M1.5 5.2 4 7.5 8.5 2.5" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="stroke-background" />
              </svg>
            )}
          </span>
          <span className="pt-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">{ranked ? bullet.rank : ""}</span>
          <span className={cn("min-w-0 cursor-pointer text-[13.5px] leading-snug", !ranked && "text-muted-foreground")} onClick={onToggle}>
            {highlightSegments(bullet.text, bullet.marks).map((seg, i) => (
              seg.hit
                ? <mark key={i} className="rounded-sm bg-graph-evidence/20 px-0.5 text-foreground">{seg.text}</mark>
                : <span key={i}>{seg.text}</span>
            ))}
          </span>
        </div>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-[3.25rem] font-mono text-[11.5px] text-muted-foreground">
          <button
            type="button" onClick={onOpen}
            className="inline-flex items-center gap-1.5 font-sans text-[12px] font-medium text-foreground hover:underline"
          >
            <span className={cn("inline-block h-2 w-2", KIND_DOT[bullet.item.kind])} aria-hidden="true" />
            {label}
          </button>
          <span>#b{bullet.ordinal}</span>
          {ranked && (bullet.gain && bullet.gain.length > 0
            ? <span className="text-accent">adds {bullet.gain.map(name).join(", ")}</span>
            : <span>adds nothing new</span>)}
        </p>
      </li>
      {cut && (
        <li role="separator" aria-label="Page budget: bullets below this line are over it" className="flex items-center gap-2 py-1 font-mono text-[10.5px] uppercase tracking-wider text-accent">
          <span className="flex-1 border-t border-dashed border-accent/60" />
          Page budget
          <span className="flex-1 border-t border-dashed border-accent/60" />
        </li>
      )}
    </>
  );
}

// ── the coverage column ────────────────────────────────────────────────────────

function StatusGlyph({ status }: { status: CoverStatus }) {
  return (
    <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true" className="shrink-0">
      {status === "covered" && <circle r="5" className="fill-graph-evidence" />}
      {status === "coverable" && <circle r="4.5" fill="none" strokeWidth="1.5" className="stroke-graph-evidence" />}
      {(status === "claimed" || status === "missing") && (
        <circle r="5" fill="none" strokeWidth="1.2" strokeDasharray="2 2.2" className="stroke-graph-gap" />
      )}
    </svg>
  );
}

function CoverageColumn({ cover, focus, onFocus, refEl }: {
  cover: Coverage; focus: string | null; onFocus: (id: string) => void; refEl: (id: string, el: HTMLElement | null) => void;
}) {
  const group = (title: string, rows: CoverageRow[]): ReactNode => rows.length > 0 && (
    <>
      <li className="px-2 pb-1 pt-3 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground first:pt-0">{title}</li>
      {rows.map((r) => (
        <li key={r.match.id}>
          <div
            ref={(el) => refEl(r.match.id, el)} role="button" tabIndex={0} aria-pressed={focus === r.match.id}
            aria-label={`${r.label}: ${STATUS_LABEL[r.status]}`}
            onClick={() => onFocus(r.match.id)} onKeyDown={onActivate(() => onFocus(r.match.id))}
            className={cn(
              "flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-card",
              focus === r.match.id && "bg-muted",
            )}
          >
            <span className="mt-0.5"><StatusGlyph status={r.status} /></span>
            <span className="min-w-0 flex-1">
              <span className={cn(r.status === "missing" && "italic text-graph-gap", r.status === "claimed" && "text-graph-gap")}>{r.label}</span>
              {(r.status === "claimed" || r.status === "missing") && (
                <span className="block text-[11px] text-muted-foreground">
                  {r.status === "claimed" ? "no bullet names it" : "not on your profile"}
                </span>
              )}
            </span>
            <span className="font-mono text-[10.5px] text-muted-foreground">{r.match.row.required ? `×${r.match.row.weight}` : "pref"}</span>
          </div>
        </li>
      ))}
    </>
  );
  return (
    <ul className="min-w-0 content-start" aria-label="Job skills and coverage">
      {group("Required", cover.rows.filter((r) => r.match.row.required))}
      {group("Preferred", cover.rows.filter((r) => !r.match.row.required))}
    </ul>
  );
}

// ── the résumé preview ─────────────────────────────────────────────────────────

function Preview({ sections, count, job, ready, onOpen }: {
  sections: PreviewSection[]; count: number; job: JobListItem | null; ready: boolean; onOpen: (itemKey: string) => void;
}) {
  return (
    <div>
      <p className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-graph-evidence" aria-hidden="true" />
        Résumé preview
      </p>
      <h3 className="mt-1 text-lg font-semibold">{job ? job.title : "Pick a job"}</h3>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        {count} bullet{count === 1 ? "" : "s"} · in résumé order
      </p>
      {ready && sections.length === 0 && (
        <p className="mt-4 text-[13px] text-muted-foreground">No bullets kept. Tick bullets on the left to build the page.</p>
      )}
      <div className="mt-4 space-y-4">
        {sections.map((s) => (
          <section key={s.title} aria-label={s.title}>
            <h4 className="border-b border-border pb-1 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{s.title}</h4>
            <div className="mt-2 space-y-3">
              {s.items.map(({ item, bullets }) => (
                <div key={item.key}>
                  <p className="flex flex-wrap items-baseline gap-x-2 text-[13.5px]">
                    <button type="button" onClick={() => onOpen(item.key)} className="font-semibold hover:underline">{item.title}</button>
                    {item.kind === "exp" && item.org && <span className="text-[12.5px] text-muted-foreground">{item.org}</span>}
                  </p>
                  <p className="font-mono text-[11px] text-muted-foreground">{dateRange(item.start, item.end)}</p>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[12.5px] leading-snug">
                    {bullets.map((b) => <li key={b.id}>{b.text}</li>)}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
      <p className="mt-5 text-[12px] text-muted-foreground">
        A selection, not a rewrite. Tailoring can reword these, but every skill on the page still has to trace back to one of them.
        Click an item to edit it; the ranking updates when you save.
      </p>
    </div>
  );
}
