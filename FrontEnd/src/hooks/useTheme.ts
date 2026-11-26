import { useCallback, useEffect, useState } from "react";
import { storage } from "../utils/storage";

export type ThemeChoice = "system" | "light" | "dark";

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeChoice>(() =>
    storage.get<ThemeChoice>("theme", "system")
  );

  const setTheme = useCallback((next: ThemeChoice) => {
    setThemeState(next);
    storage.set("theme", next);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
  }, [theme]);

  return { theme, setTheme };
}
