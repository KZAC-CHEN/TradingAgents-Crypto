import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { CryptoAsset } from "../types";
import { CryptoAssetCombobox } from "./CryptoAssetCombobox";

const ASSETS: CryptoAsset[] = [
  {
    symbol: "BTC-USDT",
    exchangeSymbol: "BTCUSDT",
    baseAsset: "BTC",
    quoteAsset: "USDT",
    nameZh: "比特币",
    nameEn: "Bitcoin",
    aliases: ["XBT"],
    featured: true,
  },
  {
    symbol: "ETH-USDT",
    exchangeSymbol: "ETHUSDT",
    baseAsset: "ETH",
    quoteAsset: "USDT",
    nameZh: "以太坊",
    nameEn: "Ethereum",
    aliases: ["Ether"],
    featured: true,
  },
];

function Harness() {
  const [value, setValue] = useState("");
  return (
    <MantineProvider>
      <CryptoAssetCombobox value={value} onChange={setValue} assets={ASSETS} />
    </MantineProvider>
  );
}

describe("加密币种组合框", () => {
  it.each(["BTC", "BTC/USDT", "Bitcoin", "比特币", "XBT"])("支持用 %s 搜索并选择", async (keyword) => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "加密币种" });

    await user.click(input);
    await user.type(input, keyword);
    expect(screen.getByText("比特币")).toBeInTheDocument();
    expect(screen.queryByText("以太坊")).not.toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");

    expect(input).toHaveValue("比特币 · BTC/USDT");
  });

  it("目录为空时展示明确提示", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider>
        <CryptoAssetCombobox value="" onChange={() => undefined} assets={[]} />
      </MantineProvider>,
    );

    await user.click(screen.getByRole("combobox", { name: "加密币种" }));
    expect(screen.getByText("暂无可用币种目录")).toBeInTheDocument();
  });
});
