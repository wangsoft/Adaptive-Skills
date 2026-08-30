import { describe, expect, it } from "vitest";
import {
  canSelectSkill,
  formatStarCount,
  projectEntryCanSync,
  projectEntryRequiresForce,
  projectEntryStateLabel,
  projectSelectionStateLabel,
  selectedRiskCount,
  activationStateLabel,
  profileActionLabel,
} from "./domain";
import type { SkillSummary } from "./types";

const skill = (overrides: Partial<SkillSummary> = {}): SkillSummary => ({
  id: "skill-1",
  source_name: "source",
  name: "sample-skill",
  description: "Sample",
  rel_path: "skills/sample-skill",
  valid: true,
  audit_severity: "none",
  ...overrides,
});

describe("project skill safety", () => {
  it("formats repository stars without inventing missing values", () => {
    expect(formatStarCount(null)).toBe("—");
    expect(formatStarCount(987)).toBe("987");
    expect(formatStarCount(1_250)).toBe("1.3k");
    expect(formatStarCount(2_000_000)).toBe("2m");
  });

  it("never permits an invalid skill", () => {
    expect(canSelectSkill(skill({ valid: false }), true)).toBe(false);
  });

  it("requires explicit risk acceptance for high and critical skills", () => {
    const risky = skill({ audit_severity: "critical" });
    expect(canSelectSkill(risky, false)).toBe(false);
    expect(canSelectSkill(risky, true)).toBe(true);
  });

  it("never permits a Skill that already occupies the selected project target", () => {
    expect(canSelectSkill(skill({ project_selection_state: "installed" }), true)).toBe(false);
    expect(canSelectSkill(skill({ project_selection_state: "managed-conflict" }), true)).toBe(false);
    expect(canSelectSkill(skill({ project_selection_state: "path-conflict" }), true)).toBe(false);
    expect(projectSelectionStateLabel("installed")).toBe("已添加");
  });

  it("counts elevated selected skills for the confirmation boundary", () => {
    const skills = [
      skill(),
      skill({ id: "skill-2", audit_severity: "high" }),
      skill({ id: "skill-3", audit_severity: "critical" }),
    ];
    expect(selectedRiskCount(skills, new Set(["skill-1", "skill-3"]))).toBe(1);
  });

  it("distinguishes recoverable drift from entries missing in the catalog", () => {
    expect(projectEntryCanSync("source-drift")).toBe(true);
    expect(projectEntryCanSync("catalog-missing")).toBe(false);
    expect(projectEntryRequiresForce("project-drift")).toBe(true);
    expect(projectEntryRequiresForce("replaced")).toBe(true);
    expect(projectEntryRequiresForce("missing")).toBe(false);
    expect(projectEntryStateLabel("broken")).toBe("链接已损坏");
  });

  it("uses explicit labels for activation and profile states", () => {
    expect(activationStateLabel("external")).toBe("外部已有");
    expect(activationStateLabel("external-match")).toBe("可迁移");
    expect(activationStateLabel("unavailable")).toBe("未发现目录");
    expect(profileActionLabel("unresolved")).toBe("未解析");
    expect(profileActionLabel("already-installed")).toBe("已安装");
  });
});
