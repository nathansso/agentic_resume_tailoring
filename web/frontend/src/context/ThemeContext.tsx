import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { applyTheme, readStoredTheme, storeTheme, type Theme } from "../lib/theme";

interface ThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // The inline script in index.html has already put the right class on <html>
  // before first paint; this just mirrors that decision into React state.
  const [theme, setTheme] = useState<Theme>(() => readStoredTheme());

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      applyTheme(next);
      storeTheme(next);
      return next;
    });
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
