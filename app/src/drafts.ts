export type ProjectTarget = "auto" | "codex" | "claude";

export interface ProjectDraft {
  project: string;
  requirement: string;
  target: ProjectTarget;
  allowRisk: boolean;
}

export interface SourceDraft {
  adding: boolean;
  url: string;
  name: string;
}

export const EMPTY_PROJECT_DRAFT: ProjectDraft = {
  project: "",
  requirement: "",
  target: "auto",
  allowRisk: false,
};

export const EMPTY_SOURCE_DRAFT: SourceDraft = {
  adding: false,
  url: "",
  name: "",
};

function key(kind: "project" | "source", library: string): string {
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
  const target = ["auto", "codex", "claude"].includes(String(draft.target))
    ? draft.target as ProjectTarget
    : "auto";
  return {
    project: typeof draft.project === "string" ? draft.project : "",
    requirement: typeof draft.requirement === "string" ? draft.requirement : "",
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
