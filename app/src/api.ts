import { invoke } from "@tauri-apps/api/core";
import {
  bootstrapDiscoverArgs,
  bootstrapImportArgs,
  bootstrapInstallArgs,
  projectApplyArgs,
  projectHistoryArgs,
  projectPlanArgs,
  snapshotArgs,
  sourceAddArgs,
  sourceReconcileArgs,
  sourceRefreshAllArgs,
  sourceUpdatePolicyArgs,
  llmConfigArgs,
  llmEvaluateArgs,
  llmProfileSaveArgs,
  llmReviewArgs,
  auditReviewArgs,
} from "./commands";
import type {
  AppSnapshot,
  ProjectPlan,
  ProjectHistory,
  ProjectStatus,
  SkillDetail,
  SourceRefreshAllResult,
  SourceReconcileResult,
  SourceUpdatePolicy,
  LLMStatus,
  LLMProfileProvider,
  LLMAPIMode,
  LLMEvaluation,
  LLMEvaluationRun,
  ProjectSummary,
  BootstrapCandidate,
  BootstrapDiscovery,
  BootstrapImportResult,
  BootstrapInstallResult,
} from "./types";

function commandError(error: unknown): Error {
  if (error instanceof Error) return error;
  if (typeof error === "string") {
    try {
      const parsed = JSON.parse(error) as { message?: string; error?: string };
      return new Error(parsed.message || parsed.error || error);
    } catch {
      return new Error(error);
    }
  }
  if (error && typeof error === "object") {
    const value = error as { message?: string; error?: string; stderr?: string };
    return new Error(value.message || value.error || value.stderr || "未知命令错误");
  }
  return new Error("未知命令错误");
}

export async function runCommand<T>(library: string, args: string[]): Promise<T> {
  try {
    return await invoke<T>("run_adaptive_command", { library, args });
  } catch (error) {
    throw commandError(error);
  }
}

export const api = {
  snapshot: (library: string, query?: string) =>
    runCommand<AppSnapshot>(library, snapshotArgs(query)),
  skill: (library: string, id: string) =>
    runCommand<SkillDetail>(library, ["skill", "show", id]),
  reviewAuditFinding: (
    library: string,
    skillId: string,
    findingId: string,
    status: "reviewed_false_positive" | "confirmed_risk",
    note?: string,
  ) => runCommand<SkillDetail>(
    library,
    auditReviewArgs(skillId, findingId, status, note),
  ),
  scan: (library: string, sourceId?: string) =>
    runCommand<unknown[]>(library, ["scan", ...(sourceId ? [sourceId] : [])]),
  bootstrapDiscover: (library: string, roots: string[] = []) =>
    runCommand<BootstrapDiscovery>(library, bootstrapDiscoverArgs(roots)),
  bootstrapImport: async (
    library: string,
    candidates: Array<Pick<BootstrapCandidate, "path" | "tree_hash">>,
  ): Promise<BootstrapImportResult> => {
    const chunks: Array<Array<Pick<BootstrapCandidate, "path" | "tree_hash">>> = [];
    for (let index = 0; index < candidates.length; index += 40) {
      chunks.push(candidates.slice(index, index + 40));
    }
    const parts: BootstrapImportResult[] = [];
    for (const chunk of chunks) {
      parts.push(await runCommand<BootstrapImportResult>(
        library,
        bootstrapImportArgs(chunk),
      ));
    }
    const last = parts.at(-1);
    if (!last) throw new Error("请至少选择一个要复制归集的 Skill");
    return {
      total: parts.reduce((sum, item) => sum + item.total, 0),
      imported: parts.reduce((sum, item) => sum + item.imported, 0),
      skipped: parts.reduce((sum, item) => sum + item.skipped, 0),
      failed: parts.reduce((sum, item) => sum + item.failed, 0),
      results: parts.flatMap((item) => item.results),
      scan: last.scan,
      source: last.source,
    };
  },
  bootstrapInstall: (library: string, starterIds: string[]) =>
    runCommand<BootstrapInstallResult>(
      library,
      bootstrapInstallArgs(starterIds),
    ),
  addSource: (library: string, url: string, name?: string) =>
    runCommand<Record<string, unknown>>(library, sourceAddArgs(url, name)),
  updateSource: (library: string, sourceId: string) =>
    runCommand<Record<string, unknown>>(library, ["source", "update", sourceId]),
  reconcileSources: (library: string) =>
    runCommand<SourceReconcileResult>(library, sourceReconcileArgs()),
  refreshAllSources: (library: string) =>
    runCommand<SourceRefreshAllResult>(library, sourceRefreshAllArgs()),
  setSourcePolicy: (
    library: string,
    sourceId: string,
    policy: SourceUpdatePolicy,
  ) =>
    runCommand<Record<string, unknown>>(
      library,
      sourceUpdatePolicyArgs(sourceId, policy),
    ),
  configureLLM: (
    library: string,
    provider: "disabled" | "codex" | "claude",
    model: string,
    timeout: number,
    maxPerRun: number,
  ) =>
    runCommand<LLMStatus>(
      library,
      llmConfigArgs(provider, model, timeout, maxPerRun),
    ),
  saveLLMProfile: async (
    library: string,
    profile: {
      id: string;
      name: string;
      provider: LLMProfileProvider;
      model: string;
      baseUrl: string;
      apiMode: LLMAPIMode;
      timeout: number;
      maxPerRun: number;
      activate: boolean;
    },
    secret?: string,
  ) => {
    try {
      return await invoke<LLMStatus>("save_llm_profile", {
        library,
        args: llmProfileSaveArgs(profile),
        secret: secret || null,
      });
    } catch (error) {
      throw commandError(error);
    }
  },
  activateLLMProfile: (library: string, profileId: string) =>
    runCommand<LLMStatus>(library, ["llm", "profile", "activate", profileId]),
  deleteLLMProfile: (library: string, profileId: string) =>
    runCommand<LLMStatus>(library, ["llm", "profile", "delete", profileId]),
  testLLMProfile: (library: string, profileId: string) =>
    runCommand<{ ok: boolean; profile_id: string }>(library, [
      "llm", "profile", "test", profileId,
    ]),
  evaluateSource: (library: string, sourceId: string) =>
    runCommand<LLMEvaluationRun>(library, llmEvaluateArgs(sourceId)),
  applyEvaluation: (
    library: string,
    evaluationId: string,
    replaceExisting = false,
  ) =>
    runCommand<LLMEvaluation>(
      library,
      llmReviewArgs("apply", evaluationId, replaceExisting),
    ),
  rejectEvaluation: (library: string, evaluationId: string) =>
    runCommand<LLMEvaluation>(
      library,
      llmReviewArgs("reject", evaluationId),
    ),
  projectPlan: (
    library: string,
    project: string,
    requirement: string,
    target: string,
    allowRisk: boolean,
  ) =>
    runCommand<ProjectPlan>(
      library,
      projectPlanArgs(project, requirement, target, allowRisk),
    ),
  projectApply: (
    library: string,
    project: string,
    skillIds: string[],
    requirement: string,
    target: string,
    allowRisk: boolean,
  ) =>
    runCommand<Record<string, unknown>>(
      library,
      projectApplyArgs(project, skillIds, requirement, target, allowRisk),
    ),
  projectStatus: (library: string, project: string) =>
    runCommand<ProjectStatus>(library, ["project", "status", project]),
  projectHistory: (library: string, project: string, limit = 50) =>
    runCommand<ProjectHistory>(library, projectHistoryArgs(project, limit)),
  projectList: (library: string) =>
    runCommand<ProjectSummary[]>(library, ["project", "list"]),
  projectRegister: (library: string, project: string) =>
    runCommand<ProjectSummary>(library, ["project", "register", project]),
  projectForget: (library: string, projectId: string) =>
    runCommand<{ forgotten: boolean }>(library, ["project", "forget", projectId]),
  projectRelink: (library: string, projectId: string, newPath: string) =>
    runCommand<ProjectSummary>(library, ["project", "relink", projectId, newPath]),
  projectSync: (
    library: string,
    project: string,
    allowRisk: boolean,
    force = false,
  ) =>
    runCommand<Record<string, unknown>>(library, [
      "project",
      "sync",
      project,
      ...(allowRisk ? ["--allow-risk"] : []),
      ...(force ? ["--force"] : []),
    ]),
  projectUnlink: (
    library: string,
    project: string,
    skillIds: string[],
    force = false,
  ) =>
    runCommand<Record<string, unknown>>(library, [
      "project",
      "unlink",
      project,
      ...skillIds.flatMap((id) => ["--skill", id]),
      ...(force ? ["--force"] : []),
    ]),
};
