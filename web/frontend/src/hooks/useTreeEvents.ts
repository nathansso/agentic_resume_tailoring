import { useEffect, useRef } from "react";
import { parseTreeEvent, type TreeEvent } from "../lib/treeEvents";

/** Subscribe to a job's tailoring-tree change feed over SSE (issue #204).
 *
 *  The server starts a fresh stream at the newest event, and `EventSource`
 *  reconnects on its own with `Last-Event-ID`, so nothing is replayed and
 *  nothing is missed across a dropped connection. The callback is held in a
 *  ref so a re-render never reopens the stream. */
export function useTreeEvents(
  jobId: string | null,
  onEvent: (event: TreeEvent) => void,
  enabled = true,
): void {
  const callback = useRef(onEvent);
  callback.current = onEvent;

  useEffect(() => {
    if (!jobId || !enabled || typeof EventSource === "undefined") return;
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    const handle = (message: MessageEvent<string>) => {
      const event = parseTreeEvent(message.data);
      if (event) callback.current(event);
    };
    source.addEventListener("tree", handle as EventListener);
    return () => {
      source.removeEventListener("tree", handle as EventListener);
      source.close();
    };
  }, [jobId, enabled]);
}
