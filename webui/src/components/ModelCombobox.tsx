import { CloseButton, Combobox, InputBase, Loader, useCombobox } from "@mantine/core";
import { Search } from "lucide-react";
import { useMemo } from "react";

import type { ModelInfo } from "../types";

interface ModelComboboxProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  models?: ModelInfo[];
  placeholder?: string;
  loading?: boolean;
  error?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

export function ModelCombobox({
  id,
  value,
  onChange,
  models = [],
  placeholder,
  loading = false,
  error,
  disabled = false,
  ariaLabel,
}: ModelComboboxProps) {
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
  });
  const filteredModels = useMemo(() => {
    const normalizedSearch = value.trim().toLocaleLowerCase();
    if (!normalizedSearch || models.some((model) => model.id === value)) return models;
    return models.filter((model) => (
      `${model.id} ${model.label}`.toLocaleLowerCase().includes(normalizedSearch)
    ));
  }, [models, value]);

  const rightSection = loading ? (
    <Loader size={16} />
  ) : value ? (
    <CloseButton
      size="sm"
      aria-label="清空模型"
      onMouseDown={(event) => event.preventDefault()}
      onClick={() => {
        onChange("");
        combobox.openDropdown();
      }}
    />
  ) : (
    <Combobox.Chevron />
  );

  return (
    <Combobox
      store={combobox}
      withinPortal
      shadow="md"
      onOptionSubmit={(modelId) => {
        onChange(modelId);
        combobox.closeDropdown();
      }}
    >
      <Combobox.Target withExpandedAttribute>
        <InputBase
          id={id}
          className="ta-control-root"
          classNames={{ input: "ta-control-input", error: "ta-control-error" }}
          value={value}
          onChange={(event) => {
            onChange(event.currentTarget.value);
            combobox.openDropdown();
            combobox.updateSelectedOptionIndex();
          }}
          onClick={() => combobox.openDropdown()}
          onFocus={() => combobox.openDropdown()}
          onBlur={() => combobox.closeDropdown()}
          placeholder={placeholder}
          aria-label={ariaLabel}
          autoComplete="off"
          disabled={disabled}
          error={error}
          leftSection={<Search size={15} />}
          rightSection={rightSection}
          rightSectionPointerEvents={loading ? "none" : "all"}
        />
      </Combobox.Target>
      <Combobox.Dropdown className="ta-control-dropdown">
        <Combobox.Options mah={300} style={{ overflowY: "auto" }}>
          {filteredModels.length ? filteredModels.map((model) => (
            <Combobox.Option
              className="ta-control-option ta-model-option"
              value={model.id}
              key={model.id}
              active={model.id === value}
            >
              <div className="model-option-content">
                <strong>{model.id}</strong>
                {model.label !== model.id ? <span>{model.label}</span> : null}
              </div>
            </Combobox.Option>
          )) : (
            <Combobox.Empty className="ta-combobox-empty">
              {models.length ? "没有匹配模型，可继续输入自定义模型 ID" : "暂无模型目录，可直接输入模型 ID"}
            </Combobox.Empty>
          )}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
