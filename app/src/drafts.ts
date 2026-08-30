export type ProjectTarget = "auto" | "codex" | "claude" | "root";
export type ProjectDiscoveryMode = "requirement" | "category";
export type DraftLLMProvider = "codex" | "claude" | "openai-compatible";
export type DraftLLMAPIMode = "auto" | "responses" | "chat-completions";

export interface ProjectDraft {
  project: string;
  requirement: string;
  discoveryMode: ProjectDiscoveryMode;
  categoryL1: string;
  categoryL2: string;
  target: ProjectTarget;
  allowRisk: boolean;
}

export interface SourceDraft {
  adding: boolean;
  url: string;
  name: string;
}

export interface SkillFilterDraft {
  query: string;
  risk: string;
  source: string;
  category: string;
}

export interface LLMProfileDraft {
  open: boolean;
  editingId: string | null;
  profileId: string;
  name: string;
  provider: DraftLLMProvider;
  model: string;
  baseUrl: string;
  apiMode: DraftLLMAPIMode;
  timeout: number;
  maxPerRun: number;
}

export interface SourceRefreshHistoryRecord {
  id: string;
  completedAt: string;
  total: number;
  updated: number;
  unchanged: number;
  local: number;
  failed: number;
}

export const EMPTY_PROJECT_DRAFT: ProjectDraft = {
  project: "",
  requirement: "",
  discoveryMode: "requirement",
  categoryL1: "",
  categoryL2: "",
  target: "auto",
  allowRisk: false,
};

export const EMPTY_SOURCE_DRAFT: SourceDraft = {
  adding: false,
  url: "",
  name: "",
};

export const EMPTY_SKILL_FILTER_DRAFT: SkillFilterDraft = {
  query: "",
  risk: "all",
  source: "all",
  category: "all",
};

export const EMPTY_LLM_PROFILE_DRAFT: LLMProfileDraft = {
  open: false,
  editingId: null,
  profileId: "",
  name: "",
  provider: "openai-compatible",
  model: "",
  baseUrl: "https://api.openai.com/v1",
  apiMode: "auto",
  timeout: 300,
  maxPerRun: 20,
};

function key(kind: "project" | "source" | "skills" | "llm" | "source-refresh-history", library: string): string {
  return `adaptive-skills:${kind}-draft:${library}`;
}

function read(storage: Storage, storageKey: string): unknown {
  try {
    const raw = storage.getItem(storageKey);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function write(storage: Storage, storageKey: string, value: unknown): void {
  try {
    storage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // Draft persistence is best-effort and must never break the form itself.
  }
}

export function loadProjectDraft(
  storage: Storage,
  library: string,
): ProjectDraft {
  const value = read(storage, key("project", library));
  if (!value || typeof value !== "object") return { ...EMPTY_PROJECT_DRAFT };
  const draft = value as Partial<ProjectDraft>;
  const target = ["auto", "codex", "claude", "root"].includes(String(draft.target))
    ? draft.target as ProjectTarget
    : "auto";
  const discoveryMode = draft.discoveryMode === "category" ? "category" : "requirement";
  return {
    project: typeof draft.project === "string" ? draft.project : "",
    requirement: typeof draft.requirement === "string" ? draft.requirement : "",
    discoveryMode,
    categoryL1: typeof draft.categoryL1 === "string" ? draft.categoryL1 : "",
    categoryL2: typeof draft.categoryL2 === "string" ? draft.categoryL2 : "",
    target,
    allowRisk: typeof draft.allowRisk === "boolean" ? draft.allowRisk : false,
  };
}

export function saveProjectDraft(
  storage: Storage,
  library: string,
  draft: ProjectDraft,
): void {
  write(storage, key("project", library), draft);
}

export function clearProjectDraft(storage: Storage, library: string): void {
  try {
    storage.removeItem(key("project", library));
  } catch {
    // The in-memory form can still be cleared when storage is unavailable.
  }
}

export function loadSourceDraft(storage: Storage, library: string): SourceDraft {
  const value = read(storage, key("source", library));
  if (!value || typeof value !== "object") return { ...EMPTY_SOURCE_DRAFT };
  const draft = value as Partial<SourceDraft>;
  return {
    adding: typeof draft.adding === "boolean" ? draft.adding : false,
    url: typeof draft.url === "string" ? draft.url : "",
    name: typeof draft.name === "string" ? draft.name : "",
  };
}

export function saveSourceDraft(
  storage: Storage,
  library: string,
  draft: SourceDraft,
): void {
  write(storage, key("source", library), draft);
}

export function clearSourceDraft(storage: Storage, library: string): void {
  try {
    storage.removeItem(key("source", library));
  } catch {
    // The in-memory form can still be cleared when storage is unavailable.
  }
}

export function loadSkillFilterDraft(
  storage: Storage,
  library: string,
): SkillFilterDraft {
  const value = read(storage, key("skills", library));
  if (!value || typeof value !== "object") return { ...EMPTY_SKILL_FILTER_DRAFT };
  const draft = value as Partial<SkillFilterDraft>;
  return {
    query: typeof draft.query === "string" ? draft.query : "",
    risk: typeof draft.risk === "string" ? draft.risk : "all",
    source: typeof draft.source === "string" ? draft.source : "all",
    category: typeof draft.category === "string" ? draft.category : "all",
  };
}

export function saveSkillFilterDraft(
  storage: Storage,
  library: string,
  draft: SkillFilterDraft,
): void {
  write(storage, key("skills", library), draft);
}

export function clearSkillFilterDraft(storage: Storage, library: string): void {
  try {
    storage.removeItem(key("skills", library));
  } catch {
    // The in-memory filters can still be reset when storage is unavailable.
  }
}

export function loadLLMProfileDraft(
  storage: Storage,
  library: string,
): LLMProfileDraft {
  const value = read(storage, key("llm", library));
  if (!value || typeof value !== "object") return { ...EMPTY_LLM_PROFILE_DRAFT };
  const draft = value as Partial<LLMProfileDraft>;
  const provider = ["codex", "claude", "openai-compatible"].includes(String(draft.provider))
    ? draft.provider as DraftLLMProvider
    : EMPTY_LLM_PROFILE_DRAFT.provider;
  const apiMode = ["auto", "responses", "chat-completions"].includes(String(draft.apiMode))
    ? draft.apiMode as DraftLLMAPIMode
    : EMPTY_LLM_PROFILE_DRAFT.apiMode;
  const timeout = Number.isFinite(draft.timeout) && Number(draft.timeout) >= 30 && Number(draft.timeout) <= 1800
    ? Number(draft.timeout)
    : EMPTY_LLM_PROFILE_DRAFT.timeout;
  const maxPerRun = Number.isFinite(draft.maxPerRun) && Number(draft.maxPerRun) >= 1 && Number(draft.maxPerRun) <= 100
    ? Number(draft.maxPerRun)
    : EMPTY_LLM_PROFILE_DRAFT.maxPerRun;
  return {
    open: typeof draft.open === "boolean" ? draft.open : false,
    editingId: typeof draft.editingId === "string" && draft.editingId ? draft.editingId : null,
    profileId: typeof draft.profileId === "string" ? draft.profileId : "",
    name: typeof draft.name === "string" ? draft.name : "",
    provider,
    model: typeof draft.model === "string" ? draft.model : "",
    baseUrl: typeof draft.baseUrl === "string" && draft.baseUrl
      ? draft.baseUrl
      : EMPTY_LLM_PROFILE_DRAFT.baseUrl,
    apiMode,
    timeout,
    maxPerRun,
  };
}

export function saveLLMProfileDraft(
  storage: Storage,
  library: string,
  draft: LLMProfileDraft,
): void {
  write(storage, key("llm", library), draft);
}

export function clearLLMProfileDraft(storage: Storage, library: string): void {
  try {
    storage.removeItem(key("llm", library));
  } catch {
    // The in-memory form can still be reset when storage is unavailable.
  }
}

export function hasLLMProfileDraft(draft: LLMProfileDraft): boolean {
  return Boolean(
    draft.editingId || draft.profileId || draft.name || draft.model ||
    draft.provider !== EMPTY_LLM_PROFILE_DRAFT.provider ||
    draft.baseUrl !== EMPTY_LLM_PROFILE_DRAFT.baseUrl ||
    draft.apiMode !== EMPTY_LLM_PROFILE_DRAFT.apiMode ||
    draft.timeout !== EMPTY_LLM_PROFILE_DRAFT.timeout ||
    draft.maxPerRun !== EMPTY_LLM_PROFILE_DRAFT.maxPerRun
  );
}

export function loadSourceRefreshHistory(
  storage: Storage,
  library: string,
): SourceRefreshHistoryRecord[] {
  const value = read(storage, key("source-refresh-history", library));
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Partial<SourceRefreshHistoryRecord>;
    const counts = [record.total, record.updated, record.unchanged, record.local, record.failed];
    if (
      typeof record.id !== "string" || typeof record.completedAt !== "string" ||
      counts.some((count) => !Number.isInteger(count) || Number(count) < 0)
    ) return [];
    return [{
      id: record.id,
      completedAt: record.completedAt,
      total: Number(record.total),
      updated: Number(record.updated),
      unchanged: Number(record.unchanged),
      local: Number(record.local),
      failed: Number(record.failed),
    }];
  }).slice(0, 10);
}

export function recordSourceRefresh(
  storage: Storage,
  library: string,
  result: Pick<SourceRefreshHistoryRecord, "total" | "updated" | "unchanged" | "local" | "failed">,
  completedAt = new Date().toISOString(),
): SourceRefreshHistoryRecord[] {
  const record: SourceRefreshHistoryRecord = {
    id: `${completedAt}-${Math.random().toString(36).slice(2, 10)}`,
    completedAt,
    ...result,
  };
  const history = [record, ...loadSourceRefreshHistory(storage, library)].slice(0, 10);
  write(storage, key("source-refresh-history", library), history);
  return history;
}
