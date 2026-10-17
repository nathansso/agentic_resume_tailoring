import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import type { EvidenceItem, EvidenceKind } from "../types";
import {
  DISMISSED_KEY, isPanelKey, jobOverlay, needsText, neighborhood, searchSkills, areaFoldKey, type JobOverlay,
} from "../lib/evidenceMap";
import {
  foldAllBut, layoutAreaMap, placeSkillNames, skillPositions, type AreaMapLayout, type PackedSkill, type PlacedArea,
  type PlacedItem,
} from "../lib/areaMap";
import { cn } from "../lib/utils";
import { useEvidenceData } from "../hooks/useEvidenceData";
import { useJobCompare } from "../hooks/useJobCompare";
import { EvidencePanel } from "./EvidencePanel";
import { Count, Glyph, JobStatus, JobToggle, KIND_LABEL, onActivate } from "./graphParts";

/*
 * The area map (issue #320): the whole knowledge graph, clustered by skill area. Areas sit on
 * a ring (neighbours share the most evidence), each skill is a circle packed inside its area,
 * and each experience or project sits where the areas its bullets cite pull it. This file
 * draws what `lib/areaMap.ts` lays out and owns selection, hover, search and the job toggle;
 * none of them moves a node. Geometry (positions, radii, the view box) comes from that layout
 * at runtime, so it is set as SVG attributes; colour and type are Tailwind utilities over the
 * `graph-*` tokens in index.css.
 */

/** Never drawn smaller than this share of its natural size; narrower than that, the map scrolls. */
const MIN_SCALE = 0.78;
const MAX_SCALE = 1.35;

/** A key that names something on the map: an item, a skill or an area (not a panel such as the dismissed list). */
const isNode = (key: string): boolean => key.startsWith("area:") || !isPanelKey(key);

export function AreaMap({ defaultJobId = null, onOpenArea, onChanged }: {
  defaultJobId?: string | null;
  /** Open the evidence map with every other area folded: the set of fold keys to start from. */
  onOpenArea: (folded: ReadonlySet<string>) => void;
  onChanged?: () => void;
}) {
  const { view, dismissed, contexts, error, reload } = useEvidenceData("Failed to load the area map");
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [avail, setAvail] = useState(900);
  const job = useJobCompare(defaultJobId);
  const { jobOn, jobSkills } = job;
  const stage = useRef<HTMLDivElement>(null);
  const loaded = view !== null;

  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setAvail(Math.floor(entry.contentRect.width) - 32));
    ro.observe(el);
    return () => ro.disconnect();
  }, [loaded]);

  const layout = useMemo(() => (view ? layoutAreaMap(view) : null), [view]);
  const skillAt = useMemo(() => (layout ? skillPositions(layout) : null), [layout]);
  const overlay = useMemo(
    () => (view && jobOn && jobSkills && jobSkills.length > 0 ? jobOverlay(view, jobSkills) : null),
    [view, jobOn, jobSkills],
  );
  const matches = useMemo(() => (view ? searchSkills(view, query) : null), [view, query]);

  const known = useMemo(() => new Set([
    ...(view?.items ?? []).map((i) => i.key), ...(view?.skills ?? []).map((s) => s.key),
  ]), [view]);
  const current = selected && (
    known.has(selected) || selected.startsWith("ghost:") || (selected === DISMISSED_KEY && dismissed.length > 0)
  ) ? selected : null;
  const active = hover ?? current;

  // What stays lit: the hovered or selected node and what it links; else the search hits and theirs.
  const lit = useMemo(() => {
    if (!view) return null;
    if (active && isNode(active)) return neighborhood(view, active);
    if (!active && matches) {
      const out = new Set<string>();
      matches.forEach((k) => neighborhood(view, k).forEach((n) => out.add(n)));
      return out;
    }
    return null;
  }, [view, active, matches]);

  // Skill names are written only for what the pointer or the search is on, never all at once.
  const names = useMemo(() => {
    if (!view || !layout || !skillAt) return [];
    const keys = new Set<string>();
    if (active && isNode(active)) {
      if (skillAt.has(active)) keys.add(active);
      else (lit ?? new Set<string>()).forEach((k) => { if (skillAt.has(k)) keys.add(k); });
    } else if (!active && matches) {
      matches.forEach((k) => keys.add(k));
    }
    return placeSkillNames(layout, keys).filter((n) => n.key === active || n.crowded < 0.2);
  }, [view, layout, skillAt, active, lit, matches]);

  if (error) return <p className="p-4 text-sm text-destructive">{error}</p>;
  if (!view || !layout) return <p className="p-4 text-sm text-muted-foreground">Loading…</p>;
  if (layout.areas.length === 0) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        Nothing to map yet. Ingest a resume or GitHub profile and the map will group your skills by area and
        show the experiences and projects behind each.
      </p>
    );
  }

  const { viewBox: vb } = layout;
  const scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, avail / vb.w));
  const select = (key: string) => setSelected((cur) => (cur === key ? null : key));
  const skills = layout.areas.reduce((n, a) => n + a.total, 0);
  const evidenced = layout.areas.reduce((n, a) => n + a.evidenced, 0);

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="shrink-0 space-y-2 px-4 pb-1 pt-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px] text-muted-foreground" aria-label="Summary">
            <Count n={layout.areas.length} label="areas" />
            <Count n={skills} label="skills" />
            <Count n={evidenced} label="cited by a bullet" tone="proven" />
            <Count n={layout.items.length} label="placed" />
            {layout.loose.length > 0 && <Count n={layout.loose.length} label="not placed" tone="gap" />}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="relative inline-flex">
              <span className="sr-only">Find a skill</span>
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <input
                type="search" value={query} placeholder="Find a skill"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const first = layout.areas.flatMap((a) => a.skills).find((s) => matches?.has(s.skill.key));
                    if (first) setSelected(first.skill.key);
                  }
                  if (e.key === "Escape") setQuery("");
                }}
                className="w-48 rounded-md border border-border bg-background py-1.5 pl-8 pr-2 text-[13px] placeholder:text-muted-foreground"
              />
            </label>
            <JobToggle job={job} />
          </div>
          <Legend />
          <JobStatus job={job} overlay={overlay} />
        </div>
        <div ref={stage} className="min-h-0 flex-1 overflow-auto px-4 pb-4">
          <svg
            role="group" aria-label="Area map: skills grouped by area on a ring, with the experiences and projects that cite them between"
            viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`} width={Math.round(vb.w * scale)} height={Math.round(vb.h * scale)}
            className="mx-auto block max-w-none select-none font-sans"
          >
            <Edges layout={layout} active={active} lit={lit} overlay={overlay} matches={matches} />
            {layout.areas.map((a) => (
              <AreaGroup
                key={a.key} area={a} lit={lit} overlay={overlay}
                onOpen={() => onOpenArea(foldAllBut(view, a.key))} onHover={setHover}
              />
            ))}
            <g>
              {layout.areas.flatMap((a) => a.skills).map((s) => (
                <SkillDot
                  key={s.skill.key} s={s} lit={lit} overlay={overlay} match={!!matches && matches.has(s.skill.key)}
                  selected={current === s.skill.key} onSelect={select} onHover={setHover}
                />
              ))}
            </g>
            <g>
              {layout.items.map((it) => (
                <ItemMark
                  key={it.item.key} it={it} lit={lit} selected={current === it.item.key}
                  onSelect={select} onHover={setHover}
                />
              ))}
            </g>
            <g aria-hidden="true" className="pointer-events-none">
              {names.map((n) => (
                <text
                  key={n.key} x={n.x} y={n.y} textAnchor={n.anchor}
                  className="fill-foreground stroke-background stroke-[3px] text-[11.5px] font-medium [paint-order:stroke]"
                >
                  {n.text}
                </text>
              ))}
            </g>
          </svg>
          {overlay && overlay.missing.length > 0 && (
            <div className="mx-auto mt-3 max-w-3xl">
              <p className="mb-1.5 font-mono text-[11px] uppercase tracking-wider text-graph-gap">
                Missing for this job · {overlay.missing.length}
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {overlay.missing.map((m) => {
                  const key = `ghost:${m.name}`;
                  return (
                    <li key={key}>
                      <button
                        type="button" aria-pressed={current === key} onClick={() => select(key)}
                        className={cn(
                          "rounded-full border border-graph-gap px-2.5 py-0.5 text-[12px] italic text-graph-gap transition-colors hover:bg-muted",
                          current === key && "bg-muted",
                        )}
                      >
                        {m.name} · {m.area}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {layout.loose.length > 0 && (
            <LooseItems items={layout.loose} selected={current} onSelect={select} />
          )}
        </div>
      </div>
      <EvidencePanel
        view={view} selected={current} overlay={overlay} jobOn={jobOn} dismissed={dismissed} contexts={contexts}
        onSelect={setSelected} onSaved={async () => { await reload(); onChanged?.(); }}
      />
    </div>
  );
}

// ── legend and the unplaced list ───────────────────────────────────────────────

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted-foreground" aria-label="Legend">
      {(["exp", "proj"] as EvidenceKind[]).map((k) => (
        <li key={k} className="inline-flex items-center gap-1.5">
          <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true"><Glyph kind={k} size={4.6} /></svg>
          {KIND_LABEL[k]}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true"><circle r={4} className="fill-graph-evidence" /></svg>
        Skill cited by a bullet, sized by how many
      </li>
      <li className="inline-flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true">
          <circle r={4} className="fill-background stroke-graph-claim [stroke-dasharray:2_2]" />
        </svg>
        No bullet cites it
      </li>
      <li>Click an area to open it in the evidence map</li>
    </ul>
  );
}

/** Experiences and projects nothing links: they have no place on the map, so they are listed. */
function LooseItems({ items, selected, onSelect }: {
  items: EvidenceItem[]; selected: string | null; onSelect: (key: string) => void;
}) {
  return (
    <div className="mx-auto mt-4 max-w-3xl">
      <p className="mb-1.5 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        Not placed · {items.length}
        <span className="ml-2 normal-case tracking-normal">no skill is linked to these, so nothing places them</span>
      </p>
      <ul className="flex flex-wrap gap-1.5">
        {items.map((i) => (
          <li key={i.key}>
            <button
              type="button" aria-pressed={selected === i.key} onClick={() => onSelect(i.key)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[12px] transition-colors hover:bg-muted",
                selected === i.key && "bg-muted",
              )}
            >
              <svg width="12" height="12" viewBox="-7 -7 14 14" aria-hidden="true"><Glyph kind={i.kind} size={4.6} /></svg>
              {i.title}
              <span className="text-muted-foreground">{needsText(i) ? "needs a description" : "names no known skill"}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── edges ──────────────────────────────────────────────────────────────────────

function Edges({ layout, active, lit, overlay, matches }: {
  layout: AreaMapLayout; active: string | null; lit: ReadonlySet<string> | null; overlay: JobOverlay | null;
  matches: ReadonlySet<string> | null;
}) {
  return (
    <g fill="none" aria-hidden="true" className="pointer-events-none">
      {layout.edges.map((e) => {
        const hot = active
          ? e.itemKey === active || e.skillKey === active || areaFoldKey(e.areaKey) === active
          : !!matches && matches.has(e.skillKey);
        const jobFade = overlay !== null && !overlay.classBySkill.has(e.skillKey);
        return (
          <path
            key={e.id} d={e.d} strokeWidth={e.width}
            className={cn(
              "transition-[opacity,stroke] duration-150 motion-reduce:transition-none",
              hot ? (e.dashed ? "stroke-foreground/60" : "stroke-graph-evidence/90") : "stroke-graph-claim/20",
              e.dashed && "[stroke-dasharray:4_3]",
              lit && !hot && "opacity-40",
              jobFade && !hot && "opacity-30",
            )}
          />
        );
      })}
    </g>
  );
}

// ── areas ──────────────────────────────────────────────────────────────────────

function AreaGroup({ area, lit, overlay, onOpen, onHover }: {
  area: PlacedArea; lit: ReadonlySet<string> | null; overlay: JobOverlay | null;
  onOpen: () => void; onHover: (key: string | null) => void;
}) {
  const hubKey = areaFoldKey(area.key);
  const req = overlay?.areaRequired.get(area.key);
  const litHere = lit !== null && (lit.has(hubKey) || area.skills.some((s) => lit.has(s.skill.key)));
  const l = area.label;
  const full = area.total > 0 && area.evidenced === area.total;
  return (
    <g
      role="button" tabIndex={0}
      aria-label={`${area.key}, ${area.evidenced} of ${area.total} skills evidenced${req ? `, ${req.evidenced} of ${req.total} required` : ""}. Open in the evidence map`}
      className={cn(
        "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
        lit !== null && !litHere && "opacity-40",
      )}
      onClick={onOpen} onKeyDown={onActivate(onOpen)}
      onMouseEnter={() => onHover(hubKey)} onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(hubKey)} onBlur={() => onHover(null)}
    >
      <title>{`Open ${area.key} in the evidence map`}</title>
      <circle
        cx={area.x} cy={area.y} r={area.r}
        className={cn(
          "fill-muted/40 stroke-border transition-colors duration-150 group-hover:fill-muted/70 group-focus-visible:stroke-ring group-focus-visible:stroke-2 motion-reduce:transition-none",
          litHere && lit?.has(hubKey) && "stroke-foreground/30",
        )}
      />
      <text x={l.x} y={l.y} textAnchor={l.anchor}>
        <tspan className="fill-muted-foreground font-mono text-[11px] uppercase tracking-wider group-hover:fill-foreground">
          {l.title}
        </tspan>
        <tspan x={l.x} dy={15} className={cn("text-[12px]", full ? "fill-accent" : "fill-foreground")}>
          {l.count}
          {req && (
            <tspan className={req.evidenced === req.total ? "fill-accent" : "fill-graph-gap"}> · {req.evidenced}/{req.total} required</tspan>
          )}
        </tspan>
        {l.top && (
          <tspan x={l.x} dy={15} className="fill-muted-foreground text-[11.5px]">{l.top}</tspan>
        )}
      </text>
    </g>
  );
}

// ── skills ─────────────────────────────────────────────────────────────────────

function SkillDot({ s, lit, overlay, match, selected, onSelect, onHover }: {
  s: PackedSkill; lit: ReadonlySet<string> | null; overlay: JobOverlay | null; match: boolean; selected: boolean;
  onSelect: (key: string) => void; onHover: (key: string | null) => void;
}) {
  const sk = s.skill;
  const cls = overlay?.classBySkill.get(sk.key);
  const faded = overlay !== null && !cls;
  const dim = (lit !== null && !lit.has(sk.key)) || faded;
  return (
    <g
      role="button" tabIndex={0} aria-pressed={selected}
      aria-label={`Skill: ${sk.name}, ${s.stat.cited ? `${s.stat.bullets} citing bullet${s.stat.bullets === 1 ? "" : "s"}` : "no citing bullet"}, ${sk.area}${cls ? `, ${cls.replace("-", " ")} for this job` : ""}`}
      transform={`translate(${s.x},${s.y})`}
      className={cn("group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none", dim && "opacity-30")}
      onClick={() => onSelect(sk.key)} onKeyDown={onActivate(() => onSelect(sk.key))}
      onMouseEnter={() => onHover(sk.key)} onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(sk.key)} onBlur={() => onHover(null)}
    >
      <circle
        r={s.r}
        className={s.stat.cited ? "fill-graph-evidence" : "fill-background stroke-graph-claim [stroke-dasharray:2_2]"}
      />
      {(cls || match || selected) && (
        <circle
          r={s.r + 3.5} fill="none" strokeWidth={1.4} aria-hidden="true"
          className={cn(
            cls?.endsWith("claimed") ? "stroke-graph-gap" : "stroke-foreground/70",
            cls?.startsWith("preferred") && "[stroke-dasharray:3_2.5]",
            !cls && "stroke-foreground",
          )}
        />
      )}
      <circle r={s.r + 5} className="fill-transparent stroke-transparent group-focus-visible:stroke-ring group-focus-visible:stroke-2" />
    </g>
  );
}

// ── experiences and projects ───────────────────────────────────────────────────

function ItemMark({ it, lit, selected, onSelect, onHover }: {
  it: PlacedItem; lit: ReadonlySet<string> | null; selected: boolean;
  onSelect: (key: string) => void; onHover: (key: string | null) => void;
}) {
  const { item, label } = it;
  const w = label.box.x1 - label.box.x0;
  return (
    <g
      role="button" tabIndex={0} aria-pressed={selected}
      aria-label={`${KIND_LABEL[item.kind]}: ${item.title}, cites ${it.areas.length} area${it.areas.length === 1 ? "" : "s"}`}
      transform={`translate(${it.x},${it.y})`}
      className={cn(
        "group cursor-pointer outline-none transition-opacity duration-150 motion-reduce:transition-none",
        lit !== null && !lit.has(item.key) && "opacity-30",
      )}
      onClick={() => onSelect(item.key)} onKeyDown={onActivate(() => onSelect(item.key))}
      onMouseEnter={() => onHover(item.key)} onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(item.key)} onBlur={() => onHover(null)}
    >
      <rect
        x={label.box.x0 - it.x} y={label.box.y0 - it.y} width={w} height={label.box.y1 - label.box.y0}
        className="fill-transparent"
      />
      <circle
        r={11}
        className={cn(
          "fill-background/85 stroke-transparent group-focus-visible:stroke-ring group-focus-visible:stroke-2",
          selected && "stroke-foreground/60",
        )}
      />
      <Glyph kind={item.kind} size={5} />
      <text
        x={label.x - it.x} y={label.y - it.y} textAnchor={label.anchor}
        className="fill-foreground stroke-background stroke-[3px] text-[12px] font-medium [paint-order:stroke]"
      >
        {label.text}
      </text>
    </g>
  );
}
