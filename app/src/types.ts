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
  github_stars?: number | null;
  github_metadata_checked_at?: string | null;
  skill_count: number;
  valid_count: number;
  invalid_count: number;
  elevated_risk_count: number;
  pending_evaluation_count: number;
  repository_exists?: boolean;
  reclone_supported?: boolean;
  restorable?: boolean;
}

export interface SourceRemovalEntry {
  skill_id: string;
  name: string | null;
  path: string;
  mode: "symlink" | "copy";
  state: ProjectEntryStatus["state"];
  restores_external: boolean;
}

export interface SourceRemovalProject {
  project_id: string;
  project_path: string;
  display_name: string;
  project_kind: "project" | "system";
  entries: SourceRemovalEntry[];
}

export interface SourceRemovalWarning {
  id: string;
  display_name: string;
  path: string;
  status: string;
  problem: string | null;
}

export interface SourceRemovalPreview {
  source: Pick<SourceSummary, "id" | "name" | "url" | "local_path" | "status" | "head_sha" | "updated_at">;
  skills: Array<{ id: string; name: string; rel_path: string }>;
  references: SourceRemovalProject[];
  inaccessible_projects: SourceRemovalWarning[];
  skill_count: number;
  affected_project_count: number;
  reference_count: number;
  symlink_count: number;
  copy_count: number;
  restore_count: number;
  blocker_count: number;
  repository_retained: true;
  repository_path: string;
  preview_digest: string;
}

export interface SourceRemovalResult {
  removed: true;
  source_id: string;
  source_name: string;
  repository_retained: true;
  repository_path: string;
  cleanup_references: boolean;
  cleaned_project_count: number;
  cleaned_reference_count: number;
  restored_external_count: number;
  kept_reference_count: number;
  cleaned_projects: Array<{
    project_id: string;
    project_path: string;
    display_name: string;
    removed_count: number;
    restored_count: number;
  }>;
  inaccessible_projects: SourceRemovalWarning[];
}

export interface SourceRestoreResult {
  restored: true;
  source: SourceSummary;
  scan: {
    discovered: number;
    valid: number;
    invalid: number;
    critical: number;
  };
}

export interface SourceForgetPreview {
  source: Pick<SourceSummary, "id" | "name" | "url" | "local_path" | "status" | "head_sha" | "updated_at">;
  skills: Array<{
    id: string;
    name: string;
    rel_path: string;
    content_hash: string;
    tree_hash: string;
    updated_at: string;
  }>;
  references: SourceRemovalProject[];
  inaccessible_projects: SourceRemovalWarning[];
  profile_locators: Array<{ profile_id: string; position: number; skill_id: string }>;
  history: {
    annotation_count: number;
    audit_review_count: number;
    evaluation_count: number;
    scan_run_count: number;
  };
  skill_count: number;
  affected_project_count: number;
  reference_count: number;
  profile_locator_count: number;
  blocker_count: number;
  repository_retained: true;
  repository_exists: boolean;
  repository_path: string;
  preview_digest: string;
}

export interface SourceForgetResult {
  forgotten: true;
  source_id: string;
  source_name: string;
  deleted_skill_count: number;
  cleared_profile_locator_count: number;
  deleted_history: SourceForgetPreview["history"];
  repository_retained: true;
  repository_exists: boolean;
  repository_path: string;
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
  previous_score: number | null;
  score_delta: number | null;
  requires_review: boolean;
  name_conflicts: Array<{
    id: string;
    name: string;
    source_name: string;
    score: number | null;
  }>;
  comparison: {
    relation?: "overlap" | "existing_covers";
    matched_skill_id?: string;
    matched_skill_name?: string;
    matched_source_name?: string;
    existing_score?: number;
    coverage?: number;
    matched_capabilities?: string[];
    reason?: string;
  };
  recommendation: "review" | "ignore";
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
  executables: {
    codex: string | null;
    claude: string | null;
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
  recent_errors: LLMEvaluation[];
}

export interface LLMEvaluationRun {
  provider: string;
  model: string | null;
  requested: number;
  proposed: number;
  unchanged: number;
  attention: number;
  failed: number;
  results: LLMEvaluation[];
}

export interface LLMProfileTestResult {
  ok: boolean;
  profile_id: string;
  executable?: string | null;
  endpoint?: string;
  model_count?: number;
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

export interface SourceReconcileResultItem {
  source_id: string;
  source: string;
  status: "scanned" | "failed";
  type?: string;
  error?: string;
  scan?: {
    discovered: number;
    valid: number;
    invalid: number;
    critical: number;
  };
}

export interface SourceReconcileResult {
  discovered: number;
  scanned: number;
  failed: number;
  results: SourceReconcileResultItem[];
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
  source_url?: string | null;
  source_stars?: number | null;
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
  variant_count?: number;
  project_selection_state?: "available" | "installed" | "managed-conflict" | "path-conflict";
  project_entry_state?: ProjectEntryStatus["state"] | null;
  project_entry_skill_id?: string | null;
  project_entry_path?: string | null;
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

export type BootstrapCandidateKind = "local" | "git" | "symlink" | "system" | "provider" | "managed";

export interface BootstrapDefaultRoot {
  id: string;
  label: string;
  path: string;
  exists: boolean;
}

export interface BootstrapRoot {
  path: string;
  exists: boolean;
  candidate_count: number;
  error: string | null;
}

export interface BootstrapCandidate {
  id: string;
  name: string;
  description: string;
  path: string;
  real_path: string;
  root: string;
  kind: BootstrapCandidateKind;
  tree_hash: string;
  file_count: number;
  git_root: string | null;
  git_url: string | null;
  provider: string | null;
  protected_reason: string | null;
  duplicate_of: string | null;
  importable: boolean;
  reason: string;
}

export interface BootstrapDiscovery {
  roots: BootstrapRoot[];
  root_count: number;
  candidate_count: number;
  importable_count: number;
  candidates: BootstrapCandidate[];
}

export interface BootstrapStarter {
  id: string;
  name: string;
  title: string;
  url: string;
  homepage: string;
  license: string;
  maintainer: string;
  description: string;
  installed: boolean;
}

export interface BootstrapStatus {
  default_roots: BootstrapDefaultRoot[];
  starters: BootstrapStarter[];
  local_source: Omit<
    SourceSummary,
    "skill_count" | "valid_count" | "invalid_count" | "elevated_risk_count" | "pending_evaluation_count"
  > | null;
}

export interface BootstrapImportItem {
  path: string;
  status: "imported" | "duplicate" | "failed";
  destination: string | null;
  error: string | null;
}

export interface BootstrapImportResult {
  total: number;
  imported: number;
  skipped: number;
  failed: number;
  results: BootstrapImportItem[];
  scan: Record<string, unknown> | null;
  source: Record<string, unknown>;
}

export interface BootstrapInstallItem {
  id: string;
  status: "installed" | "already-installed" | "failed";
  source: Record<string, unknown> | null;
  scan: Record<string, unknown> | null;
  error: string | null;
}

export interface BootstrapInstallResult {
  total: number;
  installed: number;
  already_installed: number;
  failed: number;
  results: BootstrapInstallItem[];
}

export interface AppSnapshot {
  contract_version: number;
  generated_at: string;
  library: { path: string; database: string; initialized: boolean };
  summary: AppSummary;
  sources: SourceSummary[];
  removed_sources: SourceSummary[];
  skills: SkillSummary[];
  filters: { categories: CategoryFilter[]; risks: RiskLevel[] };
  llm: LLMStatus;
  bootstrap: BootstrapStatus;
  query: string | null;
  capabilities: Record<string, boolean>;
}

export interface ProjectPlan {
  project: string;
  requirement: string;
  discovery_mode: "requirement" | "category";
  category_l1: string | null;
  category_l2: string | null;
  target: string;
  library_root: string;
  recommendations: SkillSummary[];
}

export interface ProjectEntryStatus {
  skill_id: string;
  name: string | null;
  path: string;
  mode: "symlink" | "copy";
  state: "clean" | "missing" | "catalog-missing" | "replaced" | "broken" | "source-drift" | "project-drift";
  restores_external: boolean;
}

export interface ProjectExternalMatch {
  id: string;
  name: string;
  source_name: string;
  audit_severity: RiskLevel;
  valid: boolean;
  content_match: boolean;
  target_path: string;
}

export interface ProjectExternalEntry {
  name: string;
  path: string;
  entry_type: "directory" | "symlink";
  tree_hash: string | null;
  read_only: true;
  management_state: "external" | "provider-owned";
  provider: string | null;
  protected_reason: string | null;
  migratable: boolean;
  migration_mode: "backup-and-link" | "associate-link" | null;
  matches: ProjectExternalMatch[];
}

export interface ProjectStatus {
  project: string;
  manifest: string;
  managed: boolean;
  entries: ProjectEntryStatus[];
  clean: boolean;
  project_kind: "project" | "system";
  system_scope: "agents" | "claude" | "codex" | "cursor" | "gemini" | "opencode" | null;
  protected: boolean;
  external_entries: ProjectExternalEntry[];
}

export interface ProjectHistoryEvent {
  id: string;
  action: "apply" | "adopt" | "sync" | "unlink";
  created_at: string;
  count: number;
  skill_ids: string[];
  skill_names: string[];
  requirement?: string | null;
  target?: string;
  modes?: string[];
  force?: boolean;
  backup_path?: string | null;
  source_path?: string;
  original_entry_type?: "directory" | "symlink";
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
  created_at: string | null;
  updated_at: string | null;
  problem: string | null;
  project_kind: "project" | "system";
  system_scope: "agents" | "claude" | "codex" | "cursor" | "gemini" | "opencode" | null;
  protected: boolean;
  external_count: number;
}

export type AgentTargetId = "agents" | "claude" | "codex" | "cursor" | "gemini" | "opencode";

export interface AgentTarget {
  id: AgentTargetId;
  label: string;
  path: string;
  global_path: string;
  project_path: string;
  exists: boolean;
  aliases: string[];
  preferred_rel_prefixes: string[];
}

export interface ActivationTarget extends AgentTarget {
  status: "available" | "unavailable" | "invalid";
  problem: string | null;
}

export type ActivationState =
  | "managed"
  | "drift"
  | "external-match"
  | "external"
  | "absent"
  | "unavailable";

export interface ActivationCell {
  target_id: AgentTargetId;
  skill_id: string;
  installed_skill_id: string | null;
  adopt_skill_id: string | null;
  content_match: boolean | null;
  source_name: string;
  audit_severity: RiskLevel;
  valid: boolean;
  path: string;
  detail_state: ProjectEntryStatus["state"] | "directory-missing" | string | null;
  read_only: boolean;
  state: ActivationState;
}

export interface ActivationRow {
  name: string;
  description: string;
  variant_count: number;
  cells: ActivationCell[];
}

export interface ActivationMatrix {
  library_root: string;
  query: string;
  limit: number;
  total: number;
  targets: ActivationTarget[];
  rows: ActivationRow[];
}

export interface SkillProfileSummary {
  id: string;
  name: string;
  description: string;
  entry_count: number;
  created_at: string;
  updated_at: string;
}

export interface SkillProfileEntry {
  skill_id: string | null;
  skill_name: string;
  source_name: string | null;
  source_url: string | null;
  rel_path: string | null;
}

export interface SkillProfile extends Omit<SkillProfileSummary, "entry_count"> {
  entries: SkillProfileEntry[];
}

export type SkillProfileAction =
  | "install"
  | "already-installed"
  | "conflict"
  | "unresolved";

export interface SkillProfilePreviewItem extends SkillProfileEntry {
  skill_id: string | null;
  resolved_name: string;
  source_name: string | null;
  action: SkillProfileAction;
  reason: string;
  path: string | null;
  audit_severity?: RiskLevel;
  valid?: boolean;
}

export interface SkillProfilePreview {
  profile: SkillProfile;
  project: string;
  target: string;
  items: SkillProfilePreviewItem[];
  counts: Record<SkillProfileAction, number>;
  can_apply: boolean;
}

export type SkillProfileImportStatus =
  | "exact"
  | "compatible"
  | "ambiguous"
  | "missing";

export interface SkillProfileImportItem
  extends Omit<SkillProfileEntry, "skill_id"> {
  status: SkillProfileImportStatus;
  reason: string;
}

export interface SkillProfileImportPreview {
  schema: "adaptive-skills-profile/1";
  path: string;
  sha256: string;
  profile: {
    name: string;
    description: string;
    entry_count: number;
  };
  items: SkillProfileImportItem[];
  counts: Record<SkillProfileImportStatus, number>;
  action: "create" | "already-exists";
  can_import: boolean;
  existing_profile_id: string | null;
}

export interface SkillProfileExportResult {
  schema: "adaptive-skills-profile/1";
  path: string;
  written: boolean;
  overwritten: boolean;
  bytes: number;
  profile: {
    id: string;
    name: string;
    entry_count: number;
  };
}

export interface SkillProfileImportResult {
  changed: boolean;
  action: "created" | "already-exists";
  sha256: string;
  profile: SkillProfile;
}

export interface CommandFailure {
  message: string;
  type?: string;
  exitCode?: number;
  stderr?: string;
}
