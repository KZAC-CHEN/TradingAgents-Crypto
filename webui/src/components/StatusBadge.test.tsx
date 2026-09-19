import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("任务状态", () => {
  it("显示中文状态标签", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText("分析中")).toHaveClass("status-running");
  });
});
