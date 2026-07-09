import { useRef, useState, type CSSProperties, type MouseEvent, type PointerEvent } from "react";
import { colors } from "../theme";
import {
  slotToIndex,
  targetIndexForPointer,
  texLineForPointer,
  type BulletRegion,
  type OverlayModel,
  type SectionRegion,
} from "../lib/pdfOverlay";

interface Props {
  model: OverlayModel;
  /** CSS pixel size of the page-1 canvas this overlay covers. */
  width: number;
  height: number;
  /** False while the preview is stale or compiling — bands stay visible but inert. */
  enabled: boolean;
  onMoveSection: (key: string, targetIndex: number) => void;
  onMoveBullet: (groupIndex: number, fromIdx: number, toIdx: number) => void;
  /** Double-click anywhere mapped → jump the source editor to this tex line. */
  onJumpToLine: (texLine: number) => void;
}

interface Drag {
  kind: "section" | "bullet";
  /** Position of the dragged band within its band list. */
  fromPos: number;
  groupIndex: number; // sections: -1
  startY: number;
  active: boolean;
  slot: number;
  indicatorY: number | null;
}

const DRAG_THRESHOLD_PX = 4;
const SECTION_HANDLE_W = 16;

/** Transparent drag bands over the rendered page: whole sections reorder
 *  against each other (grab the heading line or the left-edge handle), bullets
 *  reorder within their own group. Double-click jumps the source editor to the
 *  matching tex line. Hand-rolled pointer drag — the bands are fixed,
 *  PDF-derived geometry, so a sortable library has nothing to manage. */
export function PdfDragOverlay({ model, width, height, enabled, onMoveSection, onMoveBullet, onJumpToLine }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  function bandsFor(d: Pick<Drag, "kind" | "groupIndex">): { top: number; height: number }[] {
    return d.kind === "section"
      ? model.sections
      : model.bulletGroups.find(g => g.groupIndex === d.groupIndex)?.bullets ?? [];
  }

  function localY(e: PointerEvent): number {
    const rect = rootRef.current?.getBoundingClientRect();
    return rect ? e.clientY - rect.top : e.clientY;
  }

  function beginDrag(e: PointerEvent, kind: Drag["kind"], fromPos: number, groupIndex: number) {
    if (!enabled || e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ kind, fromPos, groupIndex, startY: localY(e), active: false, slot: fromPos, indicatorY: null });
  }

  function updateDrag(e: PointerEvent) {
    if (!drag) return;
    const y = localY(e);
    if (!drag.active && Math.abs(y - drag.startY) < DRAG_THRESHOLD_PX) return;
    const bands = bandsFor(drag);
    const slot = targetIndexForPointer(y, bands);
    const indicatorY =
      slot === 0 ? bands[0].top : bands[slot - 1].top + bands[slot - 1].height;
    setDrag({ ...drag, active: true, slot, indicatorY });
  }

  function endDrag() {
    if (!drag) return;
    const { kind, fromPos, groupIndex, slot, active } = drag;
    setDrag(null);
    if (!active) return;
    const toPos = slotToIndex(slot, fromPos);
    if (toPos === fromPos) return;
    if (kind === "section") {
      // Map the band position back to a document-order movable index — the
      // model can omit unmatched sections, so use the target band's index.
      const moved = model.sections[fromPos];
      const target = model.sections[toPos];
      if (moved && target) onMoveSection(moved.key, target.index);
    } else {
      onMoveBullet(groupIndex, fromPos, toPos);
    }
  }

  const dragging = drag?.active ?? false;

  function handleDoubleClick(e: MouseEvent<HTMLDivElement>) {
    if (!enabled) return;
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const line = texLineForPointer(e.clientY - rect.top, model);
    if (line !== null) onJumpToLine(line);
  }

  function sectionStyle(sec: SectionRegion, pos: number): CSSProperties {
    const isDragged = dragging && drag!.kind === "section" && drag!.fromPos === pos;
    const isHover = hover === `s${pos}` && enabled && !dragging;
    return {
      position: "absolute",
      top: sec.top,
      height: sec.height,
      left: 0,
      width: SECTION_HANDLE_W,
      cursor: enabled ? (isDragged ? "grabbing" : "grab") : "default",
      background: isDragged || isHover ? "rgba(63,185,80,0.25)" : "rgba(63,185,80,0.12)",
      borderRight: `2px solid ${isDragged || isHover ? colors.accent : "rgba(63,185,80,0.35)"}`,
      pointerEvents: enabled ? "auto" : "none",
      touchAction: "none",
    };
  }

  /** Full-width grab band over the section's rendered heading line — the
   *  affordance users actually reach for (the edge strip alone was missed). */
  function headingStyle(sec: SectionRegion, pos: number): CSSProperties {
    const isDragged = dragging && drag!.kind === "section" && drag!.fromPos === pos;
    const isHover = hover === `s${pos}` && enabled && !dragging;
    return {
      position: "absolute",
      top: sec.top,
      height: Math.max(0, sec.headingBottom - sec.top) + 4,
      left: SECTION_HANDLE_W,
      right: 0,
      cursor: enabled ? (isDragged ? "grabbing" : "grab") : "default",
      background: isDragged || isHover ? "rgba(63,185,80,0.2)" : "transparent",
      pointerEvents: enabled ? "auto" : "none",
      touchAction: "none",
    };
  }

  function bulletStyle(b: BulletRegion, pos: number, groupIndex: number): CSSProperties {
    const isDragged =
      dragging && drag!.kind === "bullet" && drag!.groupIndex === groupIndex && drag!.fromPos === pos;
    const isHover = hover === `b${groupIndex}:${pos}` && enabled && !dragging;
    return {
      position: "absolute",
      top: b.top,
      height: b.height,
      left: SECTION_HANDLE_W + 4,
      right: 0,
      cursor: enabled ? (isDragged ? "grabbing" : "grab") : "default",
      background: isDragged ? "rgba(63,185,80,0.2)" : isHover ? "rgba(63,185,80,0.12)" : "transparent",
      pointerEvents: enabled ? "auto" : "none",
      touchAction: "none",
    };
  }

  const indicatorBands = dragging ? bandsFor(drag!) : [];

  return (
    <div ref={rootRef} style={{ ...s.root, width, height }} onDoubleClick={handleDoubleClick}>
      {model.sections.map((sec, pos) => (
        <div
          key={`s${sec.key}`}
          style={sectionStyle(sec, pos)}
          title={enabled ? "Drag to reorder this section" : undefined}
          onPointerDown={e => beginDrag(e, "section", pos, -1)}
          onPointerMove={updateDrag}
          onPointerUp={endDrag}
          onPointerCancel={() => setDrag(null)}
          onPointerEnter={() => setHover(`s${pos}`)}
          onPointerLeave={() => setHover(h => (h === `s${pos}` ? null : h))}
        />
      ))}
      {model.sections.map((sec, pos) => (
        <div
          key={`h${sec.key}`}
          style={headingStyle(sec, pos)}
          title={enabled ? "Drag to reorder this section (double-click to jump to source)" : undefined}
          onPointerDown={e => beginDrag(e, "section", pos, -1)}
          onPointerMove={updateDrag}
          onPointerUp={endDrag}
          onPointerCancel={() => setDrag(null)}
          onPointerEnter={() => setHover(`s${pos}`)}
          onPointerLeave={() => setHover(h => (h === `s${pos}` ? null : h))}
        />
      ))}
      {model.bulletGroups.map(g =>
        g.bullets.map((b, pos) => (
          <div
            key={`b${g.groupIndex}:${pos}`}
            style={bulletStyle(b, pos, g.groupIndex)}
            title={enabled ? "Drag to reorder this bullet (double-click to jump to source)" : undefined}
            onPointerDown={e => beginDrag(e, "bullet", pos, g.groupIndex)}
            onPointerMove={updateDrag}
            onPointerUp={endDrag}
            onPointerCancel={() => setDrag(null)}
            onPointerEnter={() => setHover(`b${g.groupIndex}:${pos}`)}
            onPointerLeave={() => setHover(h => (h === `b${g.groupIndex}:${pos}` ? null : h))}
          />
        )),
      )}
      {dragging && drag!.indicatorY !== null && indicatorBands.length > 0 && (
        <div
          style={{
            ...s.indicator,
            top: drag!.indicatorY - 1,
            left: drag!.kind === "bullet" ? SECTION_HANDLE_W + 4 : 0,
          }}
        />
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  root: {
    position: "absolute",
    top: 0,
    left: 0,
    zIndex: 2,
  },
  indicator: {
    position: "absolute",
    right: 0,
    height: 2,
    background: colors.accent,
    pointerEvents: "none",
  },
};
