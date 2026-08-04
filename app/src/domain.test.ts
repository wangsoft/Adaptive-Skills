import { describe, expect, it } from "vitest";
import { canSelectSkill, selectedRiskCount } from "./domain";
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
  it("never permits an invalid skill", () => {
    expect(canSelectSkill(skill({ valid: false }), true)).toBe(false);
  });

  it("requires explicit risk acceptance for high and critical skills", () => {
    const risky = skill({ audit_severity: "critical" });
    expect(canSelectSkill(risky, false)).toBe(false);
    expect(canSelectSkill(risky, true)).toBe(true);
  });

  it("counts elevated selected skills for the confirmation boundary", () => {
    const skills = [
      skill(),
      skill({ id: "skill-2", audit_severity: "high" }),
      skill({ id: "skill-3", audit_severity: "critical" }),
    ];
    expect(selectedRiskCount(skills, new Set(["skill-1", "skill-3"]))).toBe(1);
  });
});
