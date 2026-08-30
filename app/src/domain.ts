import type {
  ActivationState,
  ProjectEntryStatus,
  RiskLevel,
  SkillProfileAction,
  SkillSummary,
} from "./types";
import { getActiveLanguage, translate } from "./i18n";

export const ELEVATED_RISKS = new Set<RiskLevel>(["high", "critical"]);

export function isElevatedRisk(risk: RiskLevel): boolean {
  return ELEVATED_RISKS.has(risk);
}

export function canSelectSkill(skill: SkillSummary, allowRisk: boolean): boolean {
  return (
    (skill.project_selection_state ?? "available") === "available" &&
    skill.valid &&
    (allowRisk || !isElevatedRisk(skill.audit_severity))
  );
}

export function projectSelectionStateLabel(
  state: NonNullable<SkillSummary["project_selection_state"]>,
): string {
  return translate({
    available: "可添加",
    installed: "已添加",
    "managed-conflict": "已添加其他版本",
    "path-conflict": "目标已占用",
  }[state]);
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
  if (!value) return translate("尚未记录", "Not recorded");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(getActiveLanguage() === "en" ? "en-US" : "zh-CN", {
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
  return translate({
    none: "无风险信号",
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    critical: "严重风险",
  }[risk]);
}

export function projectEntryStateLabel(state: ProjectEntryStatus["state"]): string {
  return translate({
    clean: "已同步",
    missing: "链接缺失",
    "catalog-missing": "目录中已不存在",
    replaced: "被其他内容替换",
    broken: "链接已损坏",
    "source-drift": "来源有更新",
    "project-drift": "项目副本有改动",
  }[state]);
}

export function projectEntryRequiresForce(state: ProjectEntryStatus["state"]): boolean {
  return state === "project-drift" || state === "replaced";
}

export function projectEntryCanSync(state: ProjectEntryStatus["state"]): boolean {
  return state !== "clean" && state !== "catalog-missing";
}

export function activationStateLabel(state: ActivationState): string {
  return translate({
    managed: "已安装",
    drift: "有漂移",
    "external-match": "可迁移",
    external: "外部已有",
    absent: "安装",
    unavailable: "未发现目录",
  }[state]);
}

export function profileActionLabel(action: SkillProfileAction): string {
  return translate({
    install: "将安装",
    "already-installed": "已安装",
    conflict: "有冲突",
    unresolved: "未解析",
  }[action]);
}
