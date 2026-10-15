import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  updateEducation, updateExperience, updateProject, updateProjectBlurb,
  type EducationEdit, type ExperienceEdit, type ProjectEdit,
} from "../api/profile";
import type { EvidenceItem, EvidenceKind, EvidenceSkill, EvidenceView } from "../types";
import {
  dateRange, editableFields, githubOnlyLinks, highlightSegments, initialDraft, linksByBullet, planSave,
  recentEditable, skillEvidence, textIsDescription, type Draft, type JobOverlay, type SaveOp,
} from "../lib/evidenceMap";
import { cn } from "../lib/utils";

/*
 * The side panel (issue #136): one shared selection. A skill shows what backs it, with
 * the citing bullets quoted and the words that name it marked; an item is an editor,
 * because that is where a skill's links are fixed. Skills are not edited here: they
 * follow from the text, and the map re-derives them from the saved text.
 */

const KIND_LABEL: Record<EvidenceKind, string> = { exp: "Experience", proj: "Project", edu: "Education" };
const KIND_DOT: Record<EvidenceKind, string> = {
  exp: "bg-graph-experience rounded-[2px]",
  proj: "bg-graph-project rotate-45 rounded-[2px] scale-90",
  edu: "bg-graph-education rounded-full",
};
const SOURCE_LABEL: Record<string, string> = {
  resume: "Resume", github: "GitHub", linkedin: "LinkedIn", manual: "Added by you", chat: "Chat", host: "Your agent",
};

const sectionHead = "mb-1.5 mt-5 font-mono text-[11px] uppercase tracking-wider text-muted-foreground";
const linkButton =
  "rounded-md border border-border bg-background px-2 py-0.5 text-[12px] font-medium text-foreground transition-colors hover:bg-muted";

interface Props {
  view: EvidenceView;
  selected: string | null;
  overlay: JobOverlay | null;
  jobOn: boolean;
  onSelect: (key: string | null) => void;
  /** Called after a save, so the map is re-read from the server. */
  onSaved: () => Promise<void>;
}

export function EvidencePanel({ view, selected, overlay, jobOn, onSelect, onSaved }: Props) {
  const item = selected ? view.items.find((i) => i.key === selected) : undefined;
  const skill = selected ? view.skills.find((s) => s.key === selected) : undefined;
  const ghost = selected?.startsWith("ghost:") ? overlay?.missing.find((m) => `ghost:${m.name}` === selected) : undefined;

  return (
    <aside
      aria-label="Details"
      className="max-h-[50vh] w-full shrink-0 overflow-y-auto border-t border-border bg-card p-4 lg:max-h-none lg:w-[24rem] lg:border-l lg:border-t-0"
    >
      {item ? (
        <ItemEditor key={item.key} view={view} item={item} onSelect={onSelect} onSaved={onSaved} />
      ) : skill ? (
        <SkillDetail view={view} skill={skill} overlay={overlay} jobOn={jobOn} onSelect={onSelect} />
      ) : ghost ? (
        <div>
          <Kicker><span className="h-2.5 w-2.5 rounded-full border border-dashed border-graph-gap" />Missing skill</Kicker>
          <h3 className="mt-1 text-lg font-semibold">{ghost.name}</h3>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            Required by this job · {ghost.area} · weight <span className="font-mono">{ghost.weight}</span>
          </p>
          <p className="mt-4 rounded-md border border-graph-gap/40 p-3 text-[13px]">
            <b className="font-semibold text-graph-gap">Not on your profile.</b> ART will not add it for you.
            If you have used it, say so in the experience or project where you did, and it will appear here.
          </p>
        </div>
      ) : (
        <div className="space-y-2 text-[13px] text-muted-foreground">
          <p className="text-sm font-semibold text-foreground">Select a skill or an item</p>
          <p>
            Skills follow from what you wrote. To change a link, edit the experience or project behind it
            and the map reads the new text.
          </p>
        </div>
      )}
    </aside>
  );
}

function Kicker({ children }: { children: ReactNode }) {
  return <p className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">{children}</p>;
}

function Callout({ children, warn }: { children: ReactNode; warn?: boolean }) {
  return (
    <div className={cn("mt-4 rounded-md border p-3 text-[13px]", warn ? "border-graph-gap/40" : "border-border bg-background")}>
      {children}
    </div>
  );
}

function Pips({ value }: { value: number }) {
  return (
    <span className="inline-flex gap-[2px]" role="img" aria-label={`Proficiency ${value} of 5`}>
      {[0, 1, 2, 3, 4].map((i) => (
        <span key={i} className={cn("h-1.5 w-[3px] rounded-[1px]", i < value ? "bg-muted-foreground" : "bg-muted-foreground/25")} />
      ))}
    </span>
  );
}

// ── a skill ────────────────────────────────────────────────────────────────────

function SkillDetail({ view, skill, overlay, jobOn, onSelect }: {
  view: EvidenceView; skill: EvidenceSkill; overlay: JobOverlay | null; jobOn: boolean;
  onSelect: (key: string) => void;
}) {
  const evidence = useMemo(() => skillEvidence(view, skill.key), [view, skill.key]);
  const bullets = evidence.reduce((n, e) => n + e.bullets.length, 0);
  const githubOnly = bullets === 0 && evidence.some((e) => e.edge.github);
  const cls = overlay?.classBySkill.get(skill.key);
  const sources = skill.sources.map((s) => SOURCE_LABEL[s] ?? s);
  return (
    <div>
      <Kicker>
        <span
          className={cn("h-2.5 w-2.5 rounded-full", bullets > 0 ? "bg-graph-evidence" : "border border-dashed border-graph-claim")}
        />
        Skill · {skill.area}
      </Kicker>
      <h3 className="mt-1 text-lg font-semibold">{skill.name}</h3>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        {bullets > 0
          ? `${bullets} bullet${bullets === 1 ? "" : "s"} in ${evidence.filter((e) => e.bullets.length).length} item${evidence.filter((e) => e.bullets.length).length === 1 ? "" : "s"}`
          : githubOnly ? "Linked from GitHub only" : "No evidence"}
        {skill.is_core ? " · pinned as core" : ""}
      </p>
      <dl className="mt-3 grid grid-cols-[6.5rem_1fr] items-center gap-y-1.5 text-[13px]">
        <dt className="text-muted-foreground">Proficiency</dt>
        <dd className="flex items-center gap-2">
          {skill.proficiency !== null ? <><Pips value={skill.proficiency} /><span className="font-mono text-[12px]">{skill.proficiency} of 5</span></> : "Not rated"}
        </dd>
        <dt className="text-muted-foreground">Sources</dt>
        <dd className="flex flex-wrap gap-1">
          {sources.length === 0 ? "None" : sources.map((s) => (
            <span key={s} className="rounded border border-border px-1.5 py-px text-[12px]">{s}</span>
          ))}
        </dd>
        {jobOn && cls && (
          <>
            <dt className="text-muted-foreground">This job</dt>
            <dd>
              {cls.startsWith("required") ? "Required" : "Preferred"}
              {overlay?.weightBySkill.has(skill.key) ? <>, weight <span className="font-mono">{overlay.weightBySkill.get(skill.key)}</span></> : null}
              {cls.endsWith("claimed") ? <span className="text-graph-gap"> · nothing cites it</span> : null}
            </dd>
          </>
        )}
      </dl>

      {bullets === 0 && !githubOnly && (
        <Callout warn={jobOn && !!cls}>
          <b className="font-semibold">Nothing you wrote mentions it.</b>{" "}
          {sources.length > 0 ? `${sources.join(" and ")} list${sources.length === 1 ? "s" : ""} it. ` : ""}
          To back it, mention it in the experience or project where you used it:
          <span className="mt-2 flex flex-wrap gap-1.5">
            {recentEditable(view).map((i) => (
              <button key={i.key} type="button" className={linkButton} onClick={() => onSelect(i.key)}>
                {i.kind === "exp" ? i.org || i.title : i.title}
              </button>
            ))}
          </span>
        </Callout>
      )}
      {githubOnly && (
        <Callout>
          <b className="font-semibold">Backed only by repo languages.</b> GitHub reports it, but no description
          says what you did with it. Add one to the project below.
          <span className="mt-2 flex flex-wrap gap-1.5">
            {evidence.map((e) => (
              <button key={e.item.key} type="button" className={linkButton} onClick={() => onSelect(e.item.key)}>
                {e.item.title}
              </button>
            ))}
          </span>
        </Callout>
      )}

      {evidence.some((e) => e.bullets.length > 0) && <h4 className={sectionHead}>Evidence</h4>}
      <div className="space-y-4">
        {evidence.filter((e) => e.bullets.length > 0).map(({ item, edge, bullets: cited }) => (
          <div key={item.key}>
            <button type="button" onClick={() => onSelect(item.key)} className="flex items-center gap-2 text-left text-[13px] font-medium hover:underline">
              <span className={cn("inline-block h-2 w-2 shrink-0", KIND_DOT[item.kind])} aria-hidden="true" />
              {item.title}
            </button>
            <p className="ml-4 text-[12px] text-muted-foreground">
              {[item.org, dateRange(item.start, item.end)].filter(Boolean).join(" · ")}
            </p>
            <ol className="ml-4 mt-1.5 space-y-1.5">
              {cited.map((b) => (
                <li key={b.index} className="text-[13px] leading-snug">
                  <span className="mr-1.5 font-mono text-[11px] text-muted-foreground">#b{b.index}</span>
                  {highlightSegments(b.text, b.marks).map((seg, i) => (
                    seg.hit
                      ? <mark key={i} className="rounded-sm bg-graph-evidence/20 px-px font-medium text-foreground">{seg.text}</mark>
                      : <span key={i}>{seg.text}</span>
                  ))}
                </li>
              ))}
            </ol>
            {edge.github && <p className="ml-4 mt-1 text-[12px] text-muted-foreground">Also linked from the repo's GitHub languages.</p>}
            <p className="ml-4 mt-1.5">
              <button type="button" className={linkButton} onClick={() => onSelect(item.key)}>
                Edit this {KIND_LABEL[item.kind].toLowerCase()}
              </button>
            </p>
          </div>
        ))}
      </div>
      <p className="mt-5 text-[12.5px] text-muted-foreground">
        Skills are not edited here. They follow from your experiences and projects.
      </p>
    </div>
  );
}

// ── an item: the editor ────────────────────────────────────────────────────────

/** Resize a textarea to its text. The height is runtime-computed from the content, so it is set inline. */
function autosize(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight + 2}px`;
}

const fieldClass =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-[13px] placeholder:text-muted-foreground";

async function runOp(op: SaveOp): Promise<void> {
  if (op.kind === "blurb") await updateProjectBlurb(op.projectId, op.blurbId, op.content);
  else if (op.kind === "experience") await updateExperience(op.id, op.body as ExperienceEdit);
  else if (op.kind === "education") await updateEducation(op.id, op.body as EducationEdit);
  else await updateProject(op.id, op.body as ProjectEdit);
}

function ItemEditor({ view, item, onSelect, onSaved }: {
  view: EvidenceView; item: EvidenceItem; onSelect: (key: string | null) => void; onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(item));
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  // A refetch after a save (or an agent's edit) replaces the item; start the draft from what is stored.
  const stored = JSON.stringify([item.fields, item.text]);
  useEffect(() => { setDraft(initialDraft(item)); }, [stored]);   // eslint-disable-line react-hooks/exhaustive-deps

  const plan = useMemo(() => planSave(item, draft), [item, draft]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initialDraft(item));
  const links = useMemo(() => linksByBullet(view, item.key), [view, item.key]);
  const githubOnly = useMemo(() => githubOnlyLinks(view, item.key), [view, item.key]);
  const parent = item.parent_key ? view.items.find((i) => i.key === item.parent_key) : undefined;
  const children = view.items.filter((i) => i.parent_key === item.key);
  const asDescription = textIsDescription(item);
  const fields = editableFields(item);
  const canAdd = item.kind === "exp";

  const save = async () => {
    if (plan.errors.length) { setErrors(plan.errors); return; }
    setSaving(true);
    setErrors([]);
    try {
      for (const op of plan.ops) await runOp(op);
      await onSaved();
      setSaved(true);
    } catch (e) {
      setErrors([e instanceof Error ? e.message : "Could not save"]);
    } finally {
      setSaving(false);
    }
  };

  const setText = (i: number, value: string) => {
    setSaved(false);
    setDraft((d) => ({ ...d, texts: d.texts.map((t, n) => (n === i ? value : t)) }));
  };

  return (
    <div>
      <Kicker>
        <span className={cn("inline-block h-2.5 w-2.5", KIND_DOT[item.kind])} aria-hidden="true" />
        {KIND_LABEL[item.kind]}
      </Kicker>
      <h3 className="mt-1 text-lg font-semibold">{item.title}</h3>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        {[item.org, dateRange(item.start, item.end)].filter(Boolean).join(" · ")}
      </p>

      {(parent || children.length > 0 || item.achievements.length > 0) && (
        <Callout>
          {parent && (
            <p>Part of{" "}
              <button type="button" className={linkButton} onClick={() => onSelect(parent.key)}>{parent.title}</button>
            </p>
          )}
          {children.length > 0 && (
            <p className="mt-1 flex flex-wrap items-center gap-1.5">Includes{" "}
              {children.map((c) => (
                <button key={c.key} type="button" className={linkButton} onClick={() => onSelect(c.key)}>{c.title}</button>
              ))}
            </p>
          )}
          {item.achievements.map((a) => (
            <p key={a.id} className="mt-1">★ {a.title}{a.issuer ? ` · ${a.issuer}` : ""}{a.date ? ` · ${a.date}` : ""}</p>
          ))}
        </Callout>
      )}

      {item.kind !== "edu" && item.text_source === "none" && (
        <Callout warn>
          <b className="font-semibold text-graph-gap">Needs a description.</b> ART cannot tell what you did
          here, so nothing links to it. A sentence on what you built and with what is enough.
        </Callout>
      )}

      <details className="mt-4 rounded-md border border-border bg-background" open={item.kind === "edu"}>
        <summary className="cursor-pointer px-3 py-1.5 text-[13px] font-medium">Details</summary>
        <div className="space-y-2.5 px-3 pb-3 pt-1">
          {fields.map((f) => (
            <label key={f.key} className="block text-[12px] text-muted-foreground">
              {f.label}
              {f.multiline ? (
                <textarea
                  ref={autosize} rows={2} value={draft.fields[f.key] ?? ""}
                  onChange={(e) => { autosize(e.target); setSaved(false); setDraft((d) => ({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })); }}
                  className={cn(fieldClass, "mt-1 resize-none overflow-hidden text-foreground")}
                />
              ) : (
                <input
                  type="text" value={draft.fields[f.key] ?? ""}
                  onChange={(e) => { setSaved(false); setDraft((d) => ({ ...d, fields: { ...d.fields, [f.key]: e.target.value } })); }}
                  className={cn(fieldClass, "mt-1 text-foreground")}
                />
              )}
            </label>
          ))}
        </div>
      </details>

      {item.kind !== "edu" && (
        <>
          <h4 className={sectionHead}>{asDescription ? "Description" : "Bullets"}</h4>
          <ul className="space-y-3">
            {draft.texts.map((value, i) => {
              const entry = item.text[i];
              const ordinal = entry ? entry.index : null;
              const linked = ordinal !== null ? links.get(ordinal) ?? [] : [];
              return (
                <li key={i} className="space-y-1.5">
                  <label className="block">
                    {!asDescription && (
                      <span className="mb-1 block font-mono text-[11px] text-muted-foreground">
                        {ordinal !== null ? `#b${ordinal}` : entry ? "empty" : "new"}
                      </span>
                    )}
                    <textarea
                      ref={autosize} rows={2} value={value}
                      aria-label={asDescription ? "Description" : `Bullet ${i + 1}`}
                      onChange={(e) => { autosize(e.target); setText(i, e.target.value); }}
                      placeholder={asDescription ? "What you built, and with what" : "What you did, with what, and what changed"}
                      className={cn(fieldClass, "resize-none overflow-hidden leading-snug")}
                    />
                  </label>
                  {entry && (
                    <p className="flex min-h-[1.25rem] flex-wrap items-center gap-1 text-[12px] text-muted-foreground">
                      {linked.length === 0
                        ? <span>Links no skills</span>
                        : <><span>Links</span>{linked.map((s) => (
                          <button
                            key={s.key} type="button" onClick={() => onSelect(s.key)}
                            className="rounded-full border border-graph-evidence/50 px-2 py-px text-[12px] text-foreground hover:bg-muted"
                          >
                            {s.name}
                          </button>
                        ))}</>}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {canAdd && (
            <button
              type="button" className={cn(linkButton, "mt-3")}
              onClick={() => { setSaved(false); setDraft((d) => ({ ...d, texts: [...d.texts, ""] })); }}
            >
              Add a bullet
            </button>
          )}
          {githubOnly.length > 0 && (
            <p className="mt-3 text-[12.5px] text-muted-foreground">
              GitHub languages also link {githubOnly.map((s) => s.name).join(", ")}; no bullet here names them.
            </p>
          )}
        </>
      )}

      {errors.length > 0 && (
        <ul role="alert" className="mt-3 space-y-0.5 text-[13px] text-destructive">
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
      <div className="mt-4 flex items-center gap-2">
        <button
          type="button" onClick={save} disabled={!dirty || saving || (plan.ops.length === 0 && plan.errors.length === 0)}
          className="rounded-md bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-foreground transition-opacity disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          type="button" disabled={!dirty || saving}
          onClick={() => { setDraft(initialDraft(item)); setErrors([]); }}
          className="rounded-md border border-border px-3 py-1.5 text-[13px] transition-colors hover:bg-muted disabled:opacity-40"
        >
          Discard
        </button>
        {saved && !dirty && <span role="status" className="text-[12.5px] text-accent">Saved. The map has been re-read.</span>}
      </div>
    </div>
  );
}
