import { describe, expect, it } from "vitest";

import { detectAssetType, normalizeSymbol } from "./format";

describe("标的识别", () => {
  it("规范化主流加密标的", () => {
    expect(normalizeSymbol("btc")).toBe("BTC-USD");
    expect(normalizeSymbol("eth/usdt")).toBe("ETH-USDT");
    expect(normalizeSymbol("SOLUSDT")).toBe("SOL-USDT");
    expect(detectAssetType("BTC-USD")).toBe("crypto");
  });

  it("保留美股代码", () => {
    expect(normalizeSymbol("aapl")).toBe("AAPL");
    expect(detectAssetType("NVDA")).toBe("stock");
  });
});
