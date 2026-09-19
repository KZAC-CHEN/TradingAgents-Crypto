import { CloseButton, Combobox, InputBase, Loader, useCombobox } from "@mantine/core";
import { Coins, Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { CryptoAsset } from "../types";

interface CryptoAssetComboboxProps {
  value: string;
  onChange: (value: string) => void;
  assets?: CryptoAsset[];
  loading?: boolean;
  error?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

function selectedLabel(asset: CryptoAsset | undefined, value: string): string {
  if (!asset) return value.replace("-", "/");
  const name = asset.nameZh || asset.nameEn;
  return name ? `${name} · ${asset.baseAsset}/${asset.quoteAsset}` : `${asset.baseAsset}/${asset.quoteAsset}`;
}

function searchText(asset: CryptoAsset): string {
  return [
    asset.symbol,
    asset.exchangeSymbol,
    `${asset.baseAsset}/${asset.quoteAsset}`,
    asset.baseAsset,
    asset.nameZh,
    asset.nameEn,
    ...asset.aliases,
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

export function CryptoAssetCombobox({
  value,
  onChange,
  assets = [],
  loading = false,
  error,
  disabled = false,
  ariaLabel = "加密币种",
}: CryptoAssetComboboxProps) {
  const [search, setSearch] = useState("");
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const selected = assets.find((asset) => asset.symbol === value);
  const normalizedSearch = search.trim().toLocaleLowerCase().replaceAll("-", "").replaceAll("/", "");
  const filteredAssets = useMemo(() => {
    if (!normalizedSearch) return assets;
    return assets.filter((asset) => searchText(asset).replaceAll("-", "").replaceAll("/", "").includes(normalizedSearch));
  }, [assets, normalizedSearch]);

  const open = () => {
    setSearch("");
    combobox.openDropdown();
    combobox.updateSelectedOptionIndex("active");
  };

  return (
    <Combobox
      store={combobox}
      withinPortal
      shadow="md"
      onOptionSubmit={(symbol) => {
        onChange(symbol);
        setSearch("");
        combobox.closeDropdown();
      }}
    >
      <Combobox.Target withExpandedAttribute>
        <InputBase
          className="ta-control-root"
          classNames={{ input: "ta-control-input", error: "ta-control-error" }}
          value={combobox.dropdownOpened ? search : selectedLabel(selected, value)}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            combobox.openDropdown();
            combobox.updateSelectedOptionIndex();
          }}
          onClick={open}
          onFocus={open}
          onBlur={() => combobox.closeDropdown()}
          placeholder="搜索 BTC、Bitcoin 或比特币"
          aria-label={ariaLabel}
          autoComplete="off"
          disabled={disabled}
          error={error}
          leftSection={combobox.dropdownOpened ? <Search size={15} /> : <Coins size={15} />}
          rightSection={loading ? <Loader size={16} /> : value ? (
            <CloseButton
              size="sm"
              aria-label="清空币种"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange("");
                setSearch("");
                combobox.openDropdown();
              }}
            />
          ) : <Combobox.Chevron />}
          rightSectionPointerEvents={loading ? "none" : "all"}
        />
      </Combobox.Target>
      <Combobox.Dropdown className="ta-control-dropdown">
        <Combobox.Options mah={330} style={{ overflowY: "auto" }}>
          {filteredAssets.length ? filteredAssets.map((asset) => (
            <Combobox.Option
              className="ta-control-option ta-crypto-option"
              value={asset.symbol}
              key={asset.symbol}
              active={asset.symbol === value}
            >
              <span className="crypto-option-mark">{asset.baseAsset.slice(0, 2)}</span>
              <span className="crypto-option-copy">
                <strong>{asset.nameZh || asset.nameEn || asset.baseAsset}</strong>
                <small>{asset.nameZh && asset.nameEn ? `${asset.nameEn} · ` : ""}{asset.baseAsset}/{asset.quoteAsset}</small>
              </span>
              {asset.featured ? <em>热门</em> : null}
            </Combobox.Option>
          )) : (
            <Combobox.Empty className="ta-combobox-empty">
              {loading ? "正在读取币安币种目录…" : assets.length ? "没有匹配币种" : "暂无可用币种目录"}
            </Combobox.Empty>
          )}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
