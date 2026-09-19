import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Eye, EyeOff, KeyRound, LoaderCircle, Save, ShieldCheck, Trash2 } from "lucide-react";
import { type ChangeEvent, useMemo, useState } from "react";

import { api } from "../api";
import type { ConfigField } from "../types";

function SettingInput({
  field,
  value,
  cleared,
  onChange,
  onClear,
}: {
  field: ConfigField;
  value: string;
  cleared: boolean;
  onChange: (value: string) => void;
  onClear: () => void;
}) {
  const [visible, setVisible] = useState(false);
  const common = {
    value,
    onChange: (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(event.target.value),
  };
  return (
    <div className={`setting-field ${cleared ? "will-clear" : ""}`}>
      <div className="setting-label"><label htmlFor={field.name}>{field.label}</label><span className={field.configured && !cleared ? "configured" : ""}><i />{field.configured && !cleared ? "已配置" : "未配置"}</span></div>
      <div className="setting-input-row">
        {field.inputType === "select" ? (
          <select id={field.name} {...common}><option value="">使用项目默认值</option>{field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
        ) : (
          <input
            id={field.name}
            type={field.secret && !visible ? "password" : field.inputType}
            placeholder={field.secret && field.configured ? "已安全保存；输入新值可替换" : field.placeholder || "尚未配置"}
            min={field.min}
            max={field.max}
            step={field.step}
            autoComplete={field.secret ? "new-password" : "off"}
            {...common}
          />
        )}
        {field.secret ? <button type="button" className="input-action" onClick={() => setVisible(!visible)} aria-label="显示或隐藏密钥">{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button> : null}
        {field.configured ? <button type="button" className="input-action danger" onClick={onClear} aria-label={`清除 ${field.label}`}><Trash2 size={17} /></button> : null}
      </div>
      <div className="setting-meta"><code>{field.name}</code>{field.description ? <span>{field.description}</span> : null}</div>
    </div>
  );
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ["config"], queryFn: api.getConfig });
  const [activeGroup, setActiveGroup] = useState<string>("news");
  const [updates, setUpdates] = useState<Record<string, string>>({});
  const [deletes, setDeletes] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState("");
  const data = configQuery.data;
  const group = useMemo(() => data?.groups.find((item) => item.id === activeGroup) ?? data?.groups[0], [activeGroup, data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!data) throw new Error("配置尚未载入。");
      return api.updateConfig(data.csrfToken, updates, [...deletes]);
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["config"], result);
      setUpdates({});
      setDeletes(new Set());
      setMessage(result.message || "配置已保存。");
    },
  });

  function changeField(field: ConfigField, value: string) {
    setDeletes((current) => {
      const next = new Set(current);
      next.delete(field.name);
      return next;
    });
    setUpdates((current) => {
      const next = { ...current };
      const original = field.secret ? "" : field.value;
      if (value === original || (field.secret && !value)) delete next[field.name];
      else if (!value && original) {
        delete next[field.name];
        setDeletes((current) => new Set(current).add(field.name));
      } else next[field.name] = value;
      return next;
    });
  }

  function clearField(name: string) {
    setUpdates((current) => { const next = { ...current }; delete next[name]; return next; });
    setDeletes((current) => new Set(current).add(name));
  }

  if (configQuery.isLoading) return <div className="loading-block">正在读取本地配置…</div>;
  if (configQuery.isError || !data || !group) return <div className="error-banner">无法读取配置：{configQuery.error?.message}</div>;
  const changeCount = Object.keys(updates).length + deletes.size;

  return (
    <div className="page-stack settings-page">
      <header className="page-title-row">
        <div><p className="eyebrow">SECURE CONFIGURATION</p><h1>设置</h1><p>统一管理模型与数据源。完整密钥不会从服务端回显。</p></div>
        <div className="security-score"><ShieldCheck size={21} /><div><strong>{data.configuredSecrets}/{data.totalSecrets}</strong><span>密钥已配置</span></div></div>
      </header>
      <section className="keyless-strip"><KeyRound size={20} /><div><strong>无需密钥即可使用</strong><span>{data.keylessSources.join(" · ")}</span></div></section>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          {data.groups.map((item, index) => <button key={item.id} className={group.id === item.id ? "active" : ""} onClick={() => setActiveGroup(item.id)}><b>0{index + 1}</b><span>{item.title}<small>{item.fields.filter((field) => field.configured).length} 项已配置</small></span></button>)}
        </nav>
        <section className="panel settings-panel">
          <div className="panel-heading"><div><p className="eyebrow">CONFIG GROUP</p><h2>{group.title}</h2></div><p>{group.description}</p></div>
          <div className="settings-grid">
            {group.fields.map((field) => <SettingInput key={field.name} field={field} value={updates[field.name] ?? (field.secret ? "" : field.value)} cleared={deletes.has(field.name)} onChange={(value) => changeField(field, value)} onClear={() => clearField(field.name)} />)}
          </div>
        </section>
      </div>
      <div className="save-dock">
        <div>{message ? <><Check size={17} /><strong>{message}</strong></> : <><span className="change-dot" /><strong>{changeCount ? `${changeCount} 项待保存` : "尚无变更"}</strong></>}<small>密钥留空时保持原值</small></div>
        <button className="button button-primary" disabled={!changeCount || saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? <LoaderCircle className="spin" size={18} /> : <Save size={18} />}保存配置</button>
      </div>
      {saveMutation.isError ? <div className="error-banner floating-error">{saveMutation.error.message}</div> : null}
      <footer className="settings-footer">保存位置：{data.envPath}</footer>
    </div>
  );
}
