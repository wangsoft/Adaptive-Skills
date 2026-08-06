import { describe, expect, it } from "vitest";
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
  llmClearErrorsArgs,
  llmConfigArgs,
  llmEvaluateArgs,
  llmProfileSaveArgs,
  llmReviewArgs,
  auditReviewArgs,
} from "./commands";

describe("desktop command contract", () => {
  it("keeps user input as separate process arguments", () => {
    expect(sourceAddArgs("https://example.test/repo with spaces.git", "demo")).toEqual([
      "source", "add", "https://example.test/repo with spaces.git", "--name", "demo",
    ]);
    expect(snapshotArgs("演示文稿")).toEqual([
      "app", "snapshot", "--limit", "100", "--query", "演示文稿",
    ]);
  });

  it("emits one explicit skill flag per selected stable id", () => {
    const args = projectApplyArgs(
      "/tmp/project with spaces",
      ["skill-a", "skill-b"],
      "create documentation",
      "claude",
      false,
    );
    expect(args.filter((value) => value === "--skill")).toHaveLength(2);
    expect(args).toContain("skill-a");
    expect(args).toContain("skill-b");
    expect(args).not.toContain("--allow-risk");
  });

  it("adds the risk override only after explicit acceptance", () => {
    expect(projectPlanArgs("/tmp/project", "install tools", "auto", false)).not.toContain("--allow-risk");
    expect(projectPlanArgs("/tmp/project", "install tools", "auto", true)).toContain("--allow-risk");
  });

  it("uses one explicit command for refreshing every source", () => {
    expect(sourceRefreshAllArgs()).toEqual(["source", "refresh-all"]);
  });

  it("uses one explicit command for discovering manual clones", () => {
    expect(sourceReconcileArgs()).toEqual(["source", "reconcile"]);
  });

  it("keeps source policy changes in separate process arguments", () => {
    expect(sourceUpdatePolicyArgs("source-id", "local")).toEqual([
      "source", "policy", "source-id", "local",
    ]);
  });

  it("requests bounded project history for one explicit project", () => {
    expect(projectHistoryArgs("/tmp/project with spaces", 25)).toEqual([
      "project", "history", "/tmp/project with spaces", "--limit", "25",
    ]);
  });

  it("keeps LLM provider settings and review actions explicit", () => {
    expect(llmConfigArgs("codex", "gpt-test", 240, 5)).toEqual([
      "llm", "config", "set", "--provider", "codex", "--model", "gpt-test",
      "--timeout", "240", "--max-per-run", "5",
    ]);
    expect(llmEvaluateArgs("source-id")).toEqual([
      "llm", "evaluate", "--source", "source-id",
    ]);
    expect(llmClearErrorsArgs()).toEqual(["llm", "clear-errors"]);
    expect(llmReviewArgs("apply", "evaluation-id", true)).toEqual([
      "llm", "apply", "evaluation-id", "--replace-existing",
    ]);
    expect(llmReviewArgs("reject", "evaluation-id")).toEqual([
      "llm", "reject", "evaluation-id",
    ]);
    const compatible = llmProfileSaveArgs({
      id: "office",
      name: "Office model",
      provider: "openai-compatible",
      model: "eval-model",
      baseUrl: "https://llm.example.test/v1",
      apiMode: "chat-completions",
      timeout: 90,
      maxPerRun: 4,
      activate: true,
    });
    expect(compatible).toContain("https://llm.example.test/v1");
    expect(compatible).not.toContain("super-secret-key");
    expect(compatible).not.toContain("--api-key");
  });

  it("keeps audit review evidence and outcome in separate arguments", () => {
    expect(auditReviewArgs(
      "skill-id",
      "finding-id",
      "reviewed_false_positive",
      "documentation example",
    )).toEqual([
      "skill", "audit-review", "skill-id", "finding-id",
      "--status", "reviewed_false_positive",
      "--note", "documentation example",
    ]);
  });

  it("keeps bootstrap discovery roots as separate process arguments", () => {
    expect(bootstrapDiscoverArgs(["/tmp/skills one", "/tmp/skills-two"])).toEqual([
      "bootstrap", "discover", "--root", "/tmp/skills one", "--root", "/tmp/skills-two",
    ]);
  });

  it("serializes bootstrap copy candidates with their reviewed hash", () => {
    const args = bootstrapImportArgs([
      { path: "/tmp/skill one", tree_hash: "hash-one" },
      { path: "/tmp/skill-two", tree_hash: "hash-two" },
    ]);
    expect(args.slice(0, 2)).toEqual(["bootstrap", "import"]);
    expect(args.filter((value) => value === "--candidate")).toHaveLength(2);
    expect(JSON.parse(args[3])).toEqual({ path: "/tmp/skill one", tree_hash: "hash-one" });
  });

  it("emits one explicit flag for every curated starter source", () => {
    expect(bootstrapInstallArgs(["openai-plugins", "superpowers"])).toEqual([
      "bootstrap", "install", "--starter", "openai-plugins", "--starter", "superpowers",
    ]);
  });
});
