import type { RunStatus } from "../types";

const CRYPTO_BASES = new Set([
  "BTC",
  "ETH",
  "BNB",
  "SOL",
  "XRP",
  "ADA",
  "DOGE",
  "AVAX",
  "DOT",
  "LINK",
  "LTC",
  "TRX",
]);

export function normalizeSymbol(value: string): string {
  const symbol = value.trim().toUpperCase().replace("/", "-");
  if (/^[A-Z0-9]{2,12}USDT$/.test(symbol)) {
    return `${symbol.slice(0, -4)}-USDT`;
  }
  if (/^[A-Z0-9]{2,12}USD$/.test(symbol)) {
    return `${symbol.slice(0, -3)}-USD`;
  }
  if (CRYPTO_BASES.has(symbol)) {
    return `${symbol}-USD`;
  }
  return symbol;
}

export function normalizeCryptoSymbol(value: string): string {
  const compact = value.trim().toUpperCase().replaceAll("-", "").replaceAll("/", "").replaceAll("_", "");
  if (!compact) return "";
  const quotes = ["USDT", "USDC", "BUSD", "USD"];
  const quote = quotes.find((candidate) => compact.endsWith(candidate) && compact.length > candidate.length);
  const base = quote ? compact.slice(0, -quote.length) : compact;
  return /^[A-Z0-9]{2,20}$/.test(base) ? `${base}-USDT` : value.trim().toUpperCase();
}

export function detectAssetType(value: string): "crypto" | "stock" {
  const symbol = normalizeSymbol(value);
  return symbol.endsWith("-USD") || symbol.endsWith("-USDT") ? "crypto" : "stock";
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start) return "尚未开始";
  const milliseconds = Math.max(0, new Date(end || Date.now()).getTime() - new Date(start).getTime());
  const seconds = Math.floor(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${seconds % 60} 秒`;
}

export const STATUS_LABELS: Record<RunStatus, string> = {
  queued: "排队中",
  preflight: "预检",
  evidence: "采集证据",
  running: "分析中",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  failed: "失败",
  interrupted: "已中断",
  degraded: "降级完成",
  succeeded: "已完成",
};

export function localIsoDate(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}
