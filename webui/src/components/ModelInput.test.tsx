import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModelInput } from "./ModelInput";

describe("模型选择输入框", () => {
  it("把 API 返回模型显示为下拉建议并允许手工输入", () => {
    const { container } = render(
      <ModelInput
        aria-label="快速模型"
        defaultValue="custom-model"
        models={[
          { id: "deepseek-chat", label: "DeepSeek Chat" },
          { id: "deepseek-reasoner", label: "DeepSeek Reasoner" },
        ]}
      />,
    );

    const input = screen.getByRole("combobox", { name: "快速模型" });
    const options = [...container.querySelectorAll("datalist option")];
    expect(input).toHaveValue("custom-model");
    expect(input).toHaveAttribute("list");
    expect(options.map((option) => option.getAttribute("value"))).toEqual([
      "deepseek-chat",
      "deepseek-reasoner",
    ]);
  });
});
