import { describe, expect, it } from "vitest";
import {
  clearSourceDraft,
  loadProjectDraft,
  loadSourceDraft,
  saveProjectDraft,
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
      target: "claude",
      allowRisk: true,
    });

    expect(loadProjectDraft(storage, "/library/a")).toEqual({
      project: "/project/demo",
      requirement: "制作技术方案",
      target: "claude",
      allowRisk: true,
    });
    expect(loadProjectDraft(storage, "/library/b").project).toBe("");
  });

  it("falls back safely when stored data is malformed", () => {
    const storage = new MemoryStorage();
    storage.setItem("adaptive-skills:project-draft:/library", "not-json");
    expect(loadProjectDraft(storage, "/library").target).toBe("auto");
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
});
