import { describe, expect, it } from "vitest";
import { parseTreeEvent, reloadAction, type TreeEvent } from "./treeEvents";

function ev(overrides: Partial<TreeEvent> = {}): TreeEvent {
  return { event_id: 7, kind: "commit", node_id: "n1", source: "host", ...overrides };
}

describe("reloadAction", () => {
  it("ignores the editor's own saves, dirty or not", () => {
    expect(reloadAction(ev({ source: "editor" }), false)).toBe("ignore");
    expect(reloadAction(ev({ source: "editor" }), true)).toBe("ignore");
  });

  it("reloads a clean editor on a host or pipeline commit", () => {
    expect(reloadAction(ev({ source: "host" }), false)).toBe("reload");
    expect(reloadAction(ev({ source: "pipeline" }), false)).toBe("reload");
  });

  it("asks instead of discarding unsaved keystrokes", () => {
    expect(reloadAction(ev({ source: "host" }), true)).toBe("prompt");
  });

  it("treats a checkout as outside even when it lands on an editor node", () => {
    expect(reloadAction(ev({ kind: "checkout", source: "editor" }), false)).toBe("reload");
    expect(reloadAction(ev({ kind: "checkout", source: "editor" }), true)).toBe("prompt");
  });

  it("reloads when the source is unknown", () => {
    expect(reloadAction(ev({ source: null }), false)).toBe("reload");
  });
});

describe("parseTreeEvent", () => {
  it("parses the server's payload", () => {
    expect(parseTreeEvent('{"event_id":3,"kind":"commit","node_id":"a","source":"host"}'))
      .toEqual({ event_id: 3, kind: "commit", node_id: "a", source: "host" });
  });

  it("keeps a missing source as null", () => {
    expect(parseTreeEvent('{"event_id":3,"kind":"checkout","node_id":"a","source":null}')?.source)
      .toBeNull();
  });

  it("rejects malformed payloads", () => {
    expect(parseTreeEvent("not json")).toBeNull();
    expect(parseTreeEvent("null")).toBeNull();
    expect(parseTreeEvent('{"kind":"commit","node_id":"a"}')).toBeNull();
  });
});
