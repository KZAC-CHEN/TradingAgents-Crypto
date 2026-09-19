export type RunStatus =
  | "queued"
  | "preflight"
  | "evidence"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "failed"
  | "interrupted"
  | "degraded"
  | "succeeded";

export interface EvidenceProviderIssue {
  section: string;
  provider: string;
  state: string;
  detail: string;
}

export interface EvidenceHealth {
  state: "ok" | "degraded";
  sections: Record<string, string>;
  failed_sections: string[];
  provider_issues: EvidenceProviderIssue[];
  warnings: string[];
}

export interface ConfigOption {
  value: string;
  label: string;
}

export interface ConfigField {
  name: string;
  label: string;
  inputType: "password" | "text" | "url" | "number" | "select";
  secret: boolean;
  description: string;
  placeholder: string;
  configured: boolean;
  value: string;
  options?: ConfigOption[];
  min?: number;
  max?: number;
  step?: number;
}

export interface ConfigGroup {
  id: string;
  title: string;
  description: string;
  fields: ConfigField[];
}

export interface ConfigPayload {
  envPath: string;
  configuredSecrets: number;
  totalSecrets: number;
  groups: ConfigGroup[];
  csrfToken: string;
  keylessSources: string[];
  message?: string;
}

export interface ModelInfo {
  id: string;
  label: string;
}

export interface ModelDiscoveryResult {
  provider: string;
  models: ModelInfo[];
  source: "api" | "catalog";
  warning: string | null;
  fetchedAt: string;
}

export interface AnalysisRunInput {
  symbol: string;
  analysis_date: string;
  analysts: string[];
  research_depth: number;
  checkpoint_enabled: boolean;
  llm_provider?: string;
  quick_model?: string;
  deep_model?: string;
  output_language?: string;
  backend_url?: string;
  reasoning?: Record<string, string | null>;
}

export interface AnalysisRun {
  run_id: string;
  status: RunStatus;
  stage: RunStatus;
  request: AnalysisRunInput;
  config: Record<string, unknown>;
  artifact_root: string;
  attempt: number;
  checkpoint_available: boolean;
  signal: string | null;
  evidence_health: EvidenceHealth;
  queue_position: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface PreflightResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  resolved: {
    llm_provider: string;
    quick_model: string;
    deep_model: string;
    checkpoint_enabled: boolean;
    asset_type: "stock" | "crypto";
  };
}

export interface RunEvent {
  event_id: number;
  run_id: string;
  attempt: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Artifact {
  artifact_id: string;
  run_id: string;
  kind:
    | "final_report"
    | "report"
    | "partial_report"
    | "evidence_manifest"
    | "evidence_raw"
    | "evidence_report"
    | "run_log"
    | "other";
  label: string;
  relative_path: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}
