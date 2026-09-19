import { createTheme, MantineProvider } from "@mantine/core";
import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";

type Theme = "light" | "dark";

interface AppThemeContextValue {
  theme: Theme;
  toggleTheme: () => void;
}

const AppThemeContext = createContext<AppThemeContextValue | null>(null);

const mantineTheme = createTheme({
  primaryColor: "blue",
  defaultRadius: "md",
  fontFamily: 'Inter, "Microsoft YaHei", "PingFang SC", system-ui, sans-serif',
  headings: {
    fontFamily: 'Inter, "Microsoft YaHei", "PingFang SC", system-ui, sans-serif',
  },
});

function getInitialTheme(): Theme {
  const saved = localStorage.getItem("tradingagents-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function AppThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("tradingagents-theme", theme);
  }, [theme]);

  const contextValue = useMemo<AppThemeContextValue>(
    () => ({
      theme,
      toggleTheme: () => setTheme((current) => (current === "dark" ? "light" : "dark")),
    }),
    [theme],
  );

  return (
    <AppThemeContext.Provider value={contextValue}>
      <MantineProvider theme={mantineTheme} forceColorScheme={theme}>
        {children}
      </MantineProvider>
    </AppThemeContext.Provider>
  );
}

export function useAppTheme(): AppThemeContextValue {
  const context = useContext(AppThemeContext);
  if (!context) throw new Error("useAppTheme 必须在 AppThemeProvider 内使用");
  return context;
}
