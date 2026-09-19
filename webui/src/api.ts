import type {
  AnalysisRun,
  AnalysisRunInput,
  Artifact,
  ConfigPayload,
  CryptoAssetCatalogResult,
  ModelDiscoveryResult,
  PreflightResult,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  const data = (await response.json().catch(() => ({}))) as T & { error?: string };
  if (!response.ok) {
    throw new ApiError(data.error || `请求失败（${response.status}）`, response.status);
  }
  return data;
}

function mutationInit(csrfToken: string, body?: unknown): RequestInit {
  return {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export const api = {
  getConfig: () => apiFetch<ConfigPayload>("/api/config"),
  updateConfig: (
    csrfToken: string,
    updates: Record<string, string>,
    deletes: string[],
  ) =>
    apiFetch<ConfigPayload>("/api/config", {
      ...mutationInit(csrfToken, { updates, deletes }),
      method: "PUT",
    }),
  discoverModels: (csrfToken: string, provider: string, refresh = false) =>
    apiFetch<ModelDiscoveryResult>(
      "/api/models/discover",
      mutationInit(csrfToken, { provider, refresh }),
    ),
  discoverCryptoAssets: (refresh = false) =>
    apiFetch<CryptoAssetCatalogResult>(`/api/instruments/crypto?refresh=${refresh}`),
  listRuns: () => apiFetch<{ items: AnalysisRun[] }>("/api/runs"),
  getRun: (runId: string) => apiFetch<AnalysisRun>(`/api/runs/${runId}`),
  preflight: (csrfToken: string, input: AnalysisRunInput) =>
    apiFetch<PreflightResult>("/api/preflight", mutationInit(csrfToken, input)),
  createRun: (csrfToken: string, input: AnalysisRunInput) =>
    apiFetch<AnalysisRun>("/api/runs", mutationInit(csrfToken, input)),
  cancelRun: (csrfToken: string, runId: string) =>
    apiFetch<AnalysisRun>(`/api/runs/${runId}/cancel`, mutationInit(csrfToken)),
  resumeRun: (csrfToken: string, runId: string) =>
    apiFetch<AnalysisRun>(`/api/runs/${runId}/resume`, mutationInit(csrfToken)),
  listArtifacts: (runId: string) =>
    apiFetch<{ items: Artifact[] }>(`/api/runs/${runId}/artifacts`),
  getArtifact: (artifactId: string) =>
    apiFetch<{ artifact: Artifact; content: string }>(`/api/artifacts/${artifactId}`),
};
