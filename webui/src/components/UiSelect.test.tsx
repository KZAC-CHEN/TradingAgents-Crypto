import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { UiSelect } from "./UiSelect";

function SelectHarness() {
  const [value, setValue] = useState("");
  return (
    <MantineProvider>
      <UiSelect
        ariaLabel="模型供应商"
        value={value}
        onChange={setValue}
        placeholder="使用全局设置"
        options={[
          { value: "deepseek", label: "DeepSeek" },
          { value: "openai", label: "OpenAI" },
        ]}
        searchable
        clearable
      />
    </MantineProvider>
  );
}

describe("统一下拉框", () => {
  it("支持键盘选择并把空值显示为全局默认", async () => {
    const user = userEvent.setup();
    render(<SelectHarness />);
    const input = screen.getByRole("combobox", { name: "模型供应商" });

    expect(input).toHaveAttribute("placeholder", "使用全局设置");
    await user.click(input);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(input).toHaveValue("DeepSeek");
  });

  it("按 Escape 关闭通过 Portal 渲染的选项层", async () => {
    const user = userEvent.setup();
    render(<SelectHarness />);
    const input = screen.getByRole("combobox", { name: "模型供应商" });

    await user.click(input);
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByText("OpenAI").length).toBeGreaterThan(0);
    await user.keyboard("{Escape}");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });
});
