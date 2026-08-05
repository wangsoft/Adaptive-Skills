import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleGauge,
  Database,
  ExternalLink,
  FolderGit2,
  FolderOpen,
  GitBranch,
  History,
  Layers3,
  Link2,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  Unlink,
  X,
} from "lucide-react";
import { api } from "./api";
import {
  canSelectSkill,
  formatDate,
  isElevatedRisk,
  projectEntryCanSync,
  projectEntryRequiresForce,
  projectEntryStateLabel,
  riskLabel,
  selectedRiskCount,
  shortSha,
} from "./domain";
import {
  clearLLMProfileDraft,
  clearProjectDraft,
  clearSkillFilterDraft,
  clearSourceDraft,
  EMPTY_LLM_PROFILE_DRAFT,
  EMPTY_PROJECT_DRAFT,
  hasLLMProfileDraft,
  loadLLMProfileDraft,
  loadProjectDraft,
  loadSourceRefreshHistory,
  loadSkillFilterDraft,
  loadSourceDraft,
  recordSourceRefresh,
  saveLLMProfileDraft,
  saveProjectDraft,
  saveSkillFilterDraft,
  saveSourceDraft,
} from "./drafts";
import type { ProjectDraft } from "./drafts";
import type {
  AppSnapshot,
  ProjectEntryStatus,
  ProjectPlan,
  ProjectHistoryEvent,
  ProjectStatus,
  RiskLevel,
  SkillDetail,
  SkillSummary,
  SourceRefreshAllResult,
  SourceSummary,
  SourceUpdatePolicy,
  LLMProfile,
  LLMProfileProvider,
  LLMAPIMode,
  ProjectSummary,
  AuditFinding,
  AuditReviewStatus,
  ValidationFinding,
  BootstrapCandidate,
  BootstrapDiscovery,
  BootstrapImportResult,
  BootstrapInstallResult,
  BootstrapStatus,
} from "./types";

type View = "overview" | "bootstrap" | "skills" | "sources" | "projects" | "evaluation";

const DEFAULT_LIBRARY = "~/skills";
const LAST_VIEW_KEY = "adaptive-skills:last-view";

const NAV_ITEMS: Array<{
  id: View;
  label: string;
  description: string;
  icon: typeof CircleGauge;
}> = [
  { id: "overview", label: "概览", description: "目录健康与风险", icon: CircleGauge },
  { id: "bootstrap", label: "初始化", description: "发现与归集 Skills", icon: Search },
  { id: "skills", label: "Skills", description: "筛选与审查", icon: Layers3 },
  { id: "sources", label: "来源", description: "Git 仓库生命周期", icon: FolderGit2 },
  { id: "evaluation", label: "LLM 评测", description: "分类、评分与审核", icon: Sparkles },
  { id: "projects", label: "项目", description: "按需挂载 Skills", icon: Link2 },
];

function loadLastView(): View {
  try {
    const value = localStorage.getItem(LAST_VIEW_KEY);
    return NAV_ITEMS.some((item) => item.id === value) ? value as View : "overview";
  } catch {
    return "overview";
  }
}

function useEscapeKey(active: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!active) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onEscape();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [active, onEscape]);
}

function App() {
  const [library, setLibrary] = useState(
    () => localStorage.getItem("adaptive-skills-library") || DEFAULT_LIBRARY,
  );
  const [view, setView] = useState<View>(loadLastView);
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const didAutoOpenBootstrap = useRef(false);

  const loadSnapshot = useCallback(
    async (query?: string) => {
      setLoading(true);
      setError(null);
      try {
        const value = await api.snapshot(library, query);
        setSnapshot(value);
        if (!didAutoOpenBootstrap.current && value.summary.source_count === 0) {
          didAutoOpenBootstrap.current = true;
          setView("bootstrap");
        }
        if (value.library.path !== library) setLibrary(value.library.path);
        localStorage.setItem("adaptive-skills-library", value.library.path);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoading(false);
      }
    },
    [library],
  );

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    try { localStorage.setItem(LAST_VIEW_KEY, view); } catch { /* best-effort */ }
  }, [view]);

  const navigate = (nextView: View) => {
    setDetail(null);
    setView(nextView);
  };
  useEscapeKey(Boolean(detail), () => setDetail(null));

  const chooseLibrary = async () => {
    const selected = await open({ directory: true, multiple: false, title: "选择 Skills 目录" });
    if (typeof selected === "string") setLibrary(selected);
  };

  const showSkill = async (skill: SkillSummary) => {
    setDetailLoading(true);
    setError(null);
    try {
      setDetail(await api.skill(library, skill.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDetailLoading(false);
    }
  };

  const reviewAuditFinding = async (
    findingId: string,
    status: Extract<AuditReviewStatus, "reviewed_false_positive" | "confirmed_risk">,
  ) => {
    if (!detail) return;
    setBusy(`audit-review-${findingId}`);
    setError(null);
    try {
      const updated = await api.reviewAuditFinding(
        library,
        detail.id,
        findingId,
        status,
      );
      setDetail(updated);
      await loadSnapshot();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  };

  const runAction = async (label: string, action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(label);
    setError(null);
    try {
      await action();
      await loadSnapshot();
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const refreshAllSources = async (): Promise<SourceRefreshAllResult | null> => {
    setBusy("refresh-all");
    setError(null);
    try {
      const result = await api.refreshAllSources(library);
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const addAndScanSource = (url: string, name?: string): Promise<boolean> =>
    runAction("source-add", async () => {
      const added = await api.addSource(library, url, name);
      const sourceId = added.id;
      if (typeof sourceId !== "string" || !sourceId) {
        throw new Error("新来源没有返回可扫描的稳定 ID");
      }
      await api.scan(library, sourceId);
    });

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Sparkles size={18} /></div>
          <div><strong>Adaptive Skills</strong><span>Local skill intelligence</span></div>
        </div>

        <nav className="nav-list" aria-label="主导航">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`nav-item ${view === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => navigate(item.id)}
                aria-current={view === item.id ? "page" : undefined}
              >
                <Icon size={19} />
                <span><strong>{item.label}</strong><small>{item.description}</small></span>
                <ChevronRight size={15} className="nav-chevron" />
              </button>
            );
          })}
        </nav>

        <div className="library-card">
          <div className="eyebrow"><Database size={13} /> 当前目录</div>
          <p title={library}>{library}</p>
          <button className="text-button" onClick={chooseLibrary}>
            <FolderOpen size={14} /> 更换目录
          </button>
        </div>

        <div className="sidebar-footer">
          <span className={`status-dot ${error ? "error" : loading ? "loading" : "ready"}`} />
          {error ? "连接异常" : loading ? "正在读取目录" : `契约 v${snapshot?.contract_version ?? "—"}`}
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div>
            <span className="page-kicker">{NAV_ITEMS.find((item) => item.id === view)?.description}</span>
            <h1>{NAV_ITEMS.find((item) => item.id === view)?.label}</h1>
          </div>
          <button
            className="icon-button"
            title="刷新目录"
            aria-label="刷新目录"
            disabled={loading || Boolean(busy)}
            onClick={() => void loadSnapshot()}
          >
            <RefreshCw size={18} className={loading ? "spin" : ""} />
          </button>
        </header>

        {error && (
          <div className="error-banner" role="alert">
            <AlertTriangle size={18} />
            <div><strong>操作没有完成</strong><p>{error}</p></div>
            <button onClick={() => setError(null)} aria-label="关闭错误"><X size={16} /></button>
          </div>
        )}

        {loading && !snapshot ? (
          <LoadingState />
        ) : snapshot ? (
          <div className="view-container">
            {view === "overview" && <Overview snapshot={snapshot} onNavigate={navigate} />}
            {view === "bootstrap" && (
              <BootstrapView
                key={library}
                library={library}
                status={snapshot.bootstrap}
                onRefresh={loadSnapshot}
                onError={setError}
              />
            )}
            {view === "skills" && (
              <SkillsView
                key={library}
                snapshot={snapshot}
                onSearch={loadSnapshot}
                onReset={() => loadSnapshot()}
                onOpen={showSkill}
                detailLoading={detailLoading}
              />
            )}
            {view === "sources" && (
              <SourcesView
                key={library}
                library={library}
                sources={snapshot.sources}
                busy={busy}
                onAdd={addAndScanSource}
                onScan={(id) => runAction(`scan-${id}`, () => api.scan(library, id))}
                onRefreshAll={refreshAllSources}
                onSetPolicy={(id, policy) =>
                  runAction(`policy-${id}`, () => api.setSourcePolicy(library, id, policy))
                }
                onUpdate={(id) =>
                  runAction(`update-${id}`, async () => {
                    await api.updateSource(library, id);
                    await api.scan(library, id);
                  })
                }
                llmEnabled={snapshot.llm.config.provider !== "disabled"}
                onEvaluate={(id) =>
                  runAction(`evaluate-${id}`, () => api.evaluateSource(library, id))
                }
              />
            )}
            {view === "evaluation" && (
              <EvaluationView
                key={`${library}-${snapshot.llm.config.active_profile_id || "disabled"}-${snapshot.llm.config.profiles.length}`}
                snapshot={snapshot}
                busy={busy}
                onSaveProfile={(profile, secret) =>
                  runAction("llm-profile-save", () =>
                    api.saveLLMProfile(library, profile, secret),
                  )
                }
                onActivate={(id) => runAction("llm-profile-activate", () => api.activateLLMProfile(library, id))}
                onDisable={() => runAction("llm-profile-disable", () => api.configureLLM(library, "disabled", "", 300, 20))}
                onDelete={(id) => runAction("llm-profile-delete", () => api.deleteLLMProfile(library, id))}
                onTest={(id) => runAction("llm-profile-test", () => api.testLLMProfile(library, id))}
                onEvaluate={(id) =>
                  runAction(`evaluate-${id}`, () => api.evaluateSource(library, id))
                }
                onApply={(id, replaceExisting) =>
                  runAction(`evaluation-apply-${id}`, () =>
                    api.applyEvaluation(library, id, replaceExisting),
                  )
                }
                onReject={(id) =>
                  runAction(`evaluation-reject-${id}`, () =>
                    api.rejectEvaluation(library, id),
                  )
                }
              />
            )}
            {view === "projects" && <ProjectsView key={library} library={library} onError={setError} />}
          </div>
        ) : (
          <EmptyConnection library={library} onChoose={chooseLibrary} onRetry={() => loadSnapshot()} />
        )}
      </main>

      {detail && <SkillDrawer skill={detail} busy={Boolean(busy)} onReview={reviewAuditFinding} onClose={() => setDetail(null)} />}
      {busy && <ActivityToast label={busy} />}
    </div>
  );
}

function LoadingState() {
  return (
    <div className="center-state">
      <LoaderCircle className="spin" size={28} />
      <strong>正在连接本地目录</strong>
      <span>读取 SQLite 目录，不会执行第三方 Skill。</span>
    </div>
  );
}

function EmptyConnection({ library, onChoose, onRetry }: { library: string; onChoose: () => void; onRetry: () => void }) {
  return (
    <div className="center-state empty">
      <Database size={32} />
      <strong>尚未连接 Skills 目录</strong>
      <span>{library}</span>
      <div className="button-row">
        <button className="button secondary" onClick={onChoose}><FolderOpen size={16} />选择目录</button>
        <button className="button primary" onClick={onRetry}><RefreshCw size={16} />重新连接</button>
      </div>
    </div>
  );
}

const BOOTSTRAP_KIND_LABEL: Record<BootstrapCandidate["kind"], string> = {
  local: "本地",
  git: "Git 工作区",
  symlink: "软链接",
  system: "系统内置",
  managed: "已管理",
};

function BootstrapView({ library, status, onRefresh, onError }: {
  library: string;
  status: BootstrapStatus;
  onRefresh: () => Promise<void>;
  onError: (message: string | null) => void;
}) {
  const defaultRoots = status.default_roots.filter((root) => root.exists).map((root) => root.path);
  const [extraRoots, setExtraRoots] = useState<string[]>([]);
  const [discovery, setDiscovery] = useState<BootstrapDiscovery | null>(null);
  const [selectedCandidates, setSelectedCandidates] = useState<Set<string>>(new Set());
  const [selectedStarters, setSelectedStarters] = useState<Set<string>>(new Set());
  const [working, setWorking] = useState<"discover" | "import" | "install" | null>(null);
  const [importResult, setImportResult] = useState<BootstrapImportResult | null>(null);
  const [installResult, setInstallResult] = useState<BootstrapInstallResult | null>(null);

  const scanRoots = async (customRoots = extraRoots, preserveImportResult = false) => {
    setWorking("discover");
    if (!preserveImportResult) setImportResult(null);
    onError(null);
    try {
      const roots = customRoots.length ? Array.from(new Set([...defaultRoots, ...customRoots])) : [];
      const result = await api.bootstrapDiscover(library, roots);
      setDiscovery(result);
      setSelectedCandidates(new Set(
        result.candidates.filter((candidate) => candidate.importable).map((candidate) => candidate.id),
      ));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  const chooseScanRoots = async () => {
    const selected = await open({ directory: true, multiple: true, title: "选择要扫描的 Skill 目录" });
    const paths = typeof selected === "string" ? [selected] : selected || [];
    if (!paths.length) return;
    const next = Array.from(new Set([...extraRoots, ...paths]));
    setExtraRoots(next);
    await scanRoots(next);
  };

  const toggleCandidate = (id: string) => {
    setSelectedCandidates((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const importSelected = async () => {
    if (!discovery || !selectedCandidates.size) return;
    const candidates = discovery.candidates.filter((candidate) => selectedCandidates.has(candidate.id));
    setWorking("import");
    onError(null);
    try {
      const result = await api.bootstrapImport(library, candidates);
      setImportResult(result);
      await onRefresh();
      await scanRoots(extraRoots, true);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  const toggleStarter = (id: string) => {
    setSelectedStarters((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const installSelected = async () => {
    if (!selectedStarters.size) return;
    const accepted = window.confirm(
      `将从 GitHub 克隆 ${selectedStarters.size} 个第三方仓库到 ${library}，随后只做静态扫描，不会执行其中的 Skill。是否继续？`,
    );
    if (!accepted) return;
    setWorking("install");
    setInstallResult(null);
    onError(null);
    try {
      const result = await api.bootstrapInstall(library, Array.from(selectedStarters));
      setInstallResult(result);
      setSelectedStarters(new Set());
      await onRefresh();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(null);
    }
  };

  const importableIds = discovery?.candidates.filter((candidate) => candidate.importable).map((candidate) => candidate.id) || [];
  const allImportableSelected = Boolean(importableIds.length) && importableIds.every((id) => selectedCandidates.has(id));

  return (
    <div className="bootstrap-page stack gap-lg">
      <section className="panel bootstrap-hero">
        <div>
          <div className="eyebrow"><Layers3 size={14} /> First-run repository builder</div>
          <h2>快速构建你的本地 Skills 仓库</h2>
          <p>扫描常用 Agent 目录，预览并复制归集可迁移的 Skills；原目录不会被移动、删除或改写。</p>
          <div className="bootstrap-steps" aria-label="初始化流程">
            <span>1 扫描目录</span><ChevronRight size={14} /><span>2 审核候选</span><ChevronRight size={14} /><span>3 复制或 Clone</span><ChevronRight size={14} /><span>4 静态扫描</span>
          </div>
        </div>
        <div className="bootstrap-hero-actions">
          <button className="button primary" disabled={Boolean(working)} onClick={() => void scanRoots()}>
            {working === "discover" ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            扫描常用目录
          </button>
          <button className="button secondary" disabled={Boolean(working)} onClick={() => void chooseScanRoots()}>
            <FolderOpen size={16} />添加扫描目录
          </button>
        </div>
      </section>

      <section className="panel bootstrap-roots">
        <div className="panel-heading">
          <div><span className="eyebrow">Discovery scope</span><h3>扫描范围</h3></div>
          <span className="badge neutral">只读发现</span>
        </div>
        <div className="bootstrap-root-list">
          {status.default_roots.map((root) => (
            <div className={`bootstrap-root ${root.exists ? "available" : "missing"}`} key={root.id}>
              <FolderOpen size={15} /><div><strong>{root.label}</strong><span>{root.path}</span></div>
              <span className={`badge ${root.exists ? "success" : "neutral"}`}>{root.exists ? "可扫描" : "不存在"}</span>
            </div>
          ))}
          {extraRoots.map((path) => (
            <div className="bootstrap-root available" key={path}>
              <Plus size={15} /><div><strong>自定义目录</strong><span>{path}</span></div><span className="badge success">已加入</span>
            </div>
          ))}
        </div>
      </section>

      {discovery && (
        <section className="panel bootstrap-discovery">
          <div className="panel-heading">
            <div><span className="eyebrow">Local candidates</span><h3>本地候选</h3><p>发现 {discovery.candidate_count} 个，{discovery.importable_count} 个可复制归集。</p></div>
            <div className="button-row">
              <button className="text-button" disabled={!importableIds.length} onClick={() => setSelectedCandidates(new Set(allImportableSelected ? [] : importableIds))}>{allImportableSelected ? "取消全选" : "选择全部可导入"}</button>
              <button className="button primary compact" disabled={Boolean(working) || !selectedCandidates.size} onClick={() => void importSelected()}>
                {working === "import" ? <LoaderCircle className="spin" size={15} /> : <Layers3 size={15} />}
                复制归集 {selectedCandidates.size}
              </button>
            </div>
          </div>
          {discovery.roots.some((root) => root.error) && (
            <div className="bootstrap-notice warning"><AlertTriangle size={16} /><span>{discovery.roots.filter((root) => root.error).map((root) => `${root.path}：${root.error}`).join("；")}</span></div>
          )}
          <div className="bootstrap-candidate-list">
            {discovery.candidates.map((candidate) => (
              <label className={`bootstrap-candidate ${candidate.importable ? "" : "disabled"}`} key={candidate.id}>
                <input type="checkbox" checked={selectedCandidates.has(candidate.id)} disabled={!candidate.importable || Boolean(working)} onChange={() => toggleCandidate(candidate.id)} />
                <div><strong>{candidate.name}</strong><p>{candidate.description || candidate.path}</p><small title={candidate.path}>{candidate.path} · {candidate.file_count} 个文件</small></div>
                <span className="badge neutral">{BOOTSTRAP_KIND_LABEL[candidate.kind]}</span>
                <span className={candidate.importable ? "bootstrap-reason ready" : "bootstrap-reason"}>{candidate.reason}</span>
              </label>
            ))}
            {!discovery.candidates.length && <div className="empty-inline"><Search size={22} /><strong>没有发现 SKILL.md</strong><span>可以添加其他目录后重新扫描。</span></div>}
          </div>
          {importResult && (
            <div className={`bootstrap-notice ${importResult.failed ? "warning" : "success"}`} role="status">
              {importResult.failed ? <AlertTriangle size={16} /> : <Check size={16} />}
              <span>复制完成：{importResult.imported} 个已导入，{importResult.skipped} 个重复跳过，{importResult.failed} 个失败。原目录保持不变。</span>
            </div>
          )}
          {importResult?.failed ? (
            <div className="bootstrap-result-details">
              {importResult.results.filter((item) => item.status === "failed").map((item) => (
                <p key={item.path}><strong>{item.path}</strong><span>{item.error || "复制失败"}</span></p>
              ))}
            </div>
          ) : null}
        </section>
      )}

      <section className="panel bootstrap-starters">
        <div className="panel-heading">
          <div><span className="eyebrow">Curated Git sources</span><h3>可选的起步仓库</h3><p>仅在你确认后联网 Clone，随后进入同一套 SQLite 目录和安全扫描。</p></div>
          <button className="button secondary compact" disabled={Boolean(working) || !selectedStarters.size} onClick={() => void installSelected()}>
            {working === "install" ? <LoaderCircle className="spin" size={15} /> : <GitBranch size={15} />}
            安装 {selectedStarters.size || "所选"}
          </button>
        </div>
        <div className="bootstrap-notice"><ShieldCheck size={16} /><span>第三方内容按不可信输入处理。部分仓库采用混合许可，使用前仍需查看各目录的许可证。</span></div>
        <div className="bootstrap-starter-list">
          {status.starters.map((starter) => (
            <label className={`bootstrap-starter ${starter.installed ? "installed" : ""}`} key={starter.id}>
              <input type="checkbox" checked={selectedStarters.has(starter.id)} disabled={starter.installed || Boolean(working)} onChange={() => toggleStarter(starter.id)} />
              <div><strong>{starter.title}</strong><p>{starter.description}</p><small>{starter.maintainer} · {starter.license} · {starter.url}</small></div>
              <span className={`badge ${starter.installed ? "success" : "neutral"}`}>{starter.installed ? "已安装" : "可选"}</span>
            </label>
          ))}
        </div>
        {installResult && (
          <div className={`bootstrap-notice ${installResult.failed ? "warning" : "success"}`} role="status">
            {installResult.failed ? <AlertTriangle size={16} /> : <Check size={16} />}
            <span>Git 来源处理完成：{installResult.installed} 个已安装，{installResult.already_installed} 个已存在，{installResult.failed} 个失败。</span>
          </div>
        )}
        {installResult?.failed ? (
          <div className="bootstrap-result-details">
            {installResult.results.filter((item) => item.status === "failed").map((item) => (
              <p key={item.id}><strong>{status.starters.find((starter) => starter.id === item.id)?.title || item.id}</strong><span>{item.error || "安装失败"}</span></p>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function Overview({ snapshot, onNavigate }: { snapshot: AppSnapshot; onNavigate: (view: View) => void }) {
  const { summary } = snapshot;
  const safePercent = summary.skill_count ? Math.round((summary.valid_count / summary.skill_count) * 100) : 0;
  const elevated = summary.risk_counts.high + summary.risk_counts.critical;
  const cards = [
    { label: "收录 Skills", value: summary.skill_count, note: `${summary.annotated_count} 条人工标注`, icon: Layers3, tone: "mint" },
    { label: "Git 来源", value: summary.source_count, note: `最后扫描 ${formatDate(summary.last_scanned_at)}`, icon: FolderGit2, tone: "blue" },
    { label: "有效率", value: `${safePercent}%`, note: `${summary.invalid_count} 个需要修复`, icon: ShieldCheck, tone: "green" },
    { label: "高风险信号", value: elevated, note: `${summary.risk_counts.critical} 个严重风险`, icon: ShieldAlert, tone: "amber" },
  ];
  return (
    <div className="stack gap-xl">
      <section className="hero-panel">
        <div>
          <div className="eyebrow"><Sparkles size={14} /> 本地优先 · 按项目加载</div>
          <h2>让每个项目只看到真正需要的 Skills。</h2>
          <p>目录、风险和人工分类保留在本地。先解释推荐，再由你确认创建项目软链接。</p>
          <button className="button primary" onClick={() => onNavigate("projects")}>初始化项目 Skills <ArrowRight size={16} /></button>
        </div>
        <div className="hero-orbit" aria-hidden="true">
          <div className="orbit-ring ring-one" />
          <div className="orbit-ring ring-two" />
          <div className="orbit-core"><Layers3 size={25} /></div>
          <span className="orbit-node node-one" />
          <span className="orbit-node node-two" />
          <span className="orbit-node node-three" />
        </div>
      </section>

      <section className="metric-grid">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <article className="metric-card" key={card.label}>
              <div className={`metric-icon ${card.tone}`}><Icon size={18} /></div>
              <span>{card.label}</span><strong>{card.value}</strong><small>{card.note}</small>
            </article>
          );
        })}
      </section>

      <section className="dashboard-grid">
        <article className="panel risk-panel">
          <div className="panel-heading"><div><span className="eyebrow">Risk posture</span><h3>风险分布</h3></div><button className="text-button" onClick={() => onNavigate("skills")}>查看 Skills <ArrowRight size={14} /></button></div>
          <div className="risk-bars">
            {(["none", "low", "medium", "high", "critical"] as RiskLevel[]).map((risk) => {
              const count = summary.risk_counts[risk];
              const width = summary.skill_count ? Math.max((count / summary.skill_count) * 100, count ? 2 : 0) : 0;
              return (
                <div className="risk-row" key={risk}>
                  <span>{riskLabel(risk)}</span><div className="bar-track"><i className={`bar risk-${risk}`} style={{ width: `${width}%` }} /></div><strong>{count}</strong>
                </div>
              );
            })}
          </div>
        </article>

        <article className="panel sources-preview">
          <div className="panel-heading"><div><span className="eyebrow">Recent sources</span><h3>来源状态</h3></div><button className="text-button" onClick={() => onNavigate("sources")}>管理来源 <ArrowRight size={14} /></button></div>
          <div className="compact-list">
            {snapshot.sources.slice(0, 5).map((source) => (
              <div className="compact-row" key={source.id}>
                <div className="source-avatar">{source.name.slice(0, 2).toUpperCase()}</div>
                <div><strong>{source.name}</strong><span>{source.skill_count} skills · {shortSha(source.head_sha)}</span></div>
                <span className={source.invalid_count ? "badge warning" : "badge success"}>{source.invalid_count ? `${source.invalid_count} 异常` : "健康"}</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    </div>
  );
}

function SkillsView({ snapshot, onSearch, onReset, onOpen, detailLoading }: {
  snapshot: AppSnapshot;
  onSearch: (query?: string) => Promise<void>;
  onReset: () => Promise<void>;
  onOpen: (skill: SkillSummary) => void;
  detailLoading: boolean;
}) {
  const library = snapshot.library.path;
  const initialDraft = useMemo(() => loadSkillFilterDraft(localStorage, library), [library]);
  const categories = Array.from(new Set(snapshot.filters.categories.map((item) => item.category_l1).filter(Boolean))) as string[];
  const [query, setQuery] = useState(initialDraft.query || snapshot.query || "");
  const [risk, setRisk] = useState<string>(snapshot.filters.risks.includes(initialDraft.risk as RiskLevel) ? initialDraft.risk : "all");
  const [source, setSource] = useState(snapshot.sources.some((item) => item.name === initialDraft.source) ? initialDraft.source : "all");
  const [category, setCategory] = useState(categories.includes(initialDraft.category) ? initialDraft.category : "all");
  useEffect(() => {
    saveSkillFilterDraft(localStorage, library, { query, risk, source, category });
  }, [library, query, risk, source, category]);
  const filtered = useMemo(
    () => snapshot.skills.filter((skill) =>
      (risk === "all" || skill.audit_severity === risk) &&
      (source === "all" || skill.source_name === source) &&
      (category === "all" || skill.category_l1 === category),
    ),
    [snapshot.skills, risk, source, category],
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void (query.trim() ? onSearch(query.trim()) : onReset());
  };
  const resetFilters = () => {
    setQuery(""); setRisk("all"); setSource("all"); setCategory("all");
    clearSkillFilterDraft(localStorage, library);
    void onReset();
  };

  return (
    <div className="stack gap-lg">
      <section className="panel filter-panel">
        <form className="search-field" onSubmit={submit}>
          <Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="描述项目需求，或搜索 Skill 名称…" />
          {query && <button type="button" className="clear-search" aria-label="清空检索" onClick={() => { setQuery(""); if (snapshot.query) void onReset(); }}><X size={15} /></button>}
          <button className="button primary compact" type="submit">需求检索</button>
        </form>
        <div className="filter-row">
          <FilterSelect label="风险" value={risk} onChange={setRisk} options={[{ value: "all", label: "全部风险" }, ...snapshot.filters.risks.map((value) => ({ value, label: riskLabel(value) }))]} />
          <FilterSelect label="来源" value={source} onChange={setSource} options={[{ value: "all", label: "全部来源" }, ...snapshot.sources.map((item) => ({ value: item.name, label: item.name }))]} />
          <FilterSelect label="分类" value={category} onChange={setCategory} options={[{ value: "all", label: "全部分类" }, ...categories.map((value) => ({ value, label: value }))]} />
          {(query || risk !== "all" || source !== "all" || category !== "all") && <button type="button" className="text-button" onClick={resetFilters}>重置筛选</button>}
          <span className="result-count">显示 {filtered.length} / {snapshot.summary.skill_count}</span>
        </div>
      </section>

      {snapshot.query && (
        <div className="query-note"><Sparkles size={16} /><span>正在展示“{snapshot.query}”的解释型匹配结果；高风险 Skill 默认已排除。</span></div>
      )}

      <section className="skill-grid">
        {filtered.map((skill) => (
          <button className="skill-card" key={skill.id} onClick={() => onOpen(skill)} disabled={detailLoading}>
            <div className="skill-card-top">
              <div className="skill-icon"><BookOpen size={18} /></div>
              <div className="skill-badges"><span className={`badge risk-${skill.audit_severity}`}>{riskLabel(skill.audit_severity)}</span>{(skill.format_issue_count ?? 0) > 0 && <span className="badge warning">格式 {skill.format_issue_count}</span>}{Boolean(skill.capability_hint_count) && <span className="badge neutral">能力提示 {skill.capability_hint_count}</span>}</div>
            </div>
            <div><h3>{skill.name}</h3><p>{skill.description || "暂无描述"}</p></div>
            {skill.reason?.length ? <div className="reason-line"><Sparkles size={13} />匹配 {skill.reason.slice(0, 2).map((item) => item.field).join("、")}</div> : null}
            <div className="skill-meta"><span>{skill.category_l1 || "未分类"}{skill.category_l2 ? ` / ${skill.category_l2}` : ""}</span><span>{skill.source_name}</span></div>
          </button>
        ))}
      </section>
      {!filtered.length && <div className="empty-inline"><Search size={24} /><strong>没有匹配的 Skill</strong><span>尝试调整风险、来源或分类筛选。</span></div>}
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="select-field"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>;
}

function SourcesView({ library, sources, busy, onAdd, onScan, onUpdate, onRefreshAll, onSetPolicy, llmEnabled, onEvaluate }: {
  library: string;
  sources: SourceSummary[];
  busy: string | null;
  onAdd: (url: string, name?: string) => Promise<boolean>;
  onScan: (id: string) => Promise<boolean>;
  onUpdate: (id: string) => Promise<boolean>;
  onRefreshAll: () => Promise<SourceRefreshAllResult | null>;
  onSetPolicy: (id: string, policy: SourceUpdatePolicy) => Promise<boolean>;
  llmEnabled: boolean;
  onEvaluate: (id: string) => Promise<boolean>;
}) {
  const initialDraft = useMemo(() => loadSourceDraft(localStorage, library), [library]);
  const [adding, setAdding] = useState(initialDraft.adding);
  const [url, setUrl] = useState(initialDraft.url);
  const [name, setName] = useState(initialDraft.name);
  const [refreshResult, setRefreshResult] = useState<SourceRefreshAllResult | null>(null);
  const [refreshHistory, setRefreshHistory] = useState(() => loadSourceRefreshHistory(localStorage, library));
  useEffect(() => {
    saveSourceDraft(localStorage, library, { adding, url, name });
  }, [library, adding, url, name]);
  const clearAddDraft = () => {
    setAdding(false); setUrl(""); setName("");
    clearSourceDraft(localStorage, library);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!url.trim()) return;
    const completed = await onAdd(url.trim(), name.trim() || undefined);
    if (!completed) return;
    clearAddDraft();
  };
  const refreshAll = async () => {
    setRefreshResult(null);
    const result = await onRefreshAll();
    if (result) {
      setRefreshResult(result);
      setRefreshHistory(recordSourceRefresh(localStorage, library, result));
    }
  };
  const refreshFailures = refreshResult?.results.filter((item) => item.status === "failed") ?? [];
  const evaluateSource = (source: SourceSummary) => {
    if (!source.pending_evaluation_count || !llmEnabled) return;
    const confirmed = window.confirm(
      `将使用已配置的大模型评测 ${source.name}。当前有 ${source.pending_evaluation_count} 个待处理 Skill，本次按配置上限执行，可能消耗模型额度。是否继续？`,
    );
    if (confirmed) void onEvaluate(source.id);
  };
  return (
    <div className="stack gap-lg">
      <div className="section-toolbar">
        <div><h2>{sources.length} 个来源</h2><p>远程 Git 只接受 fast-forward；本地归集来源仅重新扫描。</p></div>
        <div className="button-row">
          <button className="button secondary" disabled={Boolean(busy) || !sources.length} onClick={() => void refreshAll()}>
            {busy === "refresh-all" ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
            {busy === "refresh-all" ? "正在更新全部来源…" : "全部更新"}
          </button>
          <button className="button primary" disabled={Boolean(busy)} onClick={() => setAdding((value) => !value)}><Plus size={16} />添加 Git 来源</button>
        </div>
      </div>
      {refreshResult && (
        <div className={`refresh-summary ${refreshResult.failed ? "with-failures" : ""}`} role="status">
          <div className="refresh-summary-heading">
            {refreshResult.failed ? <AlertTriangle size={18} /> : <Check size={18} />}
            <div>
              <strong>全部来源更新完成</strong>
              <p>已检查 {refreshResult.total} 个来源 · {refreshResult.updated} 个已更新 · {refreshResult.unchanged} 个无变化 · {refreshResult.local} 个本地保留 · {refreshResult.failed} 个失败</p>
            </div>
          </div>
          {refreshFailures.length > 0 && (
            <div className="refresh-failures">
              {refreshFailures.map((item) => <p key={item.source_id}><strong>{item.source}</strong><span>{item.error || "更新失败"}{item.type === "ConflictError" ? "；可保留改动并设为本地维护" : ""}</span></p>)}
            </div>
          )}
        </div>
      )}
      {refreshHistory.length > 0 && (
        <section className="panel source-refresh-history">
          <div className="panel-heading"><div><span className="eyebrow">Local activity</span><h3>最近全部更新记录</h3></div><span className="badge neutral">保留 {refreshHistory.length} 次</span></div>
          <div className="source-refresh-history-list">{refreshHistory.map((record) => <div className="source-refresh-history-row" key={record.id}><History size={14} /><time>{formatDate(record.completedAt)}</time><span>{record.updated} 更新</span><span>{record.unchanged} 无变化</span><span>{record.local} 本地保留</span><span className={record.failed ? "failed" : "success"}>{record.failed} 失败</span></div>)}</div>
          <p>这里只保存计数摘要，不保存仓库错误文本或凭据；完整状态仍以本地目录和 SQLite 目录为准。</p>
        </section>
      )}
      {adding && (
        <form className="panel add-source" onSubmit={(event) => void submit(event)}>
          <div><span className="eyebrow">Clone, scan and queue</span><h3>添加远程 Skill 仓库</h3></div>
          <label><span>Git 地址</span><input required value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://github.com/owner/skills.git" /></label>
          <label><span>显示名称（可选）</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="自动从地址推导" /></label>
          <div className="button-row"><button type="button" className="button ghost" onClick={clearAddDraft}>取消并清空</button><button className="button primary" disabled={busy === "source-add"}>{busy === "source-add" ? <LoaderCircle className="spin" size={16} /> : <GitBranch size={16} />}Clone、扫描并入队</button></div>
        </form>
      )}
      <section className="source-grid">
        {sources.map((source) => (
          <article className="source-card" key={source.id}>
            <div className="source-card-heading"><div className="source-avatar large">{source.name.slice(0, 2).toUpperCase()}</div><div><h3>{source.name}</h3><p>{source.url || "本地归集目录"}</p></div><div className="source-badges"><span className="badge neutral">{source.url ? (source.update_policy === "local" ? "本地维护" : "远程跟随") : "本地归集"}</span><span className={source.invalid_count ? "badge warning" : "badge success"}>{source.invalid_count ? "需检查" : "健康"}</span></div></div>
            <div className="source-stat-row"><div><span>Skills</span><strong>{source.skill_count}</strong></div><div><span>有效</span><strong>{source.valid_count}</strong></div><div><span>待评测</span><strong>{source.pending_evaluation_count}</strong></div></div>
            <div className="source-path" title={source.local_path}><FolderGit2 size={14} />{source.local_path}</div>
            <div className="source-footer"><span>{source.url ? <><GitBranch size={14} />{source.tracked_ref || "当前分支"} · {shortSha(source.head_sha)}</> : <><Layers3 size={14} />本地副本 · 不拉取</>}</span><span>{formatDate(source.last_scanned_at)}</span></div>
            <div className="source-actions">
              {source.url && <button className="button ghost compact" disabled={Boolean(busy)} onClick={() => void onSetPolicy(source.id, source.update_policy === "local" ? "remote" : "local")} title={source.update_policy === "local" ? "恢复自动拉取；工作区仍需保持干净" : "保留本地改动，全部更新时只扫描、不拉取"}>{busy === `policy-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Settings2 size={15} />}{source.update_policy === "local" ? "改为远程跟随" : "设为本地维护"}</button>}
              <button className="button secondary compact" disabled={Boolean(busy)} onClick={() => void onScan(source.id)}>{busy === `scan-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重新扫描</button>
              {source.pending_evaluation_count > 0 && <button className="button secondary compact" disabled={Boolean(busy) || !llmEnabled} onClick={() => evaluateSource(source)} title={llmEnabled ? "生成分类和质量评分提案" : "先在 LLM 评测页面配置模型"}>{busy === `evaluate-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{llmEnabled ? `评测 ${source.pending_evaluation_count}` : "配置 LLM"}</button>}
              {source.url && source.update_policy === "remote" && <button className="button primary compact" disabled={Boolean(busy)} onClick={() => void onUpdate(source.id)}>{busy === `update-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <GitBranch size={15} />}更新并扫描</button>}
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}

interface LLMProfileFormValue {
  id: string;
  name: string;
  provider: LLMProfileProvider;
  model: string;
  baseUrl: string;
  apiMode: LLMAPIMode;
  timeout: number;
  maxPerRun: number;
  activate: boolean;
}

function EvaluationView({ snapshot, busy, onSaveProfile, onActivate, onDisable, onDelete, onTest, onEvaluate, onApply, onReject }: {
  snapshot: AppSnapshot;
  busy: string | null;
  onSaveProfile: (profile: LLMProfileFormValue, secret?: string) => Promise<boolean>;
  onActivate: (profileId: string) => Promise<boolean>;
  onDisable: () => Promise<boolean>;
  onDelete: (profileId: string) => Promise<boolean>;
  onTest: (profileId: string) => Promise<boolean>;
  onEvaluate: (sourceId: string) => Promise<boolean>;
  onApply: (evaluationId: string, replaceExisting: boolean) => Promise<boolean>;
  onReject: (evaluationId: string) => Promise<boolean>;
}) {
  const current = snapshot.llm.config;
  const library = snapshot.library.path;
  const storedDraft = useMemo(() => loadLLMProfileDraft(localStorage, library), [library]);
  const initialDraft = storedDraft.editingId && !current.profiles.some((profile) => profile.id === storedDraft.editingId)
    ? { ...EMPTY_LLM_PROFILE_DRAFT }
    : storedDraft;
  const [showForm, setShowForm] = useState(current.profiles.length === 0 || initialDraft.open);
  const [editingId, setEditingId] = useState<string | null>(initialDraft.editingId);
  const [profileId, setProfileId] = useState(initialDraft.profileId);
  const [name, setName] = useState(initialDraft.name);
  const [provider, setProvider] = useState<LLMProfileProvider>(initialDraft.provider);
  const [model, setModel] = useState(initialDraft.model);
  const [baseUrl, setBaseUrl] = useState(initialDraft.baseUrl);
  const [apiMode, setApiMode] = useState<LLMAPIMode>(initialDraft.apiMode);
  const [apiKey, setApiKey] = useState("");
  const [timeout, setTimeoutValue] = useState(initialDraft.timeout);
  const [maxPerRun, setMaxPerRun] = useState(initialDraft.maxPerRun);
  const pendingSources = snapshot.sources.filter((source) => source.pending_evaluation_count > 0);
  const active = snapshot.llm.active_profile;
  const profileDraft = { open: showForm, editingId, profileId, name, provider, model, baseUrl, apiMode, timeout, maxPerRun };
  const hasDraft = hasLLMProfileDraft(profileDraft);

  useEffect(() => {
    saveLLMProfileDraft(localStorage, library, profileDraft);
  }, [library, showForm, editingId, profileId, name, provider, model, baseUrl, apiMode, timeout, maxPerRun]);

  const resetForm = () => {
    setEditingId(null);
    setProfileId(""); setName(""); setProvider("openai-compatible"); setModel("");
    setBaseUrl("https://api.openai.com/v1"); setApiMode("auto"); setApiKey("");
    setTimeoutValue(300); setMaxPerRun(20);
  };
  const discardForm = () => {
    clearLLMProfileDraft(localStorage, library);
    resetForm(); setApiKey(""); setShowForm(current.profiles.length === 0);
  };
  const editProfile = (profile: LLMProfile) => {
    setEditingId(profile.id);
    setProfileId(profile.id); setName(profile.name); setProvider(profile.provider);
    setModel(profile.model || ""); setBaseUrl(profile.base_url || "https://api.openai.com/v1");
    setApiMode(profile.api_mode || "auto"); setApiKey("");
    setTimeoutValue(profile.timeout_seconds); setMaxPerRun(profile.max_per_run); setShowForm(true);
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const completed = await onSaveProfile({
      id: profileId, name, provider, model, baseUrl, apiMode, timeout, maxPerRun, activate: true,
    }, apiKey.trim() || undefined);
    if (completed) {
      clearLLMProfileDraft(localStorage, library);
      resetForm(); setApiKey(""); setShowForm(false);
    }
  };
  const evaluate = (source: SourceSummary) => {
    const confirmed = window.confirm(
      `将调用 ${active?.name || "当前模型"} 评测 ${source.name}。单次最多处理 ${current.max_per_run} 个 Skill，可能消耗模型额度。是否继续？`,
    );
    if (confirmed) void onEvaluate(source.id);
  };
  const apply = (evaluationId: string, replaceExisting: boolean) => {
    if (replaceExisting && !window.confirm("此操作会替换现有人工或 Arena 整理结果。确认继续？")) return;
    void onApply(evaluationId, replaceExisting);
  };

  return (
    <div className="stack gap-lg evaluation-page">
      <section className="panel evaluator-settings">
        <div className="panel-heading"><div><span className="eyebrow">Provider profiles</span><h3>模型连接</h3></div><div className="button-row"><button className="button ghost compact" disabled={!active || Boolean(busy)} onClick={() => void onDisable()}>暂停评测</button><button className="button primary compact" disabled={Boolean(busy)} onClick={() => { if (!hasDraft) resetForm(); setShowForm(true); }}><Plus size={15} />{hasDraft ? "继续未保存连接" : "添加连接"}</button></div></div>
        <p className="muted">支持 Codex CLI、Claude Code 和 OpenAI-compatible API。API Key 只写入系统凭据库，不进入目录配置、命令参数或评测记录。</p>
        <div className="llm-profile-list">
          {current.profiles.map((profile) => {
            const selected = current.active_profile_id === profile.id;
            const available = snapshot.llm.availability[profile.provider];
            return <article className={`llm-profile-card ${selected ? "active" : ""}`} key={profile.id}>
              <div><span className="eyebrow">{profile.provider}</span><h4>{profile.name}</h4><p>{profile.model || "默认模型"}{profile.base_url ? ` · ${profile.base_url}` : ""}</p></div>
              <div className="llm-profile-state"><span className={selected ? "badge success" : "badge neutral"}>{selected ? "当前使用" : available ? "可用" : "未检测到"}</span>{profile.provider === "openai-compatible" && <small>{profile.credential_configured ? "已配置凭据" : "无凭据 / 本地服务"}</small>}</div>
              <div className="profile-actions"><button className="text-button" disabled={Boolean(busy)} onClick={() => editProfile(profile)}>编辑</button>{profile.provider === "openai-compatible" && <button className="text-button" disabled={Boolean(busy)} onClick={() => { if (!window.confirm("连接测试会访问该服务的 /models 接口，是否继续？")) return; void onTest(profile.id).then((ok) => { if (ok) window.alert("连接测试通过"); }); }}>测试</button>}{!selected && <button className="text-button" disabled={Boolean(busy)} onClick={() => void onActivate(profile.id)}>启用</button>}<button className="text-button danger" disabled={Boolean(busy)} onClick={() => { if (window.confirm(`删除模型连接“${profile.name}”？项目评测记录会保留。`)) void onDelete(profile.id); }}><Trash2 size={13} />删除</button></div>
            </article>;
          })}
          {!current.profiles.length && <div className="history-empty"><Settings2 size={20} /><span>还没有模型连接。添加一个连接后才能对新 Skill 生成分类和评分提案。</span></div>}
        </div>
        {showForm && <form className="evaluation-profile-form" onSubmit={(event) => void save(event)}>
          <div className="profile-form-heading"><div><strong>{editingId ? "编辑连接" : "新建连接"}</strong><small>非密钥字段会自动保存为本地草稿；API Key 切换页面后需重新输入。</small></div><div className="button-row"><button type="button" className="text-button danger" onClick={discardForm}>清空草稿</button><button type="button" className="icon-button" aria-label="收起连接表单" onClick={() => setShowForm(false)}><X size={15} /></button></div></div>
          <label className="input-field"><span>连接 ID</span><input required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,63}" value={profileId} disabled={Boolean(editingId)} onChange={(event) => setProfileId(event.target.value)} placeholder="office-model" /></label>
          <label className="input-field"><span>显示名称</span><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="公司模型" /></label>
          <label className="input-field"><span>连接方式</span><select value={provider} disabled={Boolean(editingId)} onChange={(event) => setProvider(event.target.value as LLMProfileProvider)}><option value="openai-compatible">OpenAI-compatible API</option><option value="codex">Codex CLI</option><option value="claude">Claude Code</option></select></label>
          <label className="input-field"><span>模型{provider === "openai-compatible" ? "" : "（可选）"}</span><input required={provider === "openai-compatible"} value={model} onChange={(event) => setModel(event.target.value)} placeholder={provider === "openai-compatible" ? "gpt-5.2 / company-model" : "留空使用 CLI 默认模型"} /></label>
          {provider === "openai-compatible" && <><label className="input-field wide-field"><span>Base URL</span><input required type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" /></label><label className="input-field"><span>API 模式</span><select value={apiMode} onChange={(event) => setApiMode(event.target.value as LLMAPIMode)}><option value="auto">自动（OpenAI 用 Responses）</option><option value="responses">Responses API</option><option value="chat-completions">Chat Completions</option></select></label><label className="input-field"><span>API Key（留空则保留）</span><input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="仅写入系统凭据库" /></label></>}
          <label className="input-field"><span>超时秒数</span><input type="number" min={30} max={1800} value={timeout} onChange={(event) => setTimeoutValue(Number(event.target.value))} /></label>
          <label className="input-field"><span>单次最多 Skills</span><input type="number" min={1} max={100} value={maxPerRun} onChange={(event) => setMaxPerRun(Number(event.target.value))} /></label>
          <button className="button primary" disabled={Boolean(busy)}>{busy === "llm-profile-save" ? <LoaderCircle className="spin" size={16} /> : <Settings2 size={16} />}保存并启用</button>
        </form>}
      </section>

      <section className="evaluation-kpis">
        <div className="panel evaluation-kpi"><span>固定一级分类</span><strong>{snapshot.llm.taxonomy.level_one.length}</strong><small>{snapshot.llm.taxonomy.version}</small></div>
        <div className="panel evaluation-kpi"><span>待模型评测</span><strong>{snapshot.llm.pending_count}</strong><small>新 Skill 或内容已变化</small></div>
        <div className="panel evaluation-kpi"><span>待人工审核</span><strong>{snapshot.llm.proposal_count}</strong><small>不会自动覆盖正式整理</small></div>
      </section>

      <section className="panel taxonomy-policy">
        <div className="panel-heading"><div><span className="eyebrow">Taxonomy governance</span><h3>固定主干，可控扩展</h3></div></div>
        <p>一级分类采用版本化的 15 类公共主干；二级分类优先复用当前库中重复出现的词表，模型只有在确实无法归类时才能提出“新分类候选”；个性差异放在自由标签中。</p>
        <div className="taxonomy-chips">{snapshot.llm.taxonomy.level_one.map((category) => <span key={category}>{category}</span>)}</div>
      </section>

      {pendingSources.length > 0 && (
        <section className="panel pending-sources">
          <div className="panel-heading"><div><span className="eyebrow">Evaluation queue</span><h3>按来源评测</h3></div></div>
          <div className="pending-source-list">{pendingSources.map((source) => <div className="pending-source-row" key={source.id}><div><strong>{source.name}</strong><span>{source.pending_evaluation_count} 个待评测 Skill</span></div><button className="button secondary compact" disabled={Boolean(busy) || current.provider === "disabled"} onClick={() => evaluate(source)}>{busy === `evaluate-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}开始评测</button></div>)}</div>
        </section>
      )}

      <section className="panel proposal-panel">
        <div className="panel-heading"><div><span className="eyebrow">Human review gate</span><h3>评测提案</h3></div><span className="badge neutral">{snapshot.llm.proposals.length} 项</span></div>
        {snapshot.llm.proposals.length ? <div className="proposal-list">{snapshot.llm.proposals.map((proposal) => <article className="proposal-card" key={proposal.id}><div className="proposal-heading"><div><strong>{proposal.skill_name}</strong><span>{proposal.source_name} · {proposal.provider}{proposal.model ? `/${proposal.model}` : ""}</span></div><div className="proposal-score"><strong>{proposal.score != null ? proposal.score.toFixed(1) : "—"}</strong><small>/ 10 质量分</small></div></div><div className="proposal-category"><span>{proposal.category_l1}</span><ArrowRight size={13} /><span>{proposal.category_l2}</span>{proposal.category_candidate && <i className="badge warning">新二级分类候选</i>}</div><p>{proposal.problem}</p><small>{proposal.use_case}</small><div className="proposal-actions"><button className="button ghost compact" disabled={Boolean(busy)} onClick={() => void onReject(proposal.id)}>{busy === `evaluation-reject-${proposal.id}` ? <LoaderCircle className="spin" size={14} /> : <X size={14} />}拒绝</button><button className="button primary compact" disabled={Boolean(busy) || !proposal.current_content} onClick={() => apply(proposal.id, proposal.has_annotation)}>{busy === `evaluation-apply-${proposal.id}` ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}{proposal.has_annotation ? "替换现有整理" : "应用提案"}</button></div></article>)}</div> : <div className="history-empty"><Sparkles size={20} /><span>还没有待审核的 LLM 评测提案。</span></div>}
      </section>
    </div>
  );
}

function projectHistoryLabel(event: ProjectHistoryEvent): string {
  const labels = { apply: "应用 Skills", sync: "同步来源变更", unlink: "移除 Skills" };
  return `${labels[event.action]} · ${event.count} 项`;
}

function ProjectsView({ library, onError }: { library: string; onError: (message: string | null) => void }) {
  const initialDraft = useMemo(() => loadProjectDraft(localStorage, library), [library]);
  const [screen, setScreen] = useState<"list" | "detail">("list");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [project, setProject] = useState(initialDraft.project);
  const [requirement, setRequirement] = useState(initialDraft.requirement);
  const [target, setTarget] = useState<ProjectDraft["target"]>(initialDraft.target);
  const [allowRisk, setAllowRisk] = useState(initialDraft.allowRisk);
  const [plan, setPlan] = useState<ProjectPlan | null>(null);
  const [status, setStatus] = useState<ProjectStatus | null>(null);
  const [history, setHistory] = useState<ProjectHistoryEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [riskConfirmed, setRiskConfirmed] = useState(false);
  useEscapeKey(confirming, () => setConfirming(false));

  useEffect(() => {
    saveProjectDraft(localStorage, library, {
      project, requirement, target, allowRisk,
    });
  }, [library, project, requirement, target, allowRisk]);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try { setProjects(await api.projectList(library)); }
    catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setProjectsLoading(false); }
  }, [library, onError]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);

  const loadProjectContext = useCallback(async (path: string, reportError = false) => {
    if (!path.trim()) { setStatus(null); setHistory([]); return; }
    setHistoryLoading(true);
    try {
      const [nextStatus, nextHistory] = await Promise.all([
        api.projectStatus(library, path),
        api.projectHistory(library, path),
      ]);
      setStatus(nextStatus); setHistory(nextHistory.events);
    } catch (reason) {
      setStatus(null); setHistory([]);
      if (reportError) onError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setHistoryLoading(false);
    }
  }, [library, onError]);

  useEffect(() => {
    if (initialDraft.project) void loadProjectContext(initialDraft.project);
  }, [initialDraft.project, loadProjectContext]);

  const chooseProject = async () => {
    const selectedPath = await open({ directory: true, multiple: false, title: "选择要初始化的项目" });
    if (typeof selectedPath === "string") {
      setProject(selectedPath); setPlan(null); setStatus(null); setSelected(new Set());
      await loadProjectContext(selectedPath, true);
    }
  };

  const openManagedProject = async (item: ProjectSummary) => {
    if (item.status !== "active") return;
    setProject(item.path); setPlan(null); setSelected(new Set()); setScreen("detail");
    await loadProjectContext(item.path, true);
  };

  const addProject = async () => {
    const selectedPath = await open({ directory: true, multiple: false, title: "选择需要使用 Skills 的代码项目" });
    if (typeof selectedPath !== "string") return;
    if (project && project !== selectedPath && !window.confirm("选择其他项目会替换当前项目草稿，是否继续？")) return;
    await run("project-add", async () => {
      const selectedStatus = await api.projectStatus(library, selectedPath);
      setPlan(null); setSelected(new Set()); setRiskConfirmed(false);
      if (selectedStatus.managed) {
        const registered = await api.projectRegister(library, selectedPath);
        await loadProjects();
        await openManagedProject(registered);
        return;
      }
      setProject(selectedPath); setRequirement(""); setTarget("auto"); setAllowRisk(false);
      setStatus(selectedStatus); setHistory([]); setScreen("detail");
    });
  };

  const relinkProject = async (item: ProjectSummary) => {
    const selectedPath = await open({ directory: true, multiple: false, title: `重新定位 ${item.display_name}` });
    if (typeof selectedPath !== "string") return;
    await run("project-relink", async () => {
      await api.projectRelink(library, item.id, selectedPath);
      await loadProjects();
    });
  };

  const forgetProject = async (item: ProjectSummary) => {
    if (!window.confirm(`从列表中移除“${item.display_name}”？项目目录和 manifest 都会保留。`)) return;
    await run("project-forget", async () => {
      await api.projectForget(library, item.id);
      await loadProjects();
    });
  };

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); onError(null);
    try { await action(); } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(null); }
  }

  const createPlan = () => run("plan", async () => {
    const next = await api.projectPlan(library, project, requirement, target, allowRisk);
    setPlan(next); setSelected(new Set()); setRiskConfirmed(false);
    await loadProjectContext(project);
  });

  const toggle = (skill: SkillSummary) => {
    if (!canSelectSkill(skill, allowRisk)) return;
    setSelected((current) => {
      const next = new Set(current);
      next.has(skill.id) ? next.delete(skill.id) : next.add(skill.id);
      return next;
    });
  };

  const riskySelected = plan ? selectedRiskCount(plan.recommendations, selected) : 0;
  const apply = () => run("apply", async () => {
    await api.projectApply(library, project, Array.from(selected), requirement, target, allowRisk);
    await loadProjectContext(project); await loadProjects(); setConfirming(false); setSelected(new Set());
  });
  const sync = (force = false) => run("sync", async () => { await api.projectSync(library, project, allowRisk, force); await loadProjectContext(project); await loadProjects(); });
  const unlinkEntry = (skillId: string, force = false) => run("unlink", async () => { await api.projectUnlink(library, project, [skillId], force); await loadProjectContext(project); await loadProjects(); });
  const requestSync = () => {
    if (!status) return;
    const forceCount = status.entries.filter((entry) => projectEntryRequiresForce(entry.state)).length;
    if (forceCount && !window.confirm(
      `检测到 ${forceCount} 个项目内已修改或被替换的条目。强制同步会用目录中的 Skill 覆盖这些项目内容，且无法由 Adaptive Skills 恢复。确认继续？`,
    )) return;
    void sync(forceCount > 0);
  };
  const requestUnlinkEntry = (entry: ProjectEntryStatus) => {
    const force = projectEntryRequiresForce(entry.state);
    const message = force
      ? `“${entry.name || entry.skill_id}”在项目内已有改动或被其他内容替换。强制移除会删除当前路径及其中改动，且无法由 Adaptive Skills 恢复。确认继续？`
      : `从项目中移除“${entry.name || entry.skill_id}”的受管链接？Skill 来源不会被删除。`;
    if (!window.confirm(message)) return;
    void unlinkEntry(entry.skill_id, force);
  };
  const clearDraft = () => {
    clearProjectDraft(localStorage, library);
    setProject(EMPTY_PROJECT_DRAFT.project);
    setRequirement(EMPTY_PROJECT_DRAFT.requirement);
    setTarget(EMPTY_PROJECT_DRAFT.target);
    setAllowRisk(EMPTY_PROJECT_DRAFT.allowRisk);
    setPlan(null); setStatus(null); setHistory([]); setSelected(new Set());
  };

  if (screen === "list") {
    return <div className="stack gap-lg project-index">
      <section className="panel project-index-hero">
        <div><span className="eyebrow">Managed projects</span><h2>项目 Skills 工作区</h2><p>选择需要使用 Skills 的代码项目，而不是 Skills 仓库目录。系统会自动识别新项目或恢复已有 manifest 的项目。</p></div>
        <button className="button primary" disabled={Boolean(busy)} onClick={() => void addProject()}>{busy === "project-add" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}添加项目</button>
      </section>
      {project && !projects.some((item) => item.path === project) && <button className="panel project-draft-card" onClick={() => { setScreen("detail"); void loadProjectContext(project); }}><div><span className="badge warning">继续当前草稿</span><strong>{project}</strong><p>{requirement || "尚未填写需求"}</p></div><ArrowRight size={18} /></button>}
      <section className="project-index-grid">
        {projects.map((item) => <article className={`panel managed-project-card status-${item.status}`} key={item.id}>
          <button className="managed-project-main" disabled={item.status !== "active"} onClick={() => void openManagedProject(item)}><div className="managed-project-icon"><Link2 size={18} /></div><div><span className={`badge ${item.status === "active" ? item.clean ? "success" : "warning" : "warning"}`}>{item.status === "active" ? item.clean ? "已同步" : "有漂移" : item.status === "missing" ? "目录已移动" : "manifest 异常"}</span><h3>{item.display_name}</h3><p title={item.path}>{item.path}</p></div><ChevronRight size={17} /></button>
          <div className="managed-project-meta"><span>{item.entry_count} 个 Skills</span><span>{item.history_count} 条操作</span><span>{formatDate(item.last_activity_at)}</span></div>
          <div className="managed-project-actions">{item.status !== "active" && <button className="text-button" onClick={() => void relinkProject(item)}><FolderOpen size={13} />重新定位</button>}<button className="text-button danger" onClick={() => void forgetProject(item)}><Trash2 size={13} />仅从列表移除</button></div>
        </article>)}
      </section>
      {projectsLoading && <div className="history-empty"><LoaderCircle className="spin" size={18} /><span>正在读取项目列表…</span></div>}
      {!projectsLoading && !projects.length && <div className="project-placeholder compact-placeholder"><Link2 size={25} /><h3>还没有管理过的项目</h3><p>添加普通代码项目后可为它选择 Skills；若目录已有 manifest，会自动恢复历史和软链接状态。</p></div>}
    </div>;
  }

  return (
    <div className="project-layout">
      <section className="panel project-builder">
        <div className="project-draft-heading"><button className="text-button" type="button" onClick={() => { setScreen("list"); void loadProjects(); }}><ArrowLeft size={13} />项目列表</button><div className="step-label"><span>1</span> 项目与需求</div><button className="text-button" type="button" onClick={clearDraft}><Trash2 size={13} />清空草稿</button></div>
        <h2>按项目选择 Skills</h2><p className="muted">先生成可解释方案；只有被勾选的 Skill 才会创建软链接。</p>
        {status && !status.managed && <div className="project-setup-notice"><Sparkles size={16} /><span>这是尚未接入 Adaptive Skills 的普通项目。首次应用 Skill 后会创建 manifest，并加入项目历史列表。</span></div>}
        <label className="input-field"><span>项目目录</span><div className="input-with-button"><input value={project} onChange={(event) => setProject(event.target.value)} placeholder="/path/to/project" /><button type="button" onClick={chooseProject}><FolderOpen size={17} /></button></div></label>
        <label className="input-field"><span>项目需要什么能力？</span><textarea value={requirement} onChange={(event) => setRequirement(event.target.value)} rows={5} placeholder="例如：根据技术方案制作结构清晰的中文演示文稿，并检查视觉一致性。" /></label>
        <div className="two-columns"><label className="input-field"><span>目标 Agent</span><select value={target} onChange={(event) => setTarget(event.target.value as ProjectDraft["target"])}><option value="auto">通用 .agents/skills</option><option value="codex">Codex</option><option value="claude">Claude Code</option></select></label><label className="risk-toggle"><input type="checkbox" checked={allowRisk} onChange={(event) => { setAllowRisk(event.target.checked); setPlan(null); setSelected(new Set()); }} /><span><ShieldAlert size={17} /><strong>显示高风险结果</strong><small>应用前仍需二次确认</small></span></label></div>
        <button className="button primary wide" disabled={!project.trim() || !requirement.trim() || Boolean(busy)} onClick={() => void createPlan()}>{busy === "plan" ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}生成推荐方案</button>
      </section>

      <section className="project-results">
        {!plan ? (
          <div className="project-placeholder recommendation-placeholder">
            <div className="recommendation-placeholder-heading"><div className="recommendation-placeholder-icon"><Sparkles size={18} /></div><div><h3>推荐结果将在这里显示</h3><p>填写项目目录和能力需求后，点击“生成推荐方案”。系统会从本地 Skill 目录中匹配并解释推荐结果。</p></div></div>
            <div className="recommendation-preview" aria-label="推荐结果包含的信息"><span><Search size={13} />匹配理由</span><span><CircleGauge size={13} />质量评分</span><span><ShieldCheck size={13} />风险提示</span></div>
          </div>
        ) : (
          <div className="stack gap-md">
            <div className="result-heading"><div><span className="eyebrow">Step 2 · Review</span><h2>推荐 {plan.recommendations.length} 个 Skills</h2></div><span className="selection-count">已选择 {selected.size}</span></div>
            <div className="recommendation-list">
              {plan.recommendations.map((skill, index) => {
                const selectable = canSelectSkill(skill, allowRisk);
                return (
                  <button className={`recommendation ${selected.has(skill.id) ? "selected" : ""} ${!selectable ? "disabled" : ""}`} key={skill.id} onClick={() => toggle(skill)} disabled={!selectable}>
                    <span className="rank">{String(index + 1).padStart(2, "0")}</span><span className="checkbox">{selected.has(skill.id) && <Check size={14} />}</span><span className="recommendation-body"><span className="recommendation-title"><strong>{skill.name}</strong><i className={`badge risk-${skill.audit_severity}`}>{riskLabel(skill.audit_severity)}</i>{skill.annotation_score != null && <i className="badge neutral">质量 {skill.annotation_score.toFixed(1)}/10</i>}</span><p>{skill.description}</p><small>{skill.reason?.slice(0, 3).map((reason) => `${reason.field}: ${reason.terms.join("/") || reason.contribution}`).join(" · ") || skill.source_name}</small></span><span className="recommendation-score" title="需求匹配排序分，不是 0–10 质量分"><small>匹配</small>{skill.score?.toFixed(1) || "—"}</span>
                  </button>
                );
              })}
            </div>
            <button className="button primary wide" disabled={!selected.size || Boolean(busy)} onClick={() => setConfirming(true)}><Link2 size={17} />预览并应用 {selected.size} 个 Skills</button>
          </div>
        )}

        {status && status.entries.length > 0 && (
          <section className="panel project-status-panel">
            <div className="panel-heading"><div><span className="eyebrow">Managed manifest</span><h3>已挂载 Skills</h3></div><span className={status.clean ? "badge success" : "badge warning"}>{status.clean ? "全部同步" : "检测到漂移"}</span></div>
            <div className="manifest-list">{status.entries.map((entry) => <div className="manifest-row" key={entry.skill_id}><div className={`state-icon ${entry.state === "clean" ? "clean" : "drift"}`}>{entry.state === "clean" ? <Check size={14} /> : <AlertTriangle size={14} />}</div><div><strong>{entry.name || entry.skill_id}</strong><span>{entry.path} · {entry.mode}</span></div><span className="entry-state">{projectEntryStateLabel(entry.state)}</span><button aria-label={`移除 ${entry.name || entry.skill_id}`} title={projectEntryRequiresForce(entry.state) ? "强制移除项目内已改动的条目" : "移除受管链接"} disabled={Boolean(busy)} onClick={() => requestUnlinkEntry(entry)}><Unlink size={15} /></button></div>)}</div>
            {status.entries.some((entry) => entry.state === "catalog-missing") && <div className="project-drift-warning"><AlertTriangle size={16} /><span>有条目已不在 Skills 目录中，无法同步。确认项目内容后，请先用右侧按钮从 manifest 移除。</span></div>}
            {status.entries.some((entry) => projectEntryCanSync(entry.state)) && <button className={`button wide ${status.entries.some((entry) => projectEntryRequiresForce(entry.state)) ? "warning" : "secondary"}`} disabled={Boolean(busy)} onClick={requestSync}><RefreshCw size={16} />{status.entries.some((entry) => projectEntryRequiresForce(entry.state)) ? "确认并覆盖项目漂移" : "同步来源变更"}</button>}
          </section>
        )}

        {project.trim() && (
          <section className="panel project-history-panel">
            <div className="panel-heading"><div><span className="eyebrow">Project activity</span><h3>操作历史</h3></div><button className="text-button" disabled={historyLoading || Boolean(busy)} onClick={() => void loadProjectContext(project, true)}><RefreshCw className={historyLoading ? "spin" : ""} size={14} />刷新</button></div>
            {history.length ? <div className="project-history-list">{history.map((event) => <div className="project-history-row" key={event.id}><div className={`history-icon action-${event.action}`}><History size={14} /></div><div><strong>{projectHistoryLabel(event)}</strong><span>{event.skill_names?.join("、") || "没有需要变更的 Skill"}</span>{event.requirement && <small>{event.requirement}</small>}</div><time>{formatDate(event.created_at)}</time></div>)}</div> : <div className="history-empty"><History size={20} /><span>{historyLoading ? "正在读取历史…" : "还没有成功的应用、同步或移除记录。"}</span></div>}
          </section>
        )}
      </section>

      {confirming && plan && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setConfirming(false)}>
          <div className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-project-links-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="confirm-icon"><Link2 size={22} /></div><h2 id="confirm-project-links-title">确认创建项目软链接</h2><p>将 {selected.size} 个 Skill 挂载到 <strong>{plan.target}</strong>。只管理本次写入 manifest 的条目，不覆盖未登记内容。</p>
            <div className="confirm-summary"><span>项目</span><strong>{project}</strong><span>方式</span><strong>symlink</strong><span>高风险 Skill</span><strong>{riskySelected}</strong></div>
            {riskySelected > 0 && <label className="confirm-risk"><input type="checkbox" checked={riskConfirmed} onChange={(event) => setRiskConfirmed(event.target.checked)} /><span>我已审查所选高风险 Skill，并接受其静态审计结果。</span></label>}
            <div className="button-row"><button className="button ghost" autoFocus onClick={() => setConfirming(false)}>返回检查</button><button className="button primary" disabled={Boolean(busy) || (riskySelected > 0 && !riskConfirmed)} onClick={() => void apply()}>{busy === "apply" ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}确认应用</button></div>
          </div>
        </div>
      )}
    </div>
  );
}

function FindingRow({
  finding,
  tone,
  busy,
  onReview,
}: {
  finding: ValidationFinding | AuditFinding;
  tone: "format" | "hint" | "risk" | "confirmed" | "excluded";
  busy?: boolean;
  onReview?: (
    findingId: string,
    status: "reviewed_false_positive" | "confirmed_risk",
  ) => void;
}) {
  const audit = "finding_id" in finding ? finding : null;
  return (
    <div className={`finding finding-${tone}`}>
      {tone === "hint" || tone === "excluded" ? <ShieldCheck size={15} /> : <AlertTriangle size={15} />}
      <div>
        <div className="finding-heading"><strong>{finding.rule}</strong><span>{finding.severity}</span></div>
        <p>{finding.message}</p>
        {audit?.content_summary && <code>{audit.content_summary}</code>}
        <span>{finding.file}{finding.line ? `:${finding.line}` : ""}{audit ? ` · ${audit.context}` : ""}</span>
        {audit?.review_stale && <small>源码摘要已变化，之前的审查结论已失效。</small>}
        {audit?.review_note && <small>{audit.review_note}</small>}
        {audit && onReview && (tone === "risk" || tone === "confirmed" || tone === "excluded") && (
          <div className="finding-actions">
            {tone !== "confirmed" && <button className="button compact warning" disabled={busy} onClick={() => onReview(audit.finding_id, "confirmed_risk")}>确认为风险</button>}
            {tone !== "excluded" && <button className="button compact ghost" disabled={busy} onClick={() => onReview(audit.finding_id, "reviewed_false_positive")}>标记误报</button>}
          </div>
        )}
      </div>
    </div>
  );
}

function FindingSection({
  title,
  description,
  findings,
  tone,
  empty,
  busy,
  onReview,
}: {
  title: string;
  description: string;
  findings: Array<ValidationFinding | AuditFinding>;
  tone: "format" | "hint" | "risk" | "confirmed" | "excluded";
  empty: string;
  busy?: boolean;
  onReview?: (
    findingId: string,
    status: "reviewed_false_positive" | "confirmed_risk",
  ) => void;
}) {
  return (
    <section className="drawer-section finding-section">
      <div className="section-title-row"><div><h3>{title}</h3><p>{description}</p></div><span>{findings.length}</span></div>
      {findings.length ? <div className="finding-list">{findings.map((finding, index) => <FindingRow key={`${finding.rule}-${finding.file}-${finding.line ?? index}`} finding={finding} tone={tone} busy={busy} onReview={onReview} />)}</div> : <div className="clean-callout"><ShieldCheck size={18} /><span>{empty}</span></div>}
    </section>
  );
}

function SkillDrawer({ skill, busy, onReview, onClose }: {
  skill: SkillDetail;
  busy: boolean;
  onReview: (
    findingId: string,
    status: "reviewed_false_positive" | "confirmed_risk",
  ) => void;
  onClose: () => void;
}) {
  const capabilityHints = skill.audit.filter((finding) => finding.classification === "capability_hint");
  const unconfirmedRisks = skill.audit.filter((finding) => finding.classification === "risk" && finding.status === "unreviewed");
  const confirmedRisks = skill.audit.filter((finding) => finding.status === "confirmed_risk");
  const excludedFindings = skill.audit.filter((finding) => finding.status === "reviewed_false_positive");
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="skill-drawer" role="dialog" aria-modal="true" aria-labelledby="skill-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="drawer-header"><div><span className="eyebrow">{skill.source_name} / {skill.rel_path}</span><h2 id="skill-drawer-title">{skill.name}</h2></div><button className="icon-button" autoFocus aria-label="关闭 Skill 详情" onClick={onClose}><X size={18} /></button></div>
        <p className="drawer-description">{skill.description}</p>
        <div className="drawer-badges"><span className={`badge risk-${skill.audit_severity}`}>{riskLabel(skill.audit_severity)}</span><span className={skill.validation.length ? "badge warning" : "badge success"}>{skill.validation.length ? `${skill.valid ? "格式提示" : "格式不兼容"} ${skill.validation.length}` : "格式兼容"}</span>{(skill.capability_hint_count ?? 0) > 0 && <span className="badge neutral">能力提示 {skill.capability_hint_count}</span>}{skill.score != null && <span className="badge neutral">智能评分 {skill.score}</span>}</div>
        <div className="detail-grid"><div><span>一级分类</span><strong>{skill.category_l1 || "未分类"}</strong></div><div><span>二级分类</span><strong>{skill.category_l2 || "未分类"}</strong></div><div><span>许可证</span><strong>{skill.license || "未声明"}</strong></div><div><span>来源提交</span><strong>{shortSha(skill.head_sha)}</strong></div></div>
        {(skill.problem || skill.use_case) && <section className="drawer-section"><h3>AI 整理</h3>{skill.problem && <div className="insight-block"><span>解决的问题</span><p>{skill.problem}</p></div>}{skill.use_case && <div className="insight-block"><span>应用场景</span><p>{skill.use_case}</p></div>}</section>}
        <FindingSection title="格式兼容性" description="只判断 SKILL.md 与 frontmatter 是否符合加载规范，不参与安全风险等级。" findings={skill.validation} tone="format" empty="格式兼容，未发现阻止加载的问题。" />
        <FindingSection title="能力提示" description="来自文档描述或禁止名单，说明 Skill 涉及的能力，不作为真实风险。" findings={capabilityHints} tone="hint" empty="没有额外的敏感能力提示。" />
        <FindingSection title="未确认风险" description="实际命令或文件行为命中的保守规则；在审查前参与整体风险等级。" findings={unconfirmedRisks} tone="risk" empty="没有等待人工确认的真实风险。" busy={busy} onReview={onReview} />
        <FindingSection title="确认风险" description="人工确认是真实行为的风险，继续参与整体风险等级和项目门禁。" findings={confirmedRisks} tone="confirmed" empty="没有已确认风险。" busy={busy} onReview={onReview} />
        {excludedFindings.length > 0 && <FindingSection title="已排除误报" description="审查结论绑定当前源码摘要；源码变化后会自动回到未确认风险。" findings={excludedFindings} tone="excluded" empty="没有已排除误报。" busy={busy} onReview={onReview} />}
        <section className="drawer-section"><div className="section-title-row"><h3>SKILL.md</h3><span>{skill.skill_md_path}</span></div><pre className="skill-content">{skill.body || "（正文为空）"}</pre></section>
      </aside>
    </div>
  );
}

function ActivityToast({ label }: { label: string }) {
  const messages: Record<string, string> = { "source-add": "正在 Clone、扫描并建立评测队列…", "refresh-all": "正在逐个更新并扫描全部来源…", "llm-config": "正在保存本地模型配置…", plan: "正在匹配项目需求…", apply: "正在创建项目软链接…", sync: "正在同步项目链接…", unlink: "正在安全移除链接…" };
  const message = messages[label] || (label.startsWith("audit-review-") ? "正在保存风险审查结论并重算等级…" : label.startsWith("evaluate-") ? "正在调用模型生成分类与评分提案…" : label.startsWith("evaluation-apply-") ? "正在应用评测提案…" : label.startsWith("evaluation-reject-") ? "正在拒绝评测提案…" : label.startsWith("update-") ? "正在更新并重新扫描来源…" : label.startsWith("scan-") ? "正在重新扫描来源…" : "正在执行本地操作…");
  return <div className="activity-toast"><LoaderCircle className="spin" size={17} /><span>{message}</span></div>;
}

export default App;
