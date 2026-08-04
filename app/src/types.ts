export type RiskLevel = "none" | "low" | "medium" | "high" | "critical";
export type SourceUpdatePolicy = "remote" | "local";
export type AuditContext = "command_invocation" | "documentation" | "denylist" | "artifact";
export type AuditReviewStatus = "unreviewed" | "reviewed_false_positive" | "confirmed_risk" | "informational";

export interface ValidationFinding {
  severity: string;
  rule: string;
  message: string;
  file: string;
  line?: number;
}

export interface AuditFinding extends ValidationFinding {
  context: AuditContext;
  classification: "risk" | "capability_hint";
  finding_id: string;
  content_digest: string;
  content_summary: string;
  status: AuditReviewStatus;
  review_stale: boolean;
  review_note?: string | null;
  reviewed_at?: string | null;
  review_content_summary?: string | null;
}

export interface AppSummary {
  source_count: number;
  skill_count: number;
  valid_count: number;
  invalid_count: number;
  annotated_count: number;
  last_scanned_at: string | null;
  risk_counts: Record<RiskLevel, number>;
  pending_evaluation_count: number;
  proposal_count: number;
}

export interface SourceSummary {
  id: string;
  name: string;
  url: string | null;
  local_path: string;
  tracked_ref: string | null;
  update_policy: SourceUpdatePolicy;
  head_sha: string | null;
  status: string;
  last_scanned_at: string | null;
  created_at: string;
  updated_at: string;
  skill_count: number;
  valid_count: number;
  invalid_count: number;
  elevated_risk_count: number;
  pending_evaluation_count: number;
}

export type LLMProfileProvider = "codex" | "claude" | "openai-compatible";
export type LLMProvider = "disabled" | LLMProfileProvider;
export type LLMAPIMode = "responses" | "chat-completions" | "auto";

export interface LLMProfile {
  id: string;
  name: string;
  provider: LLMProfileProvider;
  model: string | null;
  base_url: string | null;
  api_mode: LLMAPIMode | null;
  timeout_seconds: number;
  max_per_run: number;
  credential_configured: boolean;
}

export interface LLMConfig {
  version: number;
  provider: LLMProvider;
  model: string | null;
  timeout_seconds: number;
  max_per_run: number;
  active_profile_id: string | null;
  profiles: LLMProfile[];
}

export interface LLMEvaluation {
  id: string;
  skill_id: string;
  skill_name: string;
  source_name: string;
  content_hash: string;
  profile_id: string;
  provider: Exclude<LLMProvider, "disabled">;
  model: string | null;
  prompt_version: string;
  taxonomy_version: string;
  category_l1: string | null;
  category_l2: string | null;
  category_candidate: boolean;
  problem: string | null;
  use_case: string | null;
  score: number | null;
  dimensions: Record<string, number>;
  notes: string | null;
  tags: string[];
  confidence: number | null;
  status: "proposed" | "applied" | "rejected" | "error";
  error: string | null;
  has_annotation: boolean;
  current_content: boolean;
  created_at: string;
}

export interface LLMStatus {
  config: LLMConfig;
  active_profile: LLMProfile | null;
  availability: {
    codex: boolean;
    claude: boolean;
    "openai-compatible": boolean;
    credential_store: boolean;
  };
  taxonomy: {
    version: string;
    level_one: string[];
    level_two: Record<string, string[]>;
    policy: Record<string, string | number>;
  };
  pending_count: number;
  proposal_count: number;
  proposals: LLMEvaluation[];
}

export interface LLMEvaluationRun {
  provider: string;
  model: string | null;
  requested: number;
  proposed: number;
  failed: number;
  results: LLMEvaluation[];
}

export interface SourceRefreshResultItem {
  source_id: string;
  source: string;
  status: "updated" | "unchanged" | "local" | "failed";
  before_sha: string | null;
  after_sha?: string | null;
  type?: string;
  error?: string;
  scan?: {
    discovered: number;
    valid: number;
    invalid: number;
    critical: number;
  };
}

export interface SourceRefreshAllResult {
  total: number;
  updated: number;
  unchanged: number;
  local: number;
  failed: number;
  results: SourceRefreshResultItem[];
}

export interface SearchReason {
  field: string;
  terms: string[];
  contribution: number;
}

export interface SkillSummary {
  id: string;
  source_id?: string;
  source_name: string;
  name: string;
  description: string;
  rel_path: string;
  valid: boolean;
  audit_severity: RiskLevel;
  format_issue_count?: number;
  capability_hint_count?: number;
  unreviewed_risk_count?: number;
  confirmed_risk_count?: number;
  false_positive_count?: number;
  category_l1?: string | null;
  category_l2?: string | null;
  score?: number | null;
  annotation_score?: number | null;
  review_status?: string | null;
  tags?: string[];
  license?: string | null;
  compatibility?: string | null;
  updated_at?: string;
  reason?: SearchReason[];
}

export interface SkillDetail extends SkillSummary {
  source_path: string;
  source_url: string | null;
  skill_md_path: string;
  body: string;
  problem: string | null;
  use_case: string | null;
  notes: string | null;
  validation: ValidationFinding[];
  audit: AuditFinding[];
  metadata: Record<string, unknown>;
  content_hash: string;
  tree_hash: string;
  head_sha: string | null;
}

export interface CategoryFilter {
  category_l1: string | null;
  category_l2: string | null;
  count: number;
}

export interface AppSnapshot {
  contract_version: number;
  generated_at: string;
  library: { path: string; database: string; initialized: boolean };
  summary: AppSummary;
  sources: SourceSummary[];
  skills: SkillSummary[];
  filters: { categories: CategoryFilter[]; risks: RiskLevel[] };
  llm: LLMStatus;
  query: string | null;
  capabilities: Record<string, boolean>;
}

export interface ProjectPlan {
  project: string;
  requirement: string;
  target: string;
  recommendations: SkillSummary[];
}

export interface ProjectEntryStatus {
  skill_id: string;
  name: string | null;
  path: string;
  mode: "symlink" | "copy";
  state: "clean" | "missing" | "catalog-missing" | "replaced" | "broken" | "source-drift" | "project-drift";
}

export interface ProjectStatus {
  project: string;
  manifest: string;
  entries: ProjectEntryStatus[];
  clean: boolean;
}

export interface ProjectHistoryEvent {
  id: string;
  action: "apply" | "sync" | "unlink";
  created_at: string;
  count: number;
  skill_ids: string[];
  skill_names: string[];
  requirement?: string | null;
  target?: string;
  modes?: string[];
  force?: boolean;
}

export interface ProjectHistory {
  project: string;
  events: ProjectHistoryEvent[];
}

export interface ProjectSummary {
  id: string;
  path: string;
  display_name: string;
  status: "active" | "missing" | "invalid";
  entry_count: number;
  history_count: number;
  clean: boolean;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
  problem: string | null;
}

export interface CommandFailure {
  message: string;
  type?: string;
  exitCode?: number;
  stderr?: string;
}
