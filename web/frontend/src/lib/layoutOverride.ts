/** Translating an editor drag into a durable arrangement override (issue #118).
 *
 *  A drag on the preview rewrites the .tex buffer, which is how the reorder
 *  becomes visible immediately. That buffer is saved as `edited_tex`, and a
 *  re-tailor deliberately clears `edited_tex` because it encodes *content* —
 *  so before #118 the reorder died at the next tailor run.
 *
 *  These helpers read the arrangement back out of the post-drag buffer as
 *  structured intent, which is persisted separately and outranks the ranker.
 *  Pure functions over the tex string: no fetch, no React.
 */

import { movableSections, parseBulletGroups } from "./texStructure";

/** Section keys shared with the backend's `_section_order`. */
export type SectionKey = string;

export interface BulletGroupOverride {
  /** "experience" | "projects" — the sections with reorderable bullets. */
  section: string;
  /** The heading the backend identifies the item by: an experience's title or
   *  a project's name. */
  item: string;
  order: string[];
}

export interface LayoutOverride {
  section_order?: SectionKey[] | null;
  skills?: string[] | null;
  bullets?: BulletGroupOverride[] | null;
}

/** Section order as the buffer now reads it, in document order. */
export function sectionOrderFromTex(tex: string): SectionKey[] {
  return movableSections(tex).map(s => s.key);
}

/** The `groupIndex`-th bullet group as an override entry, or null when the
 *  group cannot be addressed structurally — an unparseable heading, or a
 *  section whose bullets the backend does not key by item. */
export function bulletGroupFromTex(
  tex: string,
  groupIndex: number,
): BulletGroupOverride | null {
  const group = parseBulletGroups(tex)[groupIndex];
  if (!group) return null;
  const section = group.sectionKey;
  if (section !== "experience" && section !== "projects") return null;
  if (!group.label || group.bullets.length === 0) return null;
  return {
    section,
    item: group.label,
    order: group.bullets.map(b => b.text),
  };
}

/** Merge one group into an existing override, replacing the entry for the same
 *  item rather than accumulating duplicates — a user dragging the same item
 *  twice must not end up with two conflicting entries for it. */
export function mergeBulletGroup(
  existing: BulletGroupOverride[] | null | undefined,
  group: BulletGroupOverride,
): BulletGroupOverride[] {
  const rest = (existing ?? []).filter(
    g => !(g.section === group.section && g.item === group.item),
  );
  return [...rest, group];
}
