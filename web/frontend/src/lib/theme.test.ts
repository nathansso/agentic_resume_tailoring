import { describe, it, expect } from "vitest";
import { resolveInitialTheme } from "./theme";

describe("resolveInitialTheme", () => {
  it("honours an explicit stored choice over the OS preference", () => {
    expect(resolveInitialTheme("light", true)).toBe("light");
    expect(resolveInitialTheme("dark", false)).toBe("dark");
  });

  it("falls back to the OS preference when nothing is stored", () => {
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme(null, false)).toBe("light");
  });

  // Light is the designed-for default, so anything unrecognised must not
  // strand a user in the dark theme.
  it("ignores a corrupt stored value and uses the OS preference", () => {
    expect(resolveInitialTheme("", false)).toBe("light");
    expect(resolveInitialTheme("Dark", false)).toBe("light");
    expect(resolveInitialTheme("midnight", true)).toBe("dark");
  });
});
