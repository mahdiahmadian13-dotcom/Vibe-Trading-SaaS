import { api } from "./client";

/* ------------------------------ types ------------------------------ */

export type DecayStatus = "fresh" | "aging" | "stale";
export type Quality = "adequate" | "marginal" | "insufficient";
export type Regime = "bear_market" | "bull_market" | "structural";

export type StrategyItem = {
  strategy_id: string;
  name: string;
  source: "alpha_zoo" | "sdm";
  description?: string | null;
  status?: string | null;
  universe?: string | null;
  has_evidence: boolean;
  regimes_with_evidence: Regime[];
};

export type StrategyListResponse = {
  status: string;
  total: number;
  returned: number;
  offset: number;
  source?: string | null;
  items: StrategyItem[];
};

export type EvidenceRow = {
  strategy_id: string;
  regime: Regime;
  trades_in_regime: number;
  position_size?: number | null;
  return_in_regime?: number | null;
  benchmark_in_regime?: number | null;
  excess_in_regime?: number | null;
  sharpe_in_regime?: number | null;
  max_drawdown_in_regime?: number | null;
  date_ranges: string[];
  breakeven_fee_bps?: number | null;
  cost_sensitive: boolean;
  evidence_quality: Quality;
  warnings: string[];
  last_verified: string;
  evidence_stage: string;
  provenance: string;
  regime_definition: string;
  decay_status: DecayStatus;
  evidence_age_days?: number | null;
  staleness_days?: number | null;
  borderline?: boolean;
  lifecycle_note?: string;
};

export type QueryResponse = {
  status: string;
  count: number;
  returned: number;
  filters: Record<string, unknown>;
  stale_excluded: number;
  lifecycle_excluded: number;
  items: EvidenceRow[];
  note?: string;
};

export type EvidenceDetailResponse = {
  status: string;
  strategy_id: string;
  regime?: string | null;
  found: boolean;
  rows: EvidenceRow[];
  lifecycle_note?: string;
  note?: string;
};

export type RefreshResponse = {
  status: string;
  runs: number;
  strategies: number;
  rows: number;
  skipped: Array<{ run_dir?: string | null; reason: string }>;
};

/* ------------------------------ calls ------------------------------ */

export const listStrategies = (limit = 20, offset = 0, source?: string) => {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (source) qs.set("source", source);
  return api<StrategyListResponse>(`/api/v1/vibe/strategies?${qs}`);
};

export const queryStrategies = (params: {
  regime?: string;
  min_sharpe?: number;
  min_evidence_quality?: string;
  min_trades?: number;
  cost_feasible?: boolean;
  limit?: number;
  include_stale?: boolean;
}) => {
  const qs = new URLSearchParams();
  if (params.regime) qs.set("regime", params.regime);
  if (params.min_sharpe != null) qs.set("min_sharpe", String(params.min_sharpe));
  if (params.min_evidence_quality) qs.set("min_evidence_quality", params.min_evidence_quality);
  if (params.min_trades != null) qs.set("min_trades", String(params.min_trades));
  if (params.cost_feasible != null) qs.set("cost_feasible", String(params.cost_feasible));
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.include_stale != null) qs.set("include_stale", String(params.include_stale));
  return api<QueryResponse>(`/api/v1/vibe/strategies/query?${qs}`);
};

export const getStrategyEvidence = (strategyId: string, regime?: string) => {
  const qs = regime ? `?regime=${encodeURIComponent(regime)}` : "";
  return api<EvidenceDetailResponse>(
    `/api/v1/vibe/strategies/${encodeURIComponent(strategyId)}/evidence${qs}`
  );
};

export const refreshEvidence = (runs: Array<{ strategy_id: string; run_dir: string; position_size?: number }>) =>
  api<RefreshResponse>("/api/v1/vibe/strategies/evidence/refresh", {
    method: "POST",
    body: JSON.stringify({ runs }),
  });

/* ------------------------------ fa ------------------------------ */

export const REGIME_FA: Record<string, string> = {
  bear_market: "بازار نزولی",
  bull_market: "بازار صعودی",
  structural: "ساختاری",
};

export const DECAY_FA: Record<DecayStatus, string> = {
  fresh: "تازه",
  aging: "در حال کهنگی",
  stale: "کهنه",
};

export const QUALITY_FA: Record<string, string> = {
  adequate: "کافی",
  marginal: "مرزی",
  insufficient: "ناکافی",
};
