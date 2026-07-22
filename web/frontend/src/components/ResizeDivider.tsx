import { useCallback, useState } from "react";
import { cn } from "../lib/utils";

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

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      title="Drag to resize · double-click to reset"
      onMouseDown={handleMouseDown}
      onDoubleClick={onReset}
      className={cn(
        "group flex w-[7px] flex-none cursor-col-resize items-center justify-center self-stretch transition-colors",
        active ? "bg-primary" : "hover:bg-primary",
      )}
    >
      <span
        className={cn(
          "h-10 max-h-[60%] w-px rounded-full transition-colors",
          active ? "bg-primary" : "bg-border group-hover:bg-primary",
        )}
      />
    </div>
  );
}
