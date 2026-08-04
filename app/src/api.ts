import { invoke } from "@tauri-apps/api/core";
import {
  projectApplyArgs,
  projectHistoryArgs,
  projectPlanArgs,
  snapshotArgs,
  sourceAddArgs,
  sourceRefreshAllArgs,
  sourceUpdatePolicyArgs,
  llmConfigArgs,
  llmEvaluateArgs,
  llmProfileSaveArgs,
  llmReviewArgs,
} from "./commands";
import type {
  AppSnapshot,
  ProjectPlan,
  ProjectHistory,
  ProjectStatus,
  SkillDetail,
  SourceRefreshAllResult,
  SourceUpdatePolicy,
  LLMStatus,
  LLMProfileProvider,
  LLMAPIMode,
  LLMEvaluation,
  LLMEvaluationRun,
  ProjectSummary,
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
  scan: (library: string, sourceId?: string) =>
    runCommand<unknown[]>(library, ["scan", ...(sourceId ? [sourceId] : [])]),
  addSource: (library: string, url: string, name?: string) =>
    runCommand<Record<string, unknown>>(library, sourceAddArgs(url, name)),
  updateSource: (library: string, sourceId: string) =>
    runCommand<Record<string, unknown>>(library, ["source", "update", sourceId]),
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
