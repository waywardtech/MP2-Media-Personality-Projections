// Typed client for the MP2 API.
//
// The web tier talks only to the API. It never holds object-store credentials and never
// reads raw media: everything it renders arrives as canonical, lineage-bearing JSON.

const BASE = "/api";

export type MediaWork = {
  id: string;
  version: number;
  title: string;
  work_type: string;
  metadata_json: Record<string, unknown>;
};

export type AnalysisRun = {
  id: string;
  version: number;
  media_work_id: string;
  source_asset_id: string | null;
  status: string;
  workflow_id: string | null;
  parameters: Record<string, unknown>;
  error: string | null;
};

export type Segment = {
  id: string;
  kind: string;
  start_ms: number;
  end_ms: number;
};

export type Extractor = {
  id: string;
  version: string;
  repeatability_class: string;
  license: string;
};

export type Measurement = {
  id: string;
  metric: string;
  segment_id: string | null;
  content_hash: string;
  source_ref: string;
  extractor: Extractor | null;
  value: Record<string, unknown>;
};

export type EvidenceClaim = {
  id: string;
  claim_type: string;
  segment_id: string | null;
  model_execution_id: string | null;
  confidence: number;
  evidence_refs: string[];
  schema_version: string;
  structured_value: Record<string, unknown>;
};

export type ModelExecution = {
  id: string;
  provider_adapter: string;
  model_tool_id: string;
  model_tool_version: string;
  execution_location: string;
  latency_ms: number;
  estimated_cost: number;
  content_hash: string;
  warnings: string[];
};

export type RunEvidence = {
  run: { id: string; status: string; version: number; workflow_id: string | null; error: string | null };
  segments: Segment[];
  measurements: Measurement[];
  evidence_claims: EvidenceClaim[];
  model_executions: ModelExecution[];
};

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => get<{ status: string }>("/health"),
  ready: () => get<{ status: string }>("/ready"),
  listMedia: () => get<MediaWork[]>("/v1/media"),
  listRuns: () => get<AnalysisRun[]>("/v1/analysis-runs"),
  runEvidence: (runId: string) => get<RunEvidence>(`/v1/analysis-runs/${runId}/evidence`),

  createMedia: async (body: {
    title: string;
    work_type: string;
    edition_label: string;
  }): Promise<MediaWork> => {
    const response = await fetch(`${BASE}/v1/media`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...body, metadata: {} }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()) as MediaWork;
  },

  addAsset: async (
    mediaWorkId: string,
    body: {
      local_path: string;
      provenance_class: string;
      rights_access_class: string;
      retention_class: string;
    },
  ): Promise<{ id: string; sha256: string; object_key: string }> => {
    const response = await fetch(`${BASE}/v1/media/${mediaWorkId}/assets`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  },

  analyze: async (mediaWorkId: string, sourceAssetId: string): Promise<AnalysisRun> => {
    const response = await fetch(`${BASE}/v1/media/${mediaWorkId}/analyze`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source_asset_id: sourceAssetId, parameters: {} }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()) as AnalysisRun;
  },
};

export const PROVENANCE_CLASSES = [
  "synthetic",
  "user_supplied",
  "public_domain",
  "licensed",
] as const;
export const RIGHTS_CLASSES = ["internal_test", "redistributable", "private"] as const;
export const RETENTION_CLASSES = ["project", "ephemeral", "legal_hold"] as const;
