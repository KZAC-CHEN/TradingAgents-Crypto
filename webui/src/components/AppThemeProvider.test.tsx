import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { AppThemeProvider, useAppTheme } from "./AppThemeProvider";

function ThemeProbe() {
  const { theme, toggleTheme } = useAppTheme();
  return <button onClick={toggleTheme}>{theme}</button>;
}

describe("应用主题提供器", () => {
  beforeEach(() => localStorage.clear());

  it("同步现有主题存储、页面属性和 Mantine 颜色模式", async () => {
    localStorage.setItem("tradingagents-theme", "dark");
    const user = userEvent.setup();
    render(<AppThemeProvider><ThemeProbe /></AppThemeProvider>);

    expect(screen.getByRole("button", { name: "dark" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(document.documentElement.dataset.mantineColorScheme).toBe("dark");

    await user.click(screen.getByRole("button", { name: "dark" }));
    await waitFor(() => expect(localStorage.getItem("tradingagents-theme")).toBe("light"));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.dataset.mantineColorScheme).toBe("light");
  });
});
