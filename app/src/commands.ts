export function snapshotArgs(query?: string): string[] {
  return [
    "app",
    "snapshot",
    "--limit",
    query ? "100" : "5000",
    ...(query ? ["--query", query] : []),
  ];
}

export function sourceAddArgs(url: string, name?: string): string[] {
  return ["source", "add", url, ...(name ? ["--name", name] : [])];
}

export function sourceRefreshAllArgs(): string[] {
  return ["source", "refresh-all"];
}

export function sourceUpdatePolicyArgs(
  sourceId: string,
  policy: "remote" | "local",
): string[] {
  return ["source", "policy", sourceId, policy];
}

export function llmConfigArgs(
  provider: "disabled" | "codex" | "claude",
  model: string,
  timeout: number,
  maxPerRun: number,
): string[] {
  return [
    "llm",
    "config",
    "set",
    "--provider",
    provider,
    ...(model.trim() ? ["--model", model.trim()] : []),
    "--timeout",
    String(timeout),
    "--max-per-run",
    String(maxPerRun),
  ];
}

export function llmProfileSaveArgs(profile: {
  id: string;
  name: string;
  provider: "codex" | "claude" | "openai-compatible";
  model: string;
  baseUrl: string;
  apiMode: "responses" | "chat-completions" | "auto";
  timeout: number;
  maxPerRun: number;
  activate: boolean;
}): string[] {
  return [
    "llm", "profile", "save",
    "--id", profile.id.trim(),
    "--name", profile.name.trim(),
    "--provider", profile.provider,
    ...(profile.model.trim() ? ["--model", profile.model.trim()] : []),
    ...(profile.provider === "openai-compatible"
      ? ["--base-url", profile.baseUrl.trim(), "--api-mode", profile.apiMode]
      : []),
    "--timeout", String(profile.timeout),
    "--max-per-run", String(profile.maxPerRun),
    ...(!profile.activate ? ["--no-activate"] : []),
  ];
}

export function llmEvaluateArgs(sourceId: string): string[] {
  return ["llm", "evaluate", "--source", sourceId];
}

export function llmReviewArgs(
  action: "apply" | "reject",
  evaluationId: string,
  replaceExisting = false,
): string[] {
  return [
    "llm",
    action,
    evaluationId,
    ...(action === "apply" && replaceExisting ? ["--replace-existing"] : []),
  ];
}

export function projectPlanArgs(
  project: string,
  requirement: string,
  target: string,
  allowRisk: boolean,
): string[] {
  return [
    "project",
    "plan",
    project,
    "--requirement",
    requirement,
    "--target",
    target,
    "--limit",
    "20",
    ...(allowRisk ? ["--allow-risk"] : []),
  ];
}

export function projectApplyArgs(
  project: string,
  skillIds: string[],
  requirement: string,
  target: string,
  allowRisk: boolean,
): string[] {
  return [
    "project",
    "apply",
    project,
    ...skillIds.flatMap((id) => ["--skill", id]),
    "--target",
    target,
    "--mode",
    "symlink",
    "--requirement",
    requirement,
    ...(allowRisk ? ["--allow-risk"] : []),
  ];
}

export function projectHistoryArgs(project: string, limit = 50): string[] {
  return ["project", "history", project, "--limit", String(limit)];
}
