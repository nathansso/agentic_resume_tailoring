import { useCallback, useState, type CSSProperties } from "react";
import { colors } from "../theme";

interface Props {
  /** Called on each pointer move with the absolute `clientX`; the parent
   *  converts it to a width/fraction relative to its own container rect. */
  onDrag: (clientX: number) => void;
  /** Persist the final value once the drag settles. */
  onDragEnd?: () => void;
  /** Reset to the default layout (auto width / 50-50 split). */
  onReset?: () => void;
  ariaLabel: string;
}

/** A thin vertical grab handle for resizing side-by-side panes. Owns the
 *  document-level drag lifecycle so panes only supply the geometry (#90). */
export function ResizeDivider({ onDrag, onDragEnd, onReset, ariaLabel }: Props) {
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setActive(true);
      const move = (ev: MouseEvent) => onDrag(ev.clientX);
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
        setActive(false);
        onDragEnd?.();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
      // Suppress text selection + keep the resize cursor across the whole drag.
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    },
    [onDrag, onDragEnd],
  );

  const lit = active || hover;

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      title="Drag to resize · double-click to reset"
      onMouseDown={handleMouseDown}
      onDoubleClick={onReset}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ ...divider, background: lit ? colors.accent : "transparent" }}
    >
      <span style={{ ...grip, background: lit ? colors.accent : colors.primary }} />
    </div>
  );
}

const divider: CSSProperties = {
  flex: "0 0 auto",
  width: 7,
  cursor: "col-resize",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  alignSelf: "stretch",
  transition: "background 0.12s ease",
};

const grip: CSSProperties = {
  width: 1,
  height: "2.5rem",
  maxHeight: "60%",
  borderRadius: 1,
  transition: "background 0.12s ease",
};
