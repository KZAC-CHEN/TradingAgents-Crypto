import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Artifact } from "../types";
import { ArtifactContent } from "./ReportsPage";

const artifact: Artifact = {
  artifact_id: "artifact-1",
  run_id: "run-1",
  kind: "final_report",
  label: "完整报告",
  relative_path: "reports/complete_report.md",
  media_type: "text/markdown",
  size_bytes: 100,
  sha256: "abc",
  created_at: "2026-09-19T00:00:00Z",
};

describe("报告安全渲染", () => {
  it("不执行 Markdown 中的原始 HTML", () => {
    const { container } = render(
      <ArtifactContent artifact={artifact} content={'# 报告\n\n<script>alert("xss")</script>'} />,
    );
    expect(screen.getByRole("heading", { name: "报告" })).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("为外部链接添加安全属性", () => {
    render(<ArtifactContent artifact={artifact} content="[来源](https://example.com/report)" />);
    const link = screen.getByRole("link", { name: "来源" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
  });

  it("正确显示证据分区错误和结构化来源", () => {
    const manifestArtifact: Artifact = {
      ...artifact,
      kind: "evidence_manifest",
      media_type: "application/json",
      label: "统一加密证据清单",
    };
    render(<ArtifactContent artifact={manifestArtifact} content={JSON.stringify({
      sections: { market: { state: "error" }, news: { state: "ok" } },
      providers: [{ provider: "Binance", state: "error", detail: "HTTP 451" }],
      warnings: ["合约接口不可用"],
    })} />);

    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("Binance")).toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
    expect(screen.getByText("合约接口不可用")).toBeInTheDocument();
  });
});
