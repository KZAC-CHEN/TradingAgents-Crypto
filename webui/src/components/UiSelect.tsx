import { Loader, Select } from "@mantine/core";

import type { ConfigOption } from "../types";

interface UiSelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: ConfigOption[];
  placeholder?: string;
  searchable?: boolean;
  clearable?: boolean;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  ariaLabel?: string;
}

export function UiSelect({
  id,
  value,
  onChange,
  options,
  placeholder,
  searchable = false,
  clearable = false,
  disabled = false,
  loading = false,
  error,
  ariaLabel,
}: UiSelectProps) {
  return (
    <Select
      id={id}
      className="ta-control-root"
      classNames={{
        input: "ta-control-input",
        dropdown: "ta-control-dropdown",
        option: "ta-control-option",
        error: "ta-control-error",
      }}
      value={value || null}
      onChange={(nextValue) => onChange(nextValue ?? "")}
      data={options}
      placeholder={placeholder}
      aria-label={ariaLabel}
      searchable={searchable}
      clearable={clearable}
      disabled={disabled || loading}
      error={error}
      nothingFoundMessage="没有匹配选项"
      checkIconPosition="right"
      allowDeselect={clearable}
      maxDropdownHeight={280}
      comboboxProps={{ withinPortal: true, shadow: "md" }}
      rightSection={loading ? <Loader size={16} /> : undefined}
    />
  );
}
