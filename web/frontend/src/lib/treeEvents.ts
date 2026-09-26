/** The tailoring-tree change feed as the editor sees it (issue #204).
 *
 *  `GET /api/jobs/{id}/events` streams one `tree` event per commit or checkout.
 *  The editor's own saves come back as `editor` commits and must not reload the
 *  buffer the user is typing in; everything else (a host run, a pipeline
 *  re-tailor, a checkout or revert) is a new version from outside. */

export interface TreeEvent {
  event_id: number;
  kind: string;
  node_id: string;
  source: string | null;
}

/** What to do with an event: nothing, reload the editor, or ask first because
 *  the buffer holds keystrokes that have not been saved yet. */
export type ReloadAction = "ignore" | "reload" | "prompt";

export function reloadAction(event: TreeEvent, dirty: boolean): ReloadAction {
  if (event.kind === "commit" && event.source === "editor") return "ignore";
  return dirty ? "prompt" : "reload";
}

/** Parse one SSE `data:` payload; null for anything malformed. */
export function parseTreeEvent(data: string): TreeEvent | null {
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const e = raw as Record<string, unknown>;
  if (typeof e.event_id !== "number" || typeof e.kind !== "string" || typeof e.node_id !== "string") {
    return null;
  }
  return {
    event_id: e.event_id,
    kind: e.kind,
    node_id: e.node_id,
    source: typeof e.source === "string" ? e.source : null,
  };
}
