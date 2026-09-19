import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { ModelCombobox } from "./ModelCombobox";

const MODELS = [
  { id: "deepseek-chat", label: "DeepSeek Chat" },
  { id: "deepseek-reasoner", label: "DeepSeek Reasoner" },
];

function ModelHarness({ models = MODELS }: { models?: typeof MODELS }) {
  const [value, setValue] = useState("");
  return (
    <MantineProvider>
      <ModelCombobox ariaLabel="快速模型" value={value} onChange={setValue} models={models} />
    </MantineProvider>
  );
}

describe("模型组合框", () => {
  it("支持按显示名称搜索并用键盘选择模型 ID", async () => {
    const user = userEvent.setup();
    render(<ModelHarness />);
    const input = screen.getByRole("combobox", { name: "快速模型" });

    await user.click(input);
    expect(screen.getByText("DeepSeek Chat")).toBeInTheDocument();
    await user.type(input, "Reasoner");
    expect(screen.queryByText("DeepSeek Chat")).not.toBeInTheDocument();
    expect(screen.getByText("DeepSeek Reasoner")).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");

    expect(input).toHaveValue("deepseek-reasoner");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("允许输入目录之外的自定义模型 ID", async () => {
    const user = userEvent.setup();
    render(<ModelHarness />);
    const input = screen.getByRole("combobox", { name: "快速模型" });

    await user.type(input, "proxy-custom-model");

    expect(input).toHaveValue("proxy-custom-model");
    expect(screen.getByText("没有匹配模型，可继续输入自定义模型 ID")).toBeInTheDocument();
  });

  it("目录为空时显示降级提示并保留输入能力", async () => {
    const user = userEvent.setup();
    render(<ModelHarness models={[]} />);
    const input = screen.getByRole("combobox", { name: "快速模型" });

    await user.click(input);
    expect(screen.getByText("暂无模型目录，可直接输入模型 ID")).toBeInTheDocument();
    await user.type(input, "local-model");
    expect(input).toHaveValue("local-model");
  });
});
