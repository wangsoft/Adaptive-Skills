import { describe, expect, it } from "vitest";
import {
  clearLLMProfileDraft,
  clearSkillFilterDraft,
  clearSourceDraft,
  hasLLMProfileDraft,
  loadLLMProfileDraft,
  loadProjectDraft,
  loadSourceRefreshHistory,
  loadSkillFilterDraft,
  loadSourceDraft,
  saveLLMProfileDraft,
  saveProjectDraft,
  recordSourceRefresh,
  saveSkillFilterDraft,
  saveSourceDraft,
} from "./drafts";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return Array.from(this.values.keys())[index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, value); }
}

describe("navigation-safe form drafts", () => {
  it("restores project fields only within the same library", () => {
    const storage = new MemoryStorage();
    saveProjectDraft(storage, "/library/a", {
      project: "/project/demo",
      requirement: "制作技术方案",
      discoveryMode: "category",
      categoryL1: "演示与文档",
      categoryL2: "演示文稿",
      target: "claude",
      allowRisk: true,
    });

    expect(loadProjectDraft(storage, "/library/a")).toEqual({
      project: "/project/demo",
      requirement: "制作技术方案",
      discoveryMode: "category",
      categoryL1: "演示与文档",
      categoryL2: "演示文稿",
      target: "claude",
      allowRisk: true,
    });
    expect(loadProjectDraft(storage, "/library/b").project).toBe("");
  });

  it("falls back safely when stored data is malformed", () => {
    const storage = new MemoryStorage();
    storage.setItem("adaptive-skills:project-draft:/library", "not-json");
    expect(loadProjectDraft(storage, "/library").target).toBe("auto");
    expect(loadProjectDraft(storage, "/library").discoveryMode).toBe("requirement");
  });

  it("restores and clears an in-progress source form", () => {
    const storage = new MemoryStorage();
    saveSourceDraft(storage, "/library", {
      adding: true,
      url: "https://example.test/skills.git",
      name: "example",
    });
    expect(loadSourceDraft(storage, "/library").adding).toBe(true);
    clearSourceDraft(storage, "/library");
    expect(loadSourceDraft(storage, "/library")).toEqual({
      adding: false,
      url: "",
      name: "",
    });
  });

  it("restores skill filters and can reset them", () => {
    const storage = new MemoryStorage();
    saveSkillFilterDraft(storage, "/library", {
      query: "制作技术演示",
      risk: "low",
      source: "presentations",
      category: "演示与文档",
    });
    expect(loadSkillFilterDraft(storage, "/library")).toEqual({
      query: "制作技术演示",
      risk: "low",
      source: "presentations",
      category: "演示与文档",
    });
    clearSkillFilterDraft(storage, "/library");
    expect(loadSkillFilterDraft(storage, "/library").risk).toBe("all");
  });

  it("restores an LLM connection draft without ever storing a secret", () => {
    const storage = new MemoryStorage();
    saveLLMProfileDraft(storage, "/library", {
      open: true,
      editingId: null,
      profileId: "company-model",
      name: "公司模型",
      provider: "openai-compatible",
      model: "company-gpt",
      baseUrl: "https://llm.example.test/v1",
      apiMode: "chat-completions",
      timeout: 600,
      maxPerRun: 12,
    });

    const restored = loadLLMProfileDraft(storage, "/library");
    expect(restored.profileId).toBe("company-model");
    expect(restored.apiMode).toBe("chat-completions");
    expect(hasLLMProfileDraft(restored)).toBe(true);
    expect(storage.getItem("adaptive-skills:llm-draft:/library")).not.toContain("apiKey");

    clearLLMProfileDraft(storage, "/library");
    expect(hasLLMProfileDraft(loadLLMProfileDraft(storage, "/library"))).toBe(false);
  });

  it("keeps a bounded source refresh history", () => {
    const storage = new MemoryStorage();
    for (let index = 0; index < 12; index += 1) {
      recordSourceRefresh(storage, "/library", {
        total: 23,
        updated: index,
        unchanged: 20,
        local: 2,
        failed: 1,
      }, `2026-08-05T00:00:${String(index).padStart(2, "0")}Z`);
    }
    const history = loadSourceRefreshHistory(storage, "/library");
    expect(history).toHaveLength(10);
    expect(history[0].updated).toBe(11);
    expect(storage.getItem("adaptive-skills:source-refresh-history-draft:/library")).not.toContain("error");
  });

  it("preserves failure details after reopening and after another update", () => {
    const storage = new MemoryStorage();
    const result = {
      total: 1, updated: 0, unchanged: 0, local: 0, failed: 1,
      results: [{ source_id: "repo-1", source: "demo", status: "failed" as const,
        before_sha: null, type: "ValidationError", error: "Could not resolve host: github.com" }],
    };
    recordSourceRefresh(storage, "/library", result);
    expect(loadSourceRefreshHistory(storage, "/library")[0]).toMatchObject({
      failures: [{ source_id: "repo-1", source: "demo", error: "Could not resolve host: github.com" }],
    });
    recordSourceRefresh(storage, "/library", { total: 1, updated: 0, unchanged: 1, local: 0, failed: 0 });
    expect(loadSourceRefreshHistory(storage, "/library")[1]).toMatchObject({
      failures: [{ source_id: "repo-1" }],
    });
    expect(loadSourceRefreshHistory(storage, "/other-library")).toEqual([]);
  });

  it("recovers legacy raw details and rewrites them without raw credentials", () => {
    const storage = new MemoryStorage();
    const key = "adaptive-skills:source-refresh-history-draft:/library";
    storage.setItem(key, JSON.stringify([
      { id: "raw", completedAt: "2026-10-18T03:04:00Z", total: 1, updated: 0, unchanged: 0, local: 0, failed: 1,
        results: [{ source_id: "demo", source: "demo", status: "failed", error: "fatal: https://user:private-pass@example.test/repo", secret: "private-extra" }] },
      { id: "summary", completedAt: "2026-09-06T15:35:00Z", total: 1, updated: 0, unchanged: 0, local: 0, failed: 1 },
      { id: "broken", failed: -1 },
    ]));
    const records = loadSourceRefreshHistory(storage, "/library");
    expect(records).toHaveLength(2);
    expect(records[0].failures?.[0].error).toBe("fatal: https://[redacted]@example.test/repo");
    expect(records[1].failures).toBeUndefined();
    expect(storage.getItem(key)).not.toContain("private");
    expect(storage.getItem(key)).not.toContain('"results"');
  });

  it("redacts new history before storage and never persists unrelated result fields", () => {
    const storage = new MemoryStorage();
    const result = { total: 1, updated: 0, unchanged: 0, local: 0, failed: 1,
      credential: "private-extra",
      results: [{ source_id: "demo", source: "demo", status: "failed", error: "https://user:private-pass@example.test/repo" }],
    };
    recordSourceRefresh(storage, "/library", result);
    expect(storage.getItem("adaptive-skills:source-refresh-history-draft:/library")).not.toContain("private");
    expect(loadSourceRefreshHistory(storage, "/library")[0].failures).toHaveLength(1);
  });
});
