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

export function sourceReconcileArgs(): string[] {
  return ["source", "reconcile"];
}

export function sourceUpdatePolicyArgs(
  sourceId: string,
  policy: "remote" | "local",
): string[] {
  return ["source", "policy", sourceId, policy];
}

export function sourceRemovalPreviewArgs(sourceId: string): string[] {
  return ["source", "remove-preview", sourceId];
}

export function sourceRemoveArgs(
  sourceId: string,
  previewDigest: string,
  cleanupReferences: boolean,
): string[] {
  return [
    "source", "remove", sourceId,
    "--expected-digest", previewDigest,
    ...(!cleanupReferences ? ["--keep-references"] : []),
  ];
}

export function sourceRestoreArgs(sourceId: string): string[] {
  return ["source", "restore", sourceId];
}

export function sourceForgetPreviewArgs(sourceId: string): string[] {
  return ["source", "forget-preview", sourceId];
}

export function sourceForgetArgs(
  sourceId: string,
  previewDigest: string,
): string[] {
  return [
    "source", "forget", sourceId,
    "--expected-digest", previewDigest,
  ];
}

export function bootstrapDiscoverArgs(roots: string[]): string[] {
  return [
    "bootstrap", "discover",
    ...roots.flatMap((root) => ["--root", root]),
  ];
}

export function bootstrapImportArgs(
  candidates: Array<{ path: string; tree_hash: string }>,
): string[] {
  return [
    "bootstrap", "import",
    ...candidates.flatMap((candidate) => [
      "--candidate",
      JSON.stringify({ path: candidate.path, tree_hash: candidate.tree_hash }),
    ]),
  ];
}

export function bootstrapInstallArgs(starterIds: string[]): string[] {
  return [
    "bootstrap", "install",
    ...starterIds.flatMap((id) => ["--starter", id]),
  ];
}

export function auditReviewArgs(
  skillId: string,
  findingId: string,
  status: "reviewed_false_positive" | "confirmed_risk",
  note?: string,
): string[] {
  return [
    "skill", "audit-review", skillId, findingId,
    "--status", status,
    ...(note?.trim() ? ["--note", note.trim()] : []),
  ];
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

export function llmClearErrorsArgs(): string[] {
  return ["llm", "clear-errors"];
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
  categoryL1 = "",
  categoryL2 = "",
): string[] {
  return [
    "project",
    "plan",
    project,
    ...(requirement.trim() ? ["--requirement", requirement.trim()] : []),
    ...(categoryL1.trim() ? ["--category-l1", categoryL1.trim()] : []),
    ...(categoryL2.trim() ? ["--category-l2", categoryL2.trim()] : []),
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

export function projectAdoptArgs(
  project: string,
  entry: string,
  skillId: string,
  allowRisk: boolean,
  replaceContent = false,
): string[] {
  return [
    "project", "adopt", project,
    "--entry", entry,
    "--skill", skillId,
    ...(allowRisk ? ["--allow-risk"] : []),
    ...(replaceContent ? ["--replace-content"] : []),
  ];
}

export function agentTargetArgs(): string[] {
  return ["agent", "list"];
}

export function agentTargetAddArgs(target: {
  id: string;
  name: string;
  globalPath: string;
  detectPath: string;
  projectPath: string;
}): string[] {
  return [
    "agent", "add",
    "--id", target.id.trim(),
    "--name", target.name.trim(),
    "--global-path", target.globalPath.trim(),
    "--detect-path", target.detectPath.trim(),
    "--project-path", target.projectPath.trim(),
  ];
}

export function agentTargetRemoveArgs(targetId: string): string[] {
  return ["agent", "remove", targetId];
}

export function projectMatrixArgs(query = "", limit = 20): string[] {
  return [
    "project", "matrix",
    "--limit", String(limit),
    ...(query.trim() ? ["--query", query.trim()] : []),
  ];
}

export function profileCaptureArgs(
  project: string,
  name: string,
  description = "",
): string[] {
  return [
    "profile", "capture", project,
    "--name", name.trim(),
    ...(description.trim() ? ["--description", description.trim()] : []),
  ];
}

export function profilePreviewArgs(
  profileId: string,
  project: string,
  target: string,
  allowRisk: boolean,
): string[] {
  return [
    "profile", "preview", profileId, project,
    "--target", target,
    ...(allowRisk ? ["--allow-risk"] : []),
  ];
}

export function profileApplyArgs(
  profileId: string,
  project: string,
  target: string,
  allowRisk: boolean,
): string[] {
  return [
    "profile", "apply", profileId, project,
    "--target", target,
    ...(allowRisk ? ["--allow-risk"] : []),
  ];
}

export function profileDeleteArgs(profileId: string): string[] {
  return ["profile", "delete", profileId];
}

export function profileExportArgs(
  profileId: string,
  output: string,
  overwrite = false,
): string[] {
  return [
    "profile", "export", profileId,
    "--output", output,
    ...(overwrite ? ["--overwrite"] : []),
  ];
}

export function profileImportPreviewArgs(input: string): string[] {
  return ["profile", "import-preview", input];
}

export function profileImportArgs(input: string, expectedSha256: string): string[] {
  return [
    "profile", "import", input,
    "--expected-sha256", expectedSha256,
  ];
}
