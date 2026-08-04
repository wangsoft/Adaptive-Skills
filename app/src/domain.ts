import type { ProjectEntryStatus, RiskLevel, SkillSummary } from "./types";

export const ELEVATED_RISKS = new Set<RiskLevel>(["high", "critical"]);

export function isElevatedRisk(risk: RiskLevel): boolean {
  return ELEVATED_RISKS.has(risk);
}

export function canSelectSkill(skill: SkillSummary, allowRisk: boolean): boolean {
  return skill.valid && (allowRisk || !isElevatedRisk(skill.audit_severity));
}

export function selectedRiskCount(
  skills: SkillSummary[],
  selected: ReadonlySet<string>,
): number {
  return skills.filter(
    (skill) => selected.has(skill.id) && isElevatedRisk(skill.audit_severity),
  ).length;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shortSha(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "—";
}

export function riskLabel(risk: RiskLevel): string {
  return {
    none: "无风险信号",
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "严重风险",
  }[risk];
}

export function projectEntryStateLabel(state: ProjectEntryStatus["state"]): string {
  return {
    clean: "已同步",
    missing: "链接缺失",
    "catalog-missing": "目录中已不存在",
    replaced: "被其他内容替换",
    broken: "链接已损坏",
    "source-drift": "来源有更新",
    "project-drift": "项目副本有改动",
  }[state];
}

export function projectEntryRequiresForce(state: ProjectEntryStatus["state"]): boolean {
  return state === "project-drift" || state === "replaced";
}

export function projectEntryCanSync(state: ProjectEntryStatus["state"]): boolean {
  return state !== "clean" && state !== "catalog-missing";
}
