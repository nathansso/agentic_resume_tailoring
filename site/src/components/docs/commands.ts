// The install and usage pages render their commands from commands.json, and
// tests/test_site_docs.py holds every line to the docs it comes from.
import data from "./commands.json";

type Line = string | { text: string; in_source: string };

const vars: Record<string, string> = data.vars;

/** Expand {NAME} placeholders, repeatedly, so SOURCE can contain URL. */
export function expand(text: string): string {
  let out = text;
  for (let i = 0; i < 4 && /\{[A-Z]+\}/.test(out); i++) {
    out = out.replace(/\{([A-Z]+)\}/g, (_, name: string) => vars[name] ?? `{${name}}`);
  }
  return out;
}

/** The lines of one command block, ready to show and copy. */
export function block(id: keyof typeof data.blocks): string[] {
  const entry = data.blocks[id] as { lines: Line[] };
  return entry.lines.map((line) => expand(typeof line === "string" ? line : line.text));
}

/** The three /artie:* commands, as plugin/README.md lists them. */
export const slashCommands: { command: string; what: string }[] = data.slash;

/** The release wheel URL and the pinned spec, for prose that names them. */
export const WHEEL_URL = expand("{URL}");
export const SOURCE = expand("{SOURCE}");
