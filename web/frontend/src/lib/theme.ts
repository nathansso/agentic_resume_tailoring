export type Theme = "light" | "dark";

export const THEME_KEY = "art:theme";

/**
 * Resolve the theme to apply on load: an explicit stored choice wins, otherwise
 * follow the OS preference. Light is the designed-for default, so an
 * unreadable/absent `matchMedia` falls back to light rather than dark.
 */
export function resolveInitialTheme(
  stored: string | null,
  prefersDark: boolean,
): Theme {
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark ? "dark" : "light";
}

/** Tailwind is configured with `darkMode: ["class"]`, so the class on <html> is
 *  the single switch for the whole app. */
export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function readStoredTheme(): Theme {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch {
    // Private-mode / disabled storage — fall through to the OS preference.
  }
  const prefersDark =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  return resolveInitialTheme(stored, prefersDark);
}

export function storeTheme(theme: Theme) {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Non-fatal: the choice just won't survive a reload.
  }
}
