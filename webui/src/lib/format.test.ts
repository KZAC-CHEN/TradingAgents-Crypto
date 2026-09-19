import { describe, expect, it } from "vitest";

import { detectAssetType, normalizeCryptoSymbol, normalizeSymbol } from "./format";

describe("标的识别", () => {
  it("把基础币和稳定币交易对统一为 USDT 交易对", () => {
    expect(normalizeCryptoSymbol("sui")).toBe("SUI-USDT");
    expect(normalizeCryptoSymbol("suiusdt")).toBe("SUI-USDT");
    expect(normalizeCryptoSymbol("SUI-USD")).toBe("SUI-USDT");
  });

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
