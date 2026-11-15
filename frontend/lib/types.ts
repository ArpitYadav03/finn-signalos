export type Level = "high" | "medium" | "low" | "routine";

export interface Citation {
  id?: string;
  kind: string;
  ticker?: string | null;
  document_id?: number | null;
  document_title?: string | null;
  source_url?: string | null;
  document_type?: string | null;
  publication_date?: string | null;
  reporting_period?: string | null;
  page_number?: number | null;
  chunk_id?: number | null;
  speaker?: string | null;
  speaker_role?: string | null;
  relevant_excerpt?: string | null;
  claim_ids?: string[];                    // kind "claims": the exact facts/calculations a sentence cites
  claims?: Record<string, Claim>;
}

export interface ClaimSource {
  kind: string; title?: string; url?: string | null; note?: string;
  primary?: { title?: string; source_url?: string; document_id?: number | null; chunk_id?: number | null; page_number?: number | null;
    excerpt?: string; column?: string; reporting_period?: string } | null;
}
export interface Claim {
  id: string; kind: "fact" | "calc"; company: string; metric: string; label: string; measure: string; value: number; unit: string;
  period: string; period_type: string; basis: string | null; base_period: string | null; source: ClaimSource;
  formula: string | null; expression: string | null; inputs: string[]; display: string;
}
export interface Verification {
  figures_checked: number; auto_linked: number; removed: { text: string; reason: string }[];
  explanations_removed: { text: string }[]; explanations_supported: { text: string; evidence: string[] }[]; insufficient: boolean; method: string;
}

export interface Calc { value: number | null; note: string | null }
export interface KeyMetric { metric: string; label: string; value: number | null; unit: string; qoq: Calc; yoy: Calc }

export interface Signal {
  id: number;
  ticker: string;
  company_name: string;
  category: string;
  category_label: string;
  headline: string;
  summary: string | null;
  why_it_matters: string | null;
  importance_score: number;
  importance_level: Level;
  reasons: { label: string; points: number }[];
  key_metrics: KeyMetric[];
  facts: Record<string, unknown>;
  citations: Citation[];
  published_at: string;
  processed_at: string;
  source: string;
  source_url: string;
  is_sample: boolean;
  requires_review: boolean;
  related: { id: number; title: string; source_url: string; published_at: string }[];
  market: { trading_date: string | null; return_pct: number | null; volume_ratio: number | null; relation?: string; label?: string;
    pre_event?: { date: string; close: number } | null; mixed_session?: { date: string; return_pct: number | null } | null; caution?: string } | null;
  analysis_method: string;
  what_changed?: string | null;
  supporting?: { id: number; published_at: string; role: string; added: string; title: string; source_url: string; citation: Citation | null }[];
  event_key?: string;
  milestones?: Milestone[];
  promise_links?: PromiseLink[];
  review?: { status: "new" | "reviewed" | "watching"; reviewed_at?: string; watch?: Milestone & { since: string; is_alert: boolean } | null;
    new_filings?: { id: number; published_at: string; role: string; added: string }[]; update?: string } | null;
}

export interface Anomaly {
  ticker: string; company_name: string; trading_date: string; close: number;
  return_pct: number | null; volume_ratio: number | null; reasons: string[];
}

export interface Freshness {
  last_ingestion_run: string | null;
  last_ingestion_status: string | null;
  latest_trading_date: string | null;
  announcements_checked_at: string | null;
  sources: Record<string, string>;
  ai_mode: string;
  next_refresh?: string | null;
}

export interface Briefing {
  window: { start: string; end: string; hours: number; custom?: boolean };
  generated_at: string;
  coverage: { holdings: number; companies_with_filings: number; filings: number };
  collection: { status: string; finished_at?: string | null; started_at?: string | null; complete?: boolean;
    filings_unavailable_for?: string[]; prices_unavailable_for?: string[]; message?: string };
  freshness: Freshness;
  counts: { total: number; events: number; high: number; medium: number; routine: number; anomalies: number };
  signals: Signal[];
  routine: Signal[];
  anomalies: Anomaly[];
  alerts: { id: number; ticker: string; message: string; type: string; created_at: string; related_event_id: number | null }[];
}

export interface Snapshot {
  trading_date: string; close: number; previous_close: number | null; return_pct: Calc; volume: number | null;
  volume_ratio: Calc; source: string; fetched_at: string; sparkline: number[]; trend_pct?: number | null; stale_suspect?: boolean;
}

export interface Holding {
  ticker: string; company_name: string; sector: string | null; size: string | null; is_financial: boolean;
  market: Snapshot | null; flags: { price_move: boolean; volume_spike: boolean }; important_announcements_7d: number;
}

export interface ResearchResponse {
  ticker: string; company_name: string; question: string; intents: string[]; mode: string; prompt_version: string;
  answer_markdown: string; evidence: Citation[]; metrics_used: string[]; caveats: string[];
  coverage: { documents_indexed: { title: string; type: string; pages: number | null }[] }; trace_id: string;
  steps: { tool: string; ok: boolean; args?: Record<string, string> }[]; unverified_numbers: string[]; follow_up?: string | null;
  claims?: Record<string, Claim>; verification?: Verification;
}

export interface PromiseItem {
  id: number; metric: string; metric_label: string; guidance_type: string; target_text: string;
  target_min: number | null; target_max: number | null; target_value: number | null; comparator: string | null;
  target_period: string | null; horizon: string; basis: string; guidance_date: string | null; speaker: string | null;
  statement: string; conditions: string | null; extractor: string;
  revision: { previous_target: string; previous_date: string | null; changed: boolean } | null;
  reiterated_on?: (string | null)[];
  comparison: { status: string; actual_value: number | null; actual_label: string | null; actual_period: string | null;
    gap_pp: number | null; method: string | null; caveats: string[] };
  guidance_citation: Citation; actual_citation: Citation | null;
  company_reported: { value: number; statement: string; speaker: string | null; consistent: boolean | null; citation: Citation } | null;
  section?: "commitment" | "aspiration" | "other"; explanations?: string[];
}

export interface PromiseTracker {
  ticker: string; basis: string | null;
  coverage?: { transcripts_indexed: number; transcripts_listed: number; periods: string[] };
  summary: { quantitative: number; qualitative: number; met: number; missed: number; tracking: number; revised: number;
    commitments?: number; aspirations?: number; other?: number };
  items: PromiseItem[];
}

export interface RuleEvaluation { at: string; companies_checked: number; eod_session: string | null; matched: string[]; new_alerts: number;
  status?: "matched" | "no_match" | "unable"; unable?: string[] }
export interface RulePreview { scope: string; conditions: string[]; combine: string | null; frequency: string; min_importance: string | null }
export interface EditableCondition { index: number; metric: string; operator: string; value: number }
export interface AlertRule { id: number; name: string; enabled: boolean; rule_type: string; spec: Record<string, unknown>; description: string;
  created_from_text: string | null; created_at: string; last_evaluation: RuleEvaluation | null;
  ticker: string | null; subject: string; clauses: string[]; joiner: string; sentence: string;
  conditions_editable?: EditableCondition[]; preview?: RulePreview }
export interface AlertItem { id: number; ticker: string; company_name?: string; type: string; message: string; details?: Record<string, unknown>;
  as_of: string | null; status: string; created_at: string; related_event_id: number | null; rule_id?: number | null }

export interface Milestone { kind: string; label: string; expected_by: string | null; basis: string; alert_categories: string[] }
export interface PromiseLink {
  relation: "stated" | "reiterated" | "revised" | "result"; id: number; metric_label: string; target_text: string; target_period: string | null;
  horizon: string; basis: string; status: string; actual_value: number | null; actual_period: string | null; method: string | null;
  revision: { previous_target: string; previous_date: string | null; changed: boolean } | null; statement: string; speaker: string | null;
  guidance_citation: Citation; actual_citation: Citation | null; explanations: string[]; section: string | null;
}
export interface ReviewFeed {
  first_visit: boolean; started_at: string | null; last_review_at: string | null; window_days: number;
  new: Signal[]; updated: Signal[]; unresolved: Signal[]; reviewed_without_changes: number; note: string;
}

// ---------------------------------------------------------------- Investor Handover
export interface PlanCondition { kind: "filing" | "guidance_cut" | "fundraise_update" | "price_move" | "volume"; categories?: string[] | null;
  roles?: string[] | null; rating_action?: string | null; change?: string | null; direction?: string | null; threshold?: number | null }
export interface PlanSpec { tickers: string[] | null; conditions: PlanCondition[]; logical_operator: "OR" | "AND" }
export interface PlanPreview { scope: string; conditions: string[]; combine: string | null; frequency: string }
export interface PlanDraft { spec: PlanSpec | null; unsupported: string[]; explanation: string; preview: PlanPreview | null;
  clarification: { question: string; field: string; options: number[]; default: number; why: string } | null }
export interface MonitoringPlan { id: number; name: string; description: string; spec: PlanSpec; version: number; enabled: boolean;
  created_at: string; updated_at: string; last_evaluated_at: string | null; preview: PlanPreview; duplicate_of?: number;
  last_change: { at: string; summary: Record<string, number> } | null }
export interface DnaStep { id: number; published_at: string; role: string; added: string; title: string; source_url: string; citation: Citation | null }
export interface DocClaim { company: string; document_id: number; document_title: string; document_type: string; document_version: string | null;
  reporting_period: string | null; published: string | null; page: number; chunk_id: number; passage: string; speaker: string | null; source_url: string | null }
export interface BriefingItem {
  id: string; review_key?: string; version_ids?: number[]; kind: "event" | "guidance_cut" | "market" | "filings" | "investigation";
  investigation_id?: number; versions?: { reviewed: number; current: number };
  ticker: string; company?: string; matched?: string[]; status?: string; latest_published_at?: string; reviewed_at?: string;
  card?: Signal; event_dna?: DnaStep[]; new_filings?: DnaStep[]; repeats?: DnaStep[]; note?: string;
  question?: string; expected_by?: string | null; basis?: string; asked_at?: string; resolved_by?: DnaStep;
  guidance_cut?: { metric: string; period: string; basis: string; previous: string; current: string; previous_date: string; date: string; rule: string; speaker: string | null };
  evidence?: { label: string; document: DocClaim | null; limitation: string | null }[];
  market?: { session: string; value: number; unit: string; close: number; previous_close: number | null; source: string };
  reason?: string; last_success?: string;
}
export interface PlanBriefing {
  plan_id: number; plan_version: number; evaluated_at: string; since: string; scope: string[]; schedule: string; paused: boolean;
  sections: { new: BriefingItem[]; updated: BriefingItem[]; resolved: BriefingItem[]; unresolved: BriefingItem[]; unable: BriefingItem[] };
  repeats_suppressed: BriefingItem[]; reviewed_unchanged: number; plan: MonitoringPlan;
  last_recorded: { at: string; fingerprint: string } | null;
}
