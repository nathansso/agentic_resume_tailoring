import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Search } from "lucide-react";
import { getJobSkills, listJobs } from "../api/jobs";
import { getEvidenceView } from "../api/profile";
import type { EvidenceItem, EvidenceKind, EvidenceView, JobListItem, JobSkillRow } from "../types";
import {
  areaFoldKey, dateRange, estimateTextWidth, firstMatch, fitText, ghostBlock, jobOverlay, layoutMap,
  neighborhood, sourceArcs, type JobOverlay, type MapLayout, type PlacedArea, type PlacedRow,
} from "../lib/evidenceMap";
import { cn } from "../lib/utils";
import { EvidencePanel } from "./EvidencePanel";

/*
 * The evidence map (issue #136): experiences and projects on the left, skills by area
 * on the right, a line for every bullet that cites a skill. This file draws what
 * `lib/evidenceMap.ts` lays out and owns selection, hover, folding, search and the job
 * toggle. All geometry (x, y, width, transform, viewBox) comes from that layout at
 * runtime, so it is set as SVG attributes; every colour and type style is a Tailwind
 * utility over the `graph-*` tokens in index.css.
 */

const KIND_LABEL: Record<EvidenceKind, string> = { exp: "Experience", proj: "Project", edu: "Education" };
const KIND_FILL: Record<EvidenceKind, string> = {
  exp: "fill-graph-experience", proj: "fill-graph-project", edu: "fill-graph-education",
};

/** Run `fn` on Enter or Space, the keys a button answers to. */
function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

/** Square for an experience, diamond for a project, triangle for education: kind never rests on hue alone. */
function Glyph({ kind, size }: { kind: EvidenceKind; size: number }) {
  const cls = KIND_FILL[kind];
  if (kind === "exp") return <rect x={-size} y={-size} width={2 * size} height={2 * size} rx={1.6} className={cls} />;
  if (kind === "proj") {
    const s = size * 1.3;
    return <path d={`M0,${-s}L${s},0L0,${s}L${-s},0Z`} className={cls} />;
  }
  return <path d={`M0,${-size * 1.25}L${size * 1.2},${size * 0.9}L${-size * 1.2},${size * 0.9}Z`} className={cls} />;
}

function Pips({ value, x }: { value: number; x: number }) {
  return (
    <g aria-hidden="true">
      {[0, 1, 2, 3, 4].map((i) => (
        <rect
          key={i} x={x + i * 5} y={-3} width={3} height={6} rx={1}
          className={i < value ? "fill-muted-foreground" : "fill-muted-foreground/25"}
        />
      ))}
    </g>
  );
}

export function EvidenceMap({ defaultJobId = null }: { defaultJobId?: string | null }) {
  const [view, setView] = useState<EvidenceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const [query, setQuery] = useState("");
  const [width, setWidth] = useState(900);
  const [jobOn, setJobOn] = useState(false);
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(defaultJobId);
  const [jobSkills, setJobSkills] = useState<JobSkillRow[] | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const loaded = view !== null;

  const reload = useCallback(async () => {
    try {
      setView(await getEvidenceView());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load the evidence map");
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  // The map is as wide as its column (never under the minimum the layout can be read at).
  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width) - 32));
    ro.observe(el);
    return () => ro.disconnect();
  }, [loaded]);

  // The job picker lists saved jobs the first time the toggle goes on.
  useEffect(() => {
    if (!jobOn || jobs !== null) return;
    listJobs().then((list) => {
      setJobs(list);
      setJobId((cur) => (cur && list.some((j) => j.job_id === cur) ? cur : list[0]?.job_id ?? null));
    }).catch((e) => setJobError(e instanceof Error ? e.message : "Failed to load jobs"));
  }, [jobOn, jobs]);

  useEffect(() => {
    if (!jobOn || !jobId) { setJobSkills(null); return; }
    let live = true;
    setJobError(null);
    getJobSkills(jobId)
      .then((rows) => { if (live) setJobSkills(rows); })
      .catch((e) => { if (live) { setJobSkills(null); setJobError(e instanceof Error ? e.message : "Failed to load the job's skills"); } });
    return () => { live = false; };
  }, [jobOn, jobId]);

  const layout = useMemo(
    () => (view ? layoutMap(view, { width, folded, query }) : null),
    [view, width, folded, query],
  );
  const overlay = useMemo(
    () => (view && jobOn && jobSkills && jobSkills.length > 0 ? jobOverlay(view, jobSkills) : null),
    [view, jobOn, jobSkills],
  );
  const ghosts = useMemo(
    () => (layout && overlay ? ghostBlock(layout, overlay.missing) : null),
    [layout, overlay],
  );

  const known = useMemo(() => new Set([
    ...(view?.items ?? []).map((i) => i.key), ...(view?.skills ?? []).map((s) => s.key),
  ]), [view]);
  const current = selected && (known.has(selected) || selected.startsWith("ghost:")) ? selected : null;

  // What stays lit: the hovered or selected node and what it links; else the search hits and theirs.
  const lit = useMemo(() => {
    if (!view || !layout) return null;
    const active = hover ?? current;
    if (active && !active.startsWith("ghost:")) return neighborhood(view, active);
    if (!active && layout.matches) {
      const out = new Set<string>();
      layout.matches.forEach((k) => neighborhood(view, k).forEach((n) => out.add(n)));
      return out;
    }
    return null;
  }, [view, layout, hover, current]);
  const active = hover ?? current;

  const toggleFold = (area: string) => setFolded((prev) => {
    const next = new Set(prev);
    const key = areaFoldKey(area);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const select = (key: string) => setSelected((cur) => (cur === key ? null : key));

  if (error) return <p className="p-4 text-sm text-destructive">{error}</p>;
  if (!view || !layout) return <p className="p-4 text-sm text-muted-foreground">Loading…</p>;
  if (view.items.length === 0 && view.skills.length === 0) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        Nothing to map yet. Ingest a resume or GitHub profile and the map will show which of your
        experiences and projects back each skill.
      </p>
    );
  }

  const totalHeight = layout.height + (ghosts?.height ?? 0);

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Toolbar
          view={view} query={query} setQuery={setQuery}
          onEnter={() => { const k = firstMatch(layout); if (k) setSelected(k); }}
          jobOn={jobOn} setJobOn={setJobOn} jobs={jobs} jobId={jobId} setJobId={setJobId}
          jobSkills={jobSkills} jobError={jobError} overlay={overlay}
        />
        <div ref={stage} className="min-h-0 flex-1 overflow-auto px-4 pb-4">
          <svg
            role="group" aria-label="Evidence map: experiences and projects on the left, skills by area on the right"
            viewBox={`0 0 ${layout.width} ${totalHeight}`} width={layout.width} height={totalHeight}
            className="block max-w-none select-none font-sans"
          >
            <Edges layout={layout} active={active} lit={lit} overlay={overlay} />
            <Rows layout={layout} selected={current} lit={lit} onSelect={select} onHover={setHover} />
            <SkillColumn
              layout={layout} selected={current} lit={lit} overlay={overlay}
              onSelect={select} onHover={setHover} onFold={toggleFold}
            />
            {overlay && ghosts && ghosts.chips.length > 0 && (
              <GhostBlock
                layout={layout} ghosts={ghosts} selected={current} onSelect={select}
              />
            )}
          </svg>
        </div>
      </div>
      <EvidencePanel
        view={view} selected={current} overlay={overlay} jobOn={jobOn}
        onSelect={setSelected} onSaved={reload}
      />
    </div>
  );
}

// ── toolbar ────────────────────────────────────────────────────────────────────

function Count({ n, label, tone }: { n: number; label: string; tone?: "proven" | "gap" }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span
        className={cn(
          "font-mono text-[12px] font-medium tabular-nums",
          tone === "proven" && "text-accent",
          tone === "gap" && n > 0 && "text-graph-gap",
        )}
      >
        {n}
      </span>
      <span>{label}</span>
    </span>
  );
}

function Toolbar(props: {
  view: EvidenceView;
  query: string; setQuery: (q: string) => void; onEnter: () => void;
  jobOn: boolean; setJobOn: (on: boolean) => void;
  jobs: JobListItem[] | null; jobId: string | null; setJobId: (id: string) => void;
  jobSkills: JobSkillRow[] | null; jobError: string | null; overlay: JobOverlay | null;
}) {
  const { stats } = props.view;
  const noJobs = props.jobOn && props.jobs !== null && props.jobs.length === 0;
  return (
    <div className="shrink-0 space-y-2 px-4 pb-1 pt-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-muted-foreground" aria-label="Summary">
        <Count n={stats.skills} label="skills" />
        <Count n={stats.evidenced} label="cited by a bullet" tone="proven" />
        <Count n={stats.unevidenced} label="with no evidence" />
        <Count n={stats.github_only} label="GitHub only" />
        <Count n={stats.needs_text} label={stats.needs_text === 1 ? "item needs a description" : "items need a description"} tone="gap" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label className="relative inline-flex">
          <span className="sr-only">Find a skill</span>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            type="search" value={props.query} placeholder="Find a skill"
            onChange={(e) => props.setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); props.onEnter(); }
              if (e.key === "Escape") props.setQuery("");
            }}
            className="w-48 rounded-md border border-border bg-background py-1.5 pl-8 pr-2 text-[13px] placeholder:text-muted-foreground"
          />
        </label>
        <button
          type="button" role="switch" aria-checked={props.jobOn}
          onClick={() => props.setJobOn(!props.jobOn)}
          className={cn(
            "inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[13px] transition-colors",
            props.jobOn ? "border-foreground/40 bg-muted text-foreground" : "border-border text-muted-foreground hover:bg-muted",
          )}
        >
          <span
            aria-hidden="true"
            className={cn("relative h-3.5 w-6 rounded-full transition-colors", props.jobOn ? "bg-foreground/70" : "bg-muted-foreground/30")}
          >
            <span
              className={cn(
                "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-background transition-all motion-reduce:transition-none",
                props.jobOn ? "left-3" : "left-0.5",
              )}
            />
          </span>
          Compare to a job
        </button>
        {props.jobOn && props.jobs && props.jobs.length > 0 && (
          <label className="inline-flex items-center gap-2 text-[13px] text-muted-foreground">
            <span className="sr-only">Job to compare with</span>
            <select
              value={props.jobId ?? ""} onChange={(e) => props.setJobId(e.target.value)}
              className="max-w-[16rem] rounded-md border border-border bg-background px-2 py-1.5 text-[13px] text-foreground"
            >
              {props.jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.title} · {j.company}</option>)}
            </select>
          </label>
        )}
      </div>
      <Legend />
      {/* A fixed slot, so turning the job on adds a line without pushing the map down. */}
      <div className="min-h-[2.75rem] space-y-1 sm:min-h-[1.5rem]">
        {noJobs && <p className="text-[13px] text-muted-foreground">No saved jobs yet. Add one from the sidebar to compare.</p>}
        {props.jobError && <p className="text-[13px] text-destructive">{props.jobError}</p>}
        {props.jobOn && props.jobId && props.jobSkills && props.jobSkills.length === 0 && !props.jobError && (
          <p className="text-[13px] text-muted-foreground">
            This job has no analyzed skills yet. Analyze it from its page and they will appear here.
          </p>
        )}
        {props.overlay && <JobLine overlay={props.overlay} />}
      </div>
    </div>
  );
}

/** Required and preferred, each as three separate counts. Never one number, and nothing from the ATS composite. */
function JobLine({ overlay }: { overlay: JobOverlay }) {
  const group = (name: string, c: JobOverlay["required"]) => (
    <span className="inline-flex flex-wrap items-baseline gap-x-3">
      <span className="font-medium text-foreground">{name}</span>
      <Count n={c.evidenced} label="backed by a bullet" tone="proven" />
      <Count n={c.claimed} label="claimed without evidence" tone="gap" />
      <Count n={c.missing} label="missing" tone="gap" />
    </span>
  );
  return (
    <p className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-[13px] text-muted-foreground" aria-label="Job coverage">
      {group("Required", overlay.required)}
      {group("Preferred", overlay.preferred)}
    </p>
  );
}

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted-foreground" aria-label="Legend">
      {(["exp", "proj", "edu"] as EvidenceKind[]).map((k) => (
        <li key={k} className="inline-flex items-center gap-1.5">
          <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true"><Glyph kind={k} size={4.6} /></svg>
          {KIND_LABEL[k]}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true"><circle r={4} className="fill-graph-evidence" /></svg>
        Skill cited by a bullet
      </li>
      <li className="inline-flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true">
          <circle r={4} className="fill-background stroke-graph-claim [stroke-dasharray:2_2]" />
        </svg>
        No bullet cites it
      </li>
    </ul>
  );
}

// ── edges ──────────────────────────────────────────────────────────────────────

function Edges({ layout, active, lit, overlay }: {
  layout: MapLayout; active: string | null; lit: ReadonlySet<string> | null; overlay: JobOverlay | null;
}) {
  const matches = layout.matches;
  return (
    <g fill="none" aria-hidden="true">
      {layout.edges.map((e) => {
        const hot = active
          ? e.itemKey === active || e.skillKey === active || (e.areaKey !== null && areaFoldKey(e.areaKey) === active)
          : !!matches && e.skillKey !== null && matches.has(e.skillKey);
        const jobFade = overlay && e.skillKey !== null && !overlay.classBySkill.has(e.skillKey);
        return (
          <path
            key={e.id} d={e.d} strokeWidth={e.width}
            className={cn(
              "transition-[opacity,stroke] duration-150 motion-reduce:transition-none",
              hot ? (e.dashed ? "stroke-foreground/60" : "stroke-graph-evidence/90") : "stroke-graph-claim/25",
              e.dashed && "[stroke-dasharray:4_3]",
              lit && !hot && "opacity-30",
              jobFade && !hot && "opacity-20",
            )}
          />
        );
      })}
    </g>
  );
}

// ── the left column ────────────────────────────────────────────────────────────

function Rows({ layout, selected, lit, onSelect, onHover }: {
  layout: MapLayout; selected: string | null; lit: ReadonlySet<string> | null;
  onSelect: (key: string) => void; onHover: (key: string | null) => void;
}) {
  const nItems = layout.fixStart;
  const skills = layout.areas.reduce((n, a) => n + a.total, 0);
  return (
    <g>
      <text x={0} y={16} className="fill-muted-foreground font-mono text-[11px] uppercase tracking-wider">
        Experience &amp; projects · {nItems}
      </text>
      <text x={layout.skillX - 14} y={16} className="fill-muted-foreground font-mono text-[11px] uppercase tracking-wider">
        Skills by area · {skills}
      </text>
      {layout.fixLabelY !== null && (
        <text x={0} y={layout.fixLabelY} className="fill-graph-gap font-mono text-[11px] uppercase tracking-wider">
          Needs a description · {layout.rows.length - layout.fixStart}
        </text>
      )}
      {layout.rows.map((row) => (
        <Row
          key={row.item.key} row={row} layout={layout} selected={selected === row.item.key}
          dim={lit !== null && !lit.has(row.item.key)} onSelect={onSelect} onHover={onHover}
        />
      ))}
    </g>
  );
}

function Row({ row, layout, selected, dim, onSelect, onHover }: {
  row: PlacedRow; layout: MapLayout; selected: boolean; dim: boolean;
  onSelect: (key: string) => void; onHover: (key: string | null) => void;
}) {
  const it: EvidenceItem = row.item;
  const x0 = row.indent * 20;
  const room = layout.leftW - x0 - 28;
  const award = it.achievements[0];
  const sub = [it.org || (it.kind === "proj" && !it.parent_key ? "project" : ""), dateRange(it.start, it.end)]
    .filter(Boolean).join(" · ");
  return (
    <g
      role="button" tabIndex={0} aria-pressed={selected}
      aria-label={`${KIND_LABEL[it.kind]}: ${it.title}${row.fix ? ", needs a description" : ""}`}
      transform={`translate(0,${row.y})`}
      className={cn(
        "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
        dim && "opacity-30",
      )}
      onClick={() => onSelect(it.key)} onKeyDown={onActivate(() => onSelect(it.key))}
      onMouseEnter={() => onHover(it.key)} onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(it.key)} onBlur={() => onHover(null)}
    >
      <rect
        x={-6} y={-21} width={layout.leftW + 10} height={42} rx={6}
        className={cn(
          "stroke-transparent group-hover:fill-muted group-focus-visible:stroke-ring group-focus-visible:stroke-2",
          selected ? "fill-muted stroke-border" : "fill-transparent",
        )}
      />
      {row.indent > 0 && (
        <path d={`M${x0 - 12},${-layout.rowH + 12}V-4H${x0 - 4}`} fill="none" className="stroke-border" />
      )}
      <g transform={`translate(${x0 + 7},-4)`} aria-hidden="true"><Glyph kind={it.kind} size={4.6} /></g>
      <text x={x0 + 20} y={0} className="fill-foreground text-[13px] font-medium [paint-order:stroke]">
        {fitText(it.title, room - (award ? 80 : 0), 13)}
        {award && (
          <tspan dx={8} className="fill-muted-foreground text-[11px] font-normal">★ {fitText(award.title, 70, 11)}</tspan>
        )}
      </text>
      <text x={x0 + 20} y={15} className="fill-muted-foreground text-[12px]">
        {fitText(sub, room - (row.fix ? 130 : 0), 12)}
        {row.fix && <tspan dx={8} className="fill-graph-gap">● needs a description</tspan>}
      </text>
      <circle cx={layout.leftW + 3} cy={0} r={2.2} className="fill-graph-claim" aria-hidden="true" />
    </g>
  );
}

// ── the skill column ───────────────────────────────────────────────────────────

function SkillColumn({ layout, selected, lit, overlay, onSelect, onHover, onFold }: {
  layout: MapLayout; selected: string | null; lit: ReadonlySet<string> | null; overlay: JobOverlay | null;
  onSelect: (key: string) => void; onHover: (key: string | null) => void; onFold: (area: string) => void;
}) {
  return (
    <g>
      {layout.areas.map((area) => (
        <AreaGroup
          key={area.key} area={area} layout={layout} selected={selected} lit={lit} overlay={overlay}
          onSelect={onSelect} onHover={onHover} onFold={onFold}
        />
      ))}
    </g>
  );
}

function AreaGroup({ area, layout, selected, lit, overlay, onSelect, onHover, onFold }: {
  area: PlacedArea; layout: MapLayout; selected: string | null; lit: ReadonlySet<string> | null; overlay: JobOverlay | null;
  onSelect: (key: string) => void; onHover: (key: string | null) => void; onFold: (area: string) => void;
}) {
  const hubKey = areaFoldKey(area.key);
  const req = overlay?.areaRequired.get(area.key);
  const litHere = lit !== null && (lit.has(hubKey)
    || area.skills.some((s) => lit.has(s.skill.key)) || area.chips.some((c) => lit.has(c.skill.key)));
  const matches = layout.matches;
  return (
    <g>
      <g
        role="button" tabIndex={0} aria-expanded={!area.collapsed}
        aria-label={`${area.key}, ${area.evidenced} of ${area.total} evidenced${req ? `, ${req.evidenced} of ${req.total} required` : ""}. ${area.collapsed ? "Expand" : "Collapse"}`}
        transform={`translate(${layout.skillX},${area.hubY})`}
        className={cn(
          "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
          lit !== null && !litHere && "opacity-30",
        )}
        onClick={() => onFold(area.key)} onKeyDown={onActivate(() => onFold(area.key))}
        onMouseEnter={() => onHover(hubKey)} onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(hubKey)} onBlur={() => onHover(null)}
      >
        <rect
          x={-12} y={-11} width={layout.colW + 12} height={22} rx={4}
          className="fill-transparent stroke-transparent group-focus-visible:stroke-ring group-focus-visible:stroke-2"
        />
        {area.collapsed ? (
          <circle
            r={3 + 1.3 * Math.sqrt(area.bullets)}
            className={area.evidenced ? "fill-graph-evidence" : "fill-background stroke-graph-claim [stroke-dasharray:2_2]"}
          />
        ) : (
          <path d="M-4,-2.5L4,-2.5L0,2.5Z" className="fill-muted-foreground" />
        )}
        <text x={16} y={3.5} className="fill-muted-foreground font-mono text-[11px] uppercase tracking-wider group-hover:fill-foreground">
          {area.key}
          <tspan dx={8} className={cn("normal-case tracking-normal", area.evidenced === area.total && area.total > 0 && "fill-accent")}>
            {area.evidenced}/{area.total} evidenced
          </tspan>
          {req && (
            <tspan dx={8} className={cn("normal-case tracking-normal", req.evidenced === req.total ? "fill-accent" : "fill-graph-gap")}>
              {req.evidenced}/{req.total} required
            </tspan>
          )}
        </text>
      </g>
      {!area.collapsed && area.skills.map((p) => {
        const sk = p.skill;
        const cls = overlay?.classBySkill.get(sk.key);
        const faded = overlay !== null && !cls;
        const match = !!matches && matches.has(sk.key);
        const bold = sk.is_core || !!cls?.startsWith("required") || match;
        const label = p.stat.githubOnly ? "GitHub only" : String(p.stat.bullets);
        const nameW = estimateTextWidth(sk.name, 12.5) + (bold ? 6 : 0);
        const lx = Math.max(p.r, 8) + 10;
        const arcs = sourceArcs(0, 0, p.r + 3, Math.max(1, sk.sources.length));
        return (
          <g
            key={sk.key} role="button" tabIndex={0} aria-pressed={selected === sk.key}
            aria-label={`Skill: ${sk.name}, ${p.stat.cited ? `${p.stat.bullets} citing bullet${p.stat.bullets === 1 ? "" : "s"}` : "no citing bullet, GitHub only"}${cls ? `, ${cls.replace("-", " ")} for this job` : ""}`}
            transform={`translate(${layout.skillX},${p.y})`}
            className={cn(
              "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
              ((lit !== null && !lit.has(sk.key)) || faded) && "opacity-30",
            )}
            onClick={() => onSelect(sk.key)} onKeyDown={onActivate(() => onSelect(sk.key))}
            onMouseEnter={() => onHover(sk.key)} onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(sk.key)} onBlur={() => onHover(null)}
          >
            <rect
              x={-p.r - 6} y={-11} width={p.r + 20 + nameW + 64} height={22} rx={4}
              className={cn(
                "stroke-transparent group-hover:fill-muted/60 group-focus-visible:stroke-ring group-focus-visible:stroke-2",
                selected === sk.key ? "fill-muted" : "fill-transparent",
              )}
            />
            {arcs.map((d, i) => (
              <path key={i} d={d} fill="none" strokeWidth={1.5} className="stroke-graph-claim/60" aria-hidden="true" />
            ))}
            <circle
              r={p.r}
              className={p.stat.cited ? "fill-graph-evidence" : "fill-background stroke-graph-claim [stroke-dasharray:2_2]"}
              aria-hidden="true"
            />
            {cls && (
              <circle
                r={p.r + 7} fill="none" strokeWidth={1.4} aria-hidden="true"
                className={cn(
                  cls.endsWith("claimed") ? "stroke-graph-gap" : "stroke-foreground/70",
                  cls.startsWith("preferred") && "[stroke-dasharray:3_2.5]",
                )}
              />
            )}
            <text x={lx} y={4} className="fill-foreground text-[12.5px] stroke-background stroke-[3px] [paint-order:stroke]">
              <tspan fontWeight={bold ? 600 : 400}>{sk.name}</tspan>
              <tspan dx={6} className="fill-muted-foreground font-mono text-[10.5px] stroke-0">{label}</tspan>
            </text>
            {sk.proficiency !== null && <Pips value={sk.proficiency} x={lx + nameW + 10 + (p.stat.githubOnly ? 76 : 14)} />}
          </g>
        );
      })}
      {!area.collapsed && area.chips.map((c) => {
        const sk = c.skill;
        const cls = overlay?.classBySkill.get(sk.key);
        const faded = overlay !== null && !cls;
        const match = !!matches && matches.has(sk.key);
        return (
          <g
            key={sk.key} role="button" tabIndex={0} aria-pressed={selected === sk.key}
            aria-label={`Skill: ${sk.name}, no evidence${cls ? `, ${cls.replace("-", " ")} for this job` : ""}`}
            transform={`translate(${layout.skillX - 8 + c.x},${c.y})`}
            className={cn(
              "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
              ((lit !== null && !lit.has(sk.key)) || faded) && "opacity-30",
            )}
            onClick={() => onSelect(sk.key)} onKeyDown={onActivate(() => onSelect(sk.key))}
            onMouseEnter={() => onHover(sk.key)} onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(sk.key)} onBlur={() => onHover(null)}
          >
            <rect
              x={0} y={-10} width={c.w} height={20} rx={10} strokeWidth={1}
              className={cn(
                "group-hover:fill-muted group-focus-visible:stroke-ring group-focus-visible:stroke-2",
                selected === sk.key ? "fill-muted stroke-foreground" : "fill-background",
                selected !== sk.key && (cls?.endsWith("claimed") ? "stroke-graph-gap" : "stroke-graph-claim [stroke-dasharray:2_2.5]"),
              )}
            />
            <text x={9} y={4} className={cn("fill-foreground text-[12px]", match && "font-semibold", cls?.endsWith("claimed") && "fill-graph-gap")}>
              {sk.name}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function GhostBlock({ layout, ghosts, selected, onSelect }: {
  layout: MapLayout; ghosts: NonNullable<ReturnType<typeof ghostBlock>>; selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <g>
      <text x={layout.skillX - 14} y={ghosts.top + 18} className="fill-graph-gap font-mono text-[11px] uppercase tracking-wider">
        Missing for this job · {ghosts.chips.length}
      </text>
      {ghosts.chips.map((c) => {
        const key = `ghost:${c.skill.name}`;
        const label = `${c.skill.name} · ${c.skill.area}`;
        return (
          <g
            key={key} role="button" tabIndex={0} aria-pressed={selected === key}
            aria-label={`Missing required skill: ${c.skill.name}, ${c.skill.area}`}
            transform={`translate(${layout.skillX - 8 + c.x},${c.y})`}
            className="group cursor-pointer outline-none"
            onClick={() => onSelect(key)} onKeyDown={onActivate(() => onSelect(key))}
          >
            <rect
              x={0} y={-10} width={c.w} height={20} rx={10} strokeWidth={1}
              className={cn(
                "stroke-graph-gap group-hover:fill-muted group-focus-visible:stroke-2",
                selected === key ? "fill-muted" : "fill-background",
              )}
            />
            <text x={9} y={4} className="fill-graph-gap text-[12px] italic">{label}</text>
          </g>
        );
      })}
    </g>
  );
}

