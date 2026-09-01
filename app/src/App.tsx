import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open, save as saveFile } from "@tauri-apps/plugin-dialog";
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
  Star,
  Trash2,
  Unlink,
  X,
} from "lucide-react";
import { api } from "./api";
import appIconUrl from "./assets/app-icon.svg";
import { Localized, translate, useLanguage } from "./i18n";
import {
  AgentTargetRegistryPanel,
  ProjectActivationMatrix,
  ProjectProfilesPanel,
} from "./ProjectControls";
import {
  canSelectSkill,
  formatDate,
  formatStarCount,
  isElevatedRisk,
  projectEntryCanSync,
  projectEntryRequiresForce,
  projectEntryStateLabel,
  projectSelectionStateLabel,
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
  ProjectExternalEntry,
  ProjectExternalMatch,
  ProjectPlan,
  ProjectHistoryEvent,
  ProjectStatus,
  RiskLevel,
  SkillDetail,
  SkillSummary,
  SourceRefreshAllResult,
  SourceReconcileResult,
  SourceSummary,
  SourceUpdatePolicy,
  SourceRemovalPreview,
  SourceRemovalResult,
  SourceRestoreResult,
  SourceForgetPreview,
  SourceForgetResult,
  LLMProfile,
  LLMProfileProvider,
  LLMAPIMode,
  LLMEvaluation,
  LLMEvaluationRun,
  LLMProfileTestResult,
  ProjectSummary,
  AuditFinding,
  AuditReviewStatus,
  ValidationFinding,
  BootstrapCandidate,
  BootstrapDiscovery,
  BootstrapImportResult,
  BootstrapInstallResult,
  BootstrapStatus,
  CategoryFilter,
  ActivationCell,
  ActivationMatrix,
  ActivationRow,
  ActivationTarget,
  AgentTarget,
  CustomAgentTargetInput,
  SkillProfilePreview,
  SkillProfileImportPreview,
  SkillProfileSummary,
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

function isGitHubSource(url: string | null | undefined): boolean {
  return Boolean(url && (
    /^(?:https?|ssh|git):\/\/(?:[^@/]+@)?github\.com\//i.test(url)
    || /^[^@\s]+@github\.com:/i.test(url)
  ));
}

function GitHubStars({
  url,
  stars,
  checkedAt,
  className = "",
}: {
  url: string | null | undefined;
  stars: number | null | undefined;
  checkedAt?: string | null;
  className?: string;
}) {
  if (!isGitHubSource(url)) return null;
  const fullCount = stars == null ? null : stars.toLocaleString("en-US");
  const title = fullCount == null
    ? translate(
      "尚未获取 GitHub Star；重新扫描可重试，更新来源时也会按缓存周期刷新。Star 不参与质量评分、需求匹配或风险判断。",
      "GitHub stars have not been fetched yet. Rescanning retries immediately, while source updates refresh on the cache schedule. Stars do not affect quality, matching, or risk.",
    )
    : translate(
      `GitHub Stars：${fullCount}${checkedAt ? ` · 更新于 ${formatDate(checkedAt)}` : ""}。仅作为仓库热度参考，不参与质量评分、需求匹配或风险判断。`,
      `GitHub stars: ${fullCount}${checkedAt ? ` · Updated ${formatDate(checkedAt)}` : ""}. Repository popularity only; not used for quality, matching, or risk.`,
    );
  return (
    <span className={`github-stars ${stars == null ? "missing" : ""} ${className}`.trim()} title={title} aria-label={title}>
      <Star size={11} aria-hidden="true" />
      {formatStarCount(stars)}
    </span>
  );
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

function useFocusTrap(
  active: boolean,
  container: { readonly current: HTMLElement | null },
) {
  useEffect(() => {
    if (!active || !container.current) return;
    const previous = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !container.current) return;
      const controls = Array.from(
        container.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    container.current.querySelector<HTMLElement>("[data-initial-focus]")?.focus();
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previous?.focus();
    };
  }, [active, container]);
}

function App() {
  const { language, setLanguage } = useLanguage();
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
    async (query?: string, reconcile = false) => {
      setLoading(true);
      setError(null);
      try {
        let reconcileError: string | null = null;
        if (reconcile) {
          try {
            await api.reconcileSources(library);
          } catch (reason) {
            reconcileError = reason instanceof Error ? reason.message : String(reason);
          }
        }
        const value = await api.snapshot(library, query);
        setSnapshot(value);
        if (!didAutoOpenBootstrap.current && value.summary.source_count === 0) {
          didAutoOpenBootstrap.current = true;
          setView("bootstrap");
        }
        if (value.library.path !== library) setLibrary(value.library.path);
        localStorage.setItem("adaptive-skills-library", value.library.path);
        if (reconcileError) setError(translate(
          `新来源发现未完成：${reconcileError}`,
          `Source discovery did not complete: ${reconcileError}`,
        ));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setLoading(false);
      }
    },
    [library],
  );

  useEffect(() => {
    void loadSnapshot(undefined, true);
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
    const selected = await open({ directory: true, multiple: false, title: translate("选择 Skills 目录") });
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

  const reconcileLocalSources = async (): Promise<SourceReconcileResult | null> => {
    setBusy("source-reconcile");
    setError(null);
    try {
      const result = await api.reconcileSources(library);
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const previewSourceRemoval = async (sourceId: string): Promise<SourceRemovalPreview | null> => {
    setBusy(`source-remove-preview-${sourceId}`);
    setError(null);
    try {
      return await api.previewSourceRemoval(library, sourceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const removeSource = async (
    preview: SourceRemovalPreview,
    cleanupReferences: boolean,
  ): Promise<SourceRemovalResult | null> => {
    setBusy(`source-remove-${preview.source.id}`);
    setError(null);
    try {
      const result = await api.removeSource(
        library,
        preview.source.id,
        preview.preview_digest,
        cleanupReferences,
      );
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const restoreSource = async (sourceId: string): Promise<SourceRestoreResult | null> => {
    setBusy(`source-restore-${sourceId}`);
    setError(null);
    try {
      const result = await api.restoreSource(library, sourceId);
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const previewSourceForget = async (sourceId: string): Promise<SourceForgetPreview | null> => {
    setBusy(`source-forget-preview-${sourceId}`);
    setError(null);
    try {
      return await api.previewSourceForget(library, sourceId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const forgetSource = async (preview: SourceForgetPreview): Promise<SourceForgetResult | null> => {
    setBusy(`source-forget-${preview.source.id}`);
    setError(null);
    try {
      const result = await api.forgetSource(
        library,
        preview.source.id,
        preview.preview_digest,
      );
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const runEvaluation = async (sourceId: string): Promise<LLMEvaluationRun | null> => {
    setBusy(`evaluate-${sourceId}`);
    setError(null);
    try {
      const result = await api.evaluateSource(library, sourceId);
      await loadSnapshot();
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const testLLMProfile = async (profileId: string): Promise<LLMProfileTestResult | null> => {
    setBusy("llm-profile-test");
    setError(null);
    try {
      const result = await api.testLLMProfile(library, profileId);
      await loadSnapshot();
      if (!result.ok) {
        setError(translate(
          "没有检测到该连接所需的本地 CLI，请检查安装位置或改用 API 连接。",
          "The required local CLI was not detected. Check its installation path or use an API connection.",
        ));
      }
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
        throw new Error(translate(
          "新来源没有返回可扫描的稳定 ID",
          "The new source did not return a stable ID for scanning",
        ));
      }
      await api.scan(library, sourceId);
    });

  return (
    <Localized><div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={appIconUrl} alt="" aria-hidden="true" />
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

        <div className="language-switch" role="group" aria-label="界面语言">
          <button className={language === "zh-CN" ? "active" : ""} onClick={() => setLanguage("zh-CN")} aria-label={language === "zh-CN" ? "中文" : "Chinese"} aria-pressed={language === "zh-CN"}>ZH</button>
          <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} aria-label={language === "zh-CN" ? "英文" : "English"} aria-pressed={language === "en"}>EN</button>
        </div>

        <div className="sidebar-footer">
          <span className={`status-dot ${error ? "error" : loading ? "loading" : "ready"}`} />
          {error ? "连接异常" : loading ? "正在读取目录" : translate(`契约 v${snapshot?.contract_version ?? "—"}`, `Contract v${snapshot?.contract_version ?? "—"}`)}
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
            onClick={() => void loadSnapshot(undefined, true)}
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
                removedSources={snapshot.removed_sources}
                busy={busy}
                onAdd={addAndScanSource}
                onScan={(id) => runAction(`scan-${id}`, () => api.scan(library, id))}
                onReconcile={reconcileLocalSources}
                onRefreshAll={refreshAllSources}
                onSetPolicy={(id, policy) =>
                  runAction(`policy-${id}`, () => api.setSourcePolicy(library, id, policy))
                }
                onPreviewRemove={previewSourceRemoval}
                onRemove={removeSource}
                onRestore={restoreSource}
                onPreviewForget={previewSourceForget}
                onForget={forgetSource}
                onUpdate={(id) =>
                  runAction(`update-${id}`, async () => {
                    await api.updateSource(library, id);
                    await api.scan(library, id);
                  })
                }
                llmEnabled={Boolean(
                  snapshot.llm.active_profile &&
                  snapshot.llm.availability[snapshot.llm.active_profile.provider]
                )}
                onEvaluate={runEvaluation}
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
                onTest={testLLMProfile}
                onEvaluate={runEvaluation}
                onClearErrors={() => runAction("llm-errors-clear", () => api.clearLLMErrors(library))}
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
            {view === "projects" && <ProjectsView key={library} library={library} categories={snapshot.filters.categories} onError={setError} />}
          </div>
        ) : (
          <EmptyConnection library={library} onChoose={chooseLibrary} onRetry={() => loadSnapshot()} />
        )}
      </main>

      {detail && <SkillDrawer skill={detail} busy={Boolean(busy)} onReview={reviewAuditFinding} onClose={() => setDetail(null)} />}
      {busy && <ActivityToast label={busy} />}
    </div></Localized>
  );
}

function LoadingState() {
  return (
    <Localized><div className="center-state">
      <LoaderCircle className="spin" size={28} />
      <strong>正在连接本地目录</strong>
      <span>读取 SQLite 目录，不会执行第三方 Skill。</span>
    </div></Localized>
  );
}

function EmptyConnection({ library, onChoose, onRetry }: { library: string; onChoose: () => void; onRetry: () => void }) {
  return (
    <Localized><div className="center-state empty">
      <Database size={32} />
      <strong>尚未连接 Skills 目录</strong>
      <span>{library}</span>
      <div className="button-row">
        <button className="button secondary" onClick={onChoose}><FolderOpen size={16} />选择目录</button>
        <button className="button primary" onClick={onRetry}><RefreshCw size={16} />重新连接</button>
      </div>
    </div></Localized>
  );
}

const BOOTSTRAP_KIND_LABEL: Record<BootstrapCandidate["kind"], string> = {
  local: "本地",
  git: "Git 工作区",
  symlink: "软链接",
  system: "系统内置",
  provider: "宿主自带",
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
    const selected = await open({ directory: true, multiple: true, title: translate("选择要扫描的 Skill 目录") });
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
    const accepted = window.confirm(translate(
      `将从 GitHub 克隆 ${selectedStarters.size} 个第三方仓库到 ${library}，随后只做静态扫描，不会执行其中的 Skill。是否继续？`,
      `${selectedStarters.size} third-party repositories will be cloned from GitHub into ${library}, then statically scanned without executing any Skill. Continue?`,
    ));
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
    <Localized><div className="bootstrap-page stack gap-lg">
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
    </div></Localized>
  );
}

function Overview({ snapshot, onNavigate }: { snapshot: AppSnapshot; onNavigate: (view: View) => void }) {
  const { summary } = snapshot;
  const safePercent = summary.skill_count ? Math.round((summary.valid_count / summary.skill_count) * 100) : 0;
  const elevated = summary.risk_counts.high + summary.risk_counts.critical;
  const cards = [
    { label: "收录 Skills", value: summary.skill_count, note: translate(`${summary.annotated_count} 条智能整理`, `${summary.annotated_count} smart annotations`), icon: Layers3, tone: "mint" },
    { label: "Git 来源", value: summary.source_count, note: translate(`最后扫描 ${formatDate(summary.last_scanned_at)}`, `Last scanned ${formatDate(summary.last_scanned_at)}`), icon: FolderGit2, tone: "blue" },
    { label: "有效率", value: `${safePercent}%`, note: translate(`${summary.invalid_count} 个需要修复`, `${summary.invalid_count} need attention`), icon: ShieldCheck, tone: "green" },
    { label: "高风险信号", value: elevated, note: translate(`${summary.risk_counts.critical} 个严重风险`, `${summary.risk_counts.critical} critical risks`), icon: ShieldAlert, tone: "amber" },
  ];
  return (
    <Localized><div className="stack gap-xl">
      <section className="hero-panel">
        <div>
          <div className="eyebrow"><Sparkles size={14} /> 本地优先 · 按项目加载</div>
          <h2>让每个项目只看到真正需要的 Skills。</h2>
          <p>分类、评分和风险审查都保存在本地 SQLite。先解释推荐，再由你确认创建项目软链接。</p>
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
                <div><strong>{source.name}</strong><span className="compact-source-meta">{source.skill_count} skills · {shortSha(source.head_sha)}<GitHubStars url={source.url} stars={source.github_stars} checkedAt={source.github_metadata_checked_at} /></span></div>
                <span className={source.invalid_count ? "badge warning" : "badge success"}>{source.invalid_count ? translate(`${source.invalid_count} 异常`, `${source.invalid_count} issues`) : "健康"}</span>
              </div>
            ))}
          </div>
        </article>
      </section>
    </div></Localized>
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
    <Localized><div className="stack gap-lg">
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
            <div className="skill-meta"><span>{skill.category_l1 || "未分类"}{skill.category_l2 ? ` / ${skill.category_l2}` : ""}</span><div className="skill-source-meta"><span>{skill.source_name}</span><GitHubStars url={skill.source_url} stars={skill.source_stars} /></div></div>
          </button>
        ))}
      </section>
      {!filtered.length && <div className="empty-inline"><Search size={24} /><strong>没有匹配的 Skill</strong><span>尝试调整风险、来源或分类筛选。</span></div>}
    </div></Localized>
  );
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <Localized><label className="select-field"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label></Localized>;
}

function SourcesView({ library, sources, removedSources, busy, onAdd, onScan, onReconcile, onUpdate, onRefreshAll, onSetPolicy, onPreviewRemove, onRemove, onRestore, onPreviewForget, onForget, llmEnabled, onEvaluate }: {
  library: string;
  sources: SourceSummary[];
  removedSources: SourceSummary[];
  busy: string | null;
  onAdd: (url: string, name?: string) => Promise<boolean>;
  onScan: (id: string) => Promise<boolean>;
  onReconcile: () => Promise<SourceReconcileResult | null>;
  onUpdate: (id: string) => Promise<boolean>;
  onRefreshAll: () => Promise<SourceRefreshAllResult | null>;
  onSetPolicy: (id: string, policy: SourceUpdatePolicy) => Promise<boolean>;
  onPreviewRemove: (id: string) => Promise<SourceRemovalPreview | null>;
  onRemove: (preview: SourceRemovalPreview, cleanupReferences: boolean) => Promise<SourceRemovalResult | null>;
  onRestore: (id: string) => Promise<SourceRestoreResult | null>;
  onPreviewForget: (id: string) => Promise<SourceForgetPreview | null>;
  onForget: (preview: SourceForgetPreview) => Promise<SourceForgetResult | null>;
  llmEnabled: boolean;
  onEvaluate: (id: string) => Promise<LLMEvaluationRun | null>;
}) {
  const initialDraft = useMemo(() => loadSourceDraft(localStorage, library), [library]);
  const [adding, setAdding] = useState(initialDraft.adding);
  const [url, setUrl] = useState(initialDraft.url);
  const [name, setName] = useState(initialDraft.name);
  const [refreshResult, setRefreshResult] = useState<SourceRefreshAllResult | null>(null);
  const [reconcileResult, setReconcileResult] = useState<SourceReconcileResult | null>(null);
  const [evaluationResult, setEvaluationResult] = useState<LLMEvaluationRun | null>(null);
  const [removalPreview, setRemovalPreview] = useState<SourceRemovalPreview | null>(null);
  const [cleanupReferences, setCleanupReferences] = useState(true);
  const [removalResult, setRemovalResult] = useState<SourceRemovalResult | null>(null);
  const [restoredName, setRestoredName] = useState<string | null>(null);
  const [forgetPreview, setForgetPreview] = useState<SourceForgetPreview | null>(null);
  const [forgetConfirmation, setForgetConfirmation] = useState("");
  const [forgetResult, setForgetResult] = useState<SourceForgetResult | null>(null);
  const removalDialogRef = useRef<HTMLDivElement>(null);
  const forgetDialogRef = useRef<HTMLDivElement>(null);
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
    setReconcileResult(null);
    const result = await onRefreshAll();
    if (result) {
      setRefreshResult(result);
      setRefreshHistory(recordSourceRefresh(localStorage, library, result));
    }
  };
  const reconcile = async () => {
    setReconcileResult(null);
    setRefreshResult(null);
    const result = await onReconcile();
    if (result) setReconcileResult(result);
  };
  const refreshFailures = refreshResult?.results.filter((item) => item.status === "failed") ?? [];
  const evaluateSource = (source: SourceSummary) => {
    if (!source.pending_evaluation_count || !llmEnabled) return;
    const confirmed = window.confirm(translate(
      `将使用已配置的大模型评测 ${source.name}。当前有 ${source.pending_evaluation_count} 个待处理 Skill，本次按配置上限执行，可能消耗模型额度。是否继续？`,
      `${source.name} will be evaluated with the configured model. ${source.pending_evaluation_count} Skills are pending; this run follows the configured limit and may consume model quota. Continue?`,
    ));
    if (confirmed) {
      setEvaluationResult(null);
      void onEvaluate(source.id).then((result) => {
        if (result) setEvaluationResult(result);
      });
    }
  };
  const closeRemoval = () => {
    if (busy?.startsWith("source-remove-")) return;
    setRemovalPreview(null);
  };
  useEscapeKey(Boolean(removalPreview), closeRemoval);
  useFocusTrap(Boolean(removalPreview), removalDialogRef);
  const closeForget = () => {
    if (busy?.startsWith("source-forget-")) return;
    setForgetPreview(null);
    setForgetConfirmation("");
  };
  useEscapeKey(Boolean(forgetPreview), closeForget);
  useFocusTrap(Boolean(forgetPreview), forgetDialogRef);
  const previewRemoval = async (source: SourceSummary) => {
    setRemovalResult(null);
    setRestoredName(null);
    const preview = await onPreviewRemove(source.id);
    if (!preview) return;
    setCleanupReferences(true);
    setRemovalPreview(preview);
  };
  const confirmRemoval = async () => {
    if (!removalPreview) return;
    const result = await onRemove(removalPreview, cleanupReferences);
    if (!result) return;
    setRemovalResult(result);
    setRemovalPreview(null);
  };
  const restoreRemovedSource = async (source: SourceSummary) => {
    setRemovalResult(null);
    const result = await onRestore(source.id);
    if (result) setRestoredName(source.name);
  };
  const previewForget = async (source: SourceSummary) => {
    setForgetResult(null);
    setRestoredName(null);
    const preview = await onPreviewForget(source.id);
    if (!preview) return;
    setForgetConfirmation("");
    setForgetPreview(preview);
  };
  const confirmForget = async () => {
    if (!forgetPreview || forgetConfirmation !== forgetPreview.source.name) return;
    const result = await onForget(forgetPreview);
    if (!result) return;
    setForgetResult(result);
    setForgetPreview(null);
    setForgetConfirmation("");
  };
  const blockedCleanup = Boolean(cleanupReferences && removalPreview?.blocker_count);
  const blockedForget = Boolean(forgetPreview?.blocker_count);
  const restorableRemovedCount = removedSources.filter((source) => source.restorable).length;
  return (
    <Localized><div className="stack gap-lg">
      <div className="section-toolbar">
        <div><h2>{sources.length} 个来源</h2><p>根目录下手动 Clone 的 Git 仓库可自动发现；远程更新只接受 fast-forward。GitHub Star 在添加、重新扫描或更新来源时刷新，仅作热度参考。</p></div>
        <div className="button-row">
          <button className="button secondary" disabled={Boolean(busy)} onClick={() => void reconcile()}>
            {busy === "source-reconcile" ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}
            {busy === "source-reconcile" ? "正在检查目录…" : "发现本地仓库"}
          </button>
          <button className="button secondary" disabled={Boolean(busy) || !sources.length} onClick={() => void refreshAll()}>
            {busy === "refresh-all" ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
            {busy === "refresh-all" ? "正在更新全部来源…" : "全部更新"}
          </button>
          <button className="button primary" disabled={Boolean(busy)} onClick={() => setAdding((value) => !value)}><Plus size={16} />添加 Git 来源</button>
        </div>
      </div>
      {reconcileResult && (
        <div className={`refresh-summary ${reconcileResult.failed ? "with-failures" : ""}`} role="status">
          <div className="refresh-summary-heading">
            {reconcileResult.failed ? <AlertTriangle size={18} /> : <Check size={18} />}
            <div>
              <strong>Skills 目录检查完成</strong>
              <p>{reconcileResult.discovered ? translate(
                `新发现 ${reconcileResult.discovered} 个 Git 来源 · ${reconcileResult.scanned} 个已扫描 · ${reconcileResult.failed} 个失败`,
                `Discovered ${reconcileResult.discovered} Git sources · ${reconcileResult.scanned} scanned · ${reconcileResult.failed} failed`,
              ) : "没有发现未登记的顶层 Git 仓库"}</p>
            </div>
          </div>
          {reconcileResult.results.filter((item) => item.status === "failed").map((item) => <div className="refresh-failures" key={item.source_id}><p><strong>{item.source}</strong><span>{item.error || "扫描失败"}</span></p></div>)}
        </div>
      )}
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
      {evaluationResult && <EvaluationRunSummary run={evaluationResult} />}
      {removalResult && (
        <div className="refresh-summary" role="status">
          <div className="refresh-summary-heading"><Check size={18} /><div><strong>来源已从目录移除</strong><p>{removalResult.cleanup_references
            ? translate(`已清理 ${removalResult.cleaned_reference_count} 个受管引用`, `Cleaned ${removalResult.cleaned_reference_count} managed references`)
            : translate(`保留 ${removalResult.kept_reference_count} 个项目引用`, `Kept ${removalResult.kept_reference_count} project references`)} · Git 仓库仍保留在原位置</p></div></div>
          <div className="source-retained-path" title={removalResult.repository_path}><FolderGit2 size={14} /><span>{removalResult.repository_path}</span></div>
        </div>
      )}
      {restoredName && <div className="refresh-summary" role="status"><div className="refresh-summary-heading"><Check size={18} /><div><strong>来源已恢复</strong><p>{translate(`${restoredName} 已重新扫描并返回目录。`, `${restoredName} was rescanned and restored to the catalog.`)}</p></div></div></div>}
      {forgetResult && (
        <div className="refresh-summary" role="status">
          <div className="refresh-summary-heading"><Check size={18} /><div><strong>目录历史记录已彻底移除</strong><p>{translate(
            `${forgetResult.source_name} 的 ${forgetResult.deleted_skill_count} 个历史 Skill 已从 SQLite 删除；现在可以重新登记同名或同路径仓库。`,
            `${forgetResult.deleted_skill_count} historical Skills from ${forgetResult.source_name} were deleted from SQLite. A repository with the same name or path can now be registered again.`,
          )}</p></div></div>
          <div className="source-retained-path" title={forgetResult.repository_path}><FolderGit2 size={14} /><span>{forgetResult.repository_exists
            ? translate(`仓库文件仍保留：${forgetResult.repository_path}`, `Repository files remain: ${forgetResult.repository_path}`)
            : translate(`原仓库目录已不存在：${forgetResult.repository_path}`, `Original repository directory no longer exists: ${forgetResult.repository_path}`)}</span></div>
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
            <div className="source-card-heading"><div className="source-avatar large">{source.name.slice(0, 2).toUpperCase()}</div><div><h3>{source.name}</h3><p>{source.url || "本地归集目录"}</p></div><div className="source-badges"><span className="badge neutral">{source.url ? (source.update_policy === "local" ? "本地维护" : "远程跟随") : "本地归集"}</span><span className={source.repository_exists === false || source.invalid_count ? "badge warning" : "badge success"}>{source.repository_exists === false ? "目录缺失" : source.invalid_count ? "需检查" : "健康"}</span></div></div>
            <div className="source-stat-row"><div><span>Skills</span><strong>{source.skill_count}</strong></div><div><span>有效</span><strong>{source.valid_count}</strong></div><div><span>待评测</span><strong>{source.pending_evaluation_count}</strong></div></div>
            <div className="source-path" title={source.local_path}><FolderGit2 size={14} />{source.local_path}</div>
            <div className="source-footer"><span>{source.url ? <><GitBranch size={14} />{source.tracked_ref || "当前分支"} · {shortSha(source.head_sha)}</> : <><Layers3 size={14} />本地副本 · 不拉取</>}</span><div className="source-footer-meta"><GitHubStars url={source.url} stars={source.github_stars} checkedAt={source.github_metadata_checked_at} /><span>{formatDate(source.last_scanned_at)}</span></div></div>
            <div className="source-actions">
              {source.repository_exists === false && source.reclone_supported && source.url ? <button className="button primary compact" disabled={Boolean(busy)} onClick={() => void onAdd(source.url!, source.name)}>{busy === "source-add" ? <LoaderCircle className="spin" size={15} /> : <GitBranch size={15} />}重新 Clone 并扫描</button> : <>
                {source.url && <button className="button ghost compact" disabled={Boolean(busy)} onClick={() => void onSetPolicy(source.id, source.update_policy === "local" ? "remote" : "local")} title={source.update_policy === "local" ? "恢复自动拉取；工作区仍需保持干净" : "保留本地改动，全部更新时只扫描、不拉取"}>{busy === `policy-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Settings2 size={15} />}{source.update_policy === "local" ? "改为远程跟随" : "设为本地维护"}</button>}
                <button className="button secondary compact" disabled={Boolean(busy)} onClick={() => void onScan(source.id)}>{busy === `scan-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}重新扫描</button>
                {source.pending_evaluation_count > 0 && <button className="button secondary compact" disabled={Boolean(busy) || !llmEnabled} onClick={() => evaluateSource(source)} title={llmEnabled ? "生成分类和质量评分提案" : "先在 LLM 评测页面配置模型"}>{busy === `evaluate-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{llmEnabled ? translate(`评测 ${source.pending_evaluation_count}`, `Evaluate ${source.pending_evaluation_count}`) : "配置 LLM"}</button>}
                {source.url && source.update_policy === "remote" && <button className="button primary compact" disabled={Boolean(busy)} onClick={() => void onUpdate(source.id)}>{busy === `update-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <GitBranch size={15} />}更新并扫描</button>}
              </>}
              <button className="button ghost compact danger" disabled={Boolean(busy)} onClick={() => void previewRemoval(source)}>{busy === `source-remove-preview-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}移除来源</button>
            </div>
          </article>
        ))}
      </section>
      {sources.length === 0 && <div className="empty-inline"><FolderGit2 size={24} /><strong>没有正在管理的来源</strong><span>可以添加 Git 来源，或从下方恢复已移除来源。</span></div>}
      {removedSources.length > 0 && (
        <section className="panel removed-sources-panel">
          <div className="panel-heading"><div><h3>已移除来源</h3><p>这里保存的是 SQLite 历史记录。目录仍存在时可恢复；也可确认彻底移除记录，释放同名和同路径。</p></div><span className="badge neutral">{removedSources.length} 个历史记录 · {restorableRemovedCount} 个可恢复</span></div>
          <div className="removed-source-list">
            {removedSources.map((source) => (
              <div className="removed-source-row" key={source.id}>
                <div className="removed-source-state"><Trash2 size={15} /></div>
                <div className="removed-source-copy"><strong>{source.name}</strong><span title={source.local_path}>{source.local_path}</span><small>{source.skill_count} 个历史 Skill · {source.repository_exists ? "目录存在" : "目录不存在"} · {formatDate(source.updated_at)}</small></div>
                <div className="removed-source-actions">
                  <button className="button secondary compact" disabled={Boolean(busy) || !source.restorable} title={source.restorable ? "恢复原来源并重新扫描" : "原仓库目录不存在，无法恢复"} onClick={() => void restoreRemovedSource(source)}>{busy === `source-restore-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}恢复并扫描</button>
                  <button className="button ghost compact danger" disabled={Boolean(busy)} onClick={() => void previewForget(source)}>{busy === `source-forget-preview-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}彻底移除记录</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {removalPreview && (
        <div className="modal-backdrop" role="presentation" onMouseDown={closeRemoval}>
          <div ref={removalDialogRef} className="confirm-modal source-remove-modal" role="dialog" aria-modal="true" aria-labelledby="source-remove-title" aria-describedby="source-remove-description" onMouseDown={(event) => event.stopPropagation()}>
            <div className="confirm-icon danger"><Trash2 size={22} /></div>
            <h2 id="source-remove-title">移除来源“{removalPreview.source.name}”</h2>
            <p id="source-remove-description">将从 Adaptive Skills 目录隐藏该来源和 {removalPreview.skill_count} 个 Skill。Git 仓库不会删除，仍保留在原位置，并可随时恢复。</p>
            <div className="confirm-summary source-remove-summary">
              <span>仓库保留</span><strong title={removalPreview.repository_path}>{removalPreview.repository_path}</strong>
              <span>受影响项目</span><strong>{removalPreview.affected_project_count}</strong>
              <span>受管引用</span><strong>{removalPreview.reference_count} 个（{removalPreview.symlink_count} 软链接 · {removalPreview.copy_count} 副本）</strong>
              <span>恢复外部内容</span><strong>{removalPreview.restore_count}</strong>
            </div>
            {removalPreview.references.length > 0 && <div className="source-remove-projects" aria-label="受影响的项目">{removalPreview.references.map((project) => <div className="source-remove-project" key={project.project_id}><div><strong>{project.display_name}</strong><span title={project.project_path}>{project.project_path}</span></div><ul>{project.entries.map((entry) => <li key={`${entry.path}-${entry.skill_id}`}><span>{entry.name || entry.skill_id}</span><small>{entry.mode === "symlink" ? "软链接" : "受管副本"} · {projectEntryStateLabel(entry.state)}{entry.restores_external ? " · 将恢复原内容" : ""}</small></li>)}</ul></div>)}</div>}
            {removalPreview.reference_count > 0 && <label className="source-cleanup-choice"><input type="checkbox" disabled={Boolean(busy)} checked={cleanupReferences} onChange={(event) => setCleanupReferences(event.target.checked)} /><span><strong>同时清理受管引用</strong><small>默认移除上面列出的软链接和受管副本；关联过的外部 Skill 会恢复原内容。</small></span></label>}
            {blockedCleanup && <div className="confirm-danger" role="alert"><AlertTriangle size={16} /><span>有 {removalPreview.blocker_count} 个引用已被修改或替换，不能安全清理。请先在项目页面处理，或取消勾选并明确保留引用。</span></div>}
            {!cleanupReferences && removalPreview.reference_count > 0 && <div className="confirm-risk"><AlertTriangle size={16} /><span>这些引用会继续留在项目中，但来源移除后无法更新，并显示为“目录中已不存在”。</span></div>}
            {removalPreview.inaccessible_projects.length > 0 && <div className="confirm-risk"><AlertTriangle size={16} /><span>另有 {removalPreview.inaccessible_projects.length} 个已登记项目当前无法读取，系统不能确认其中是否引用了此来源。</span></div>}
            <div className="button-row align-end"><button className="button secondary" data-initial-focus disabled={Boolean(busy)} onClick={closeRemoval}>取消</button><button className="button danger" disabled={Boolean(busy) || blockedCleanup} onClick={() => void confirmRemoval()}>{busy === `source-remove-${removalPreview.source.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}{removalPreview.reference_count === 0 ? "移除来源" : cleanupReferences ? "清理引用并移除" : "保留引用并移除"}</button></div>
          </div>
        </div>
      )}
      {forgetPreview && (
        <div className="modal-backdrop" role="presentation" onMouseDown={closeForget}>
          <div ref={forgetDialogRef} className="confirm-modal source-remove-modal" role="dialog" aria-modal="true" aria-labelledby="source-forget-title" aria-describedby="source-forget-description" onMouseDown={(event) => event.stopPropagation()}>
            <div className="confirm-icon danger"><Trash2 size={22} /></div>
            <h2 id="source-forget-title">彻底移除“{forgetPreview.source.name}”的目录记录</h2>
            <p id="source-forget-description">这会永久删除 SQLite 中的来源历史、Skill、分类评分、审查和评测记录。仓库文件绝不会被删除，但目录历史无法自动恢复。</p>
            <div className="confirm-summary source-remove-summary">
              <span>历史 Skill</span><strong>{forgetPreview.skill_count}</strong>
              <span>分类 / 评分</span><strong>{forgetPreview.history.annotation_count}</strong>
              <span>AI 评测</span><strong>{forgetPreview.history.evaluation_count}</strong>
              <span>静态审查确认</span><strong>{forgetPreview.history.audit_review_count}</strong>
              <span>仓库目录</span><strong title={forgetPreview.repository_path}>{forgetPreview.repository_exists ? "仍存在并保留" : "已不存在"} · {forgetPreview.repository_path}</strong>
            </div>
            {forgetPreview.profile_locator_count > 0 && <div className="confirm-risk"><AlertTriangle size={16} /><span>{forgetPreview.profile_locator_count} 个配置集条目会保留名称、来源和相对路径，仅清除失效的内部 Skill ID，以便将来重新匹配。</span></div>}
            {forgetPreview.reference_count > 0 && <div className="confirm-danger" role="alert"><AlertTriangle size={16} /><span>仍有 {forgetPreview.reference_count} 个受管项目引用。请先在项目页面卸载这些 Skill，系统不会制造悬空引用。</span></div>}
            {forgetPreview.inaccessible_projects.length > 0 && <div className="confirm-danger" role="alert"><AlertTriangle size={16} /><span>有 {forgetPreview.inaccessible_projects.length} 个已登记项目当前无法读取，无法确认引用安全；请先重新连接或移除对应项目记录。</span></div>}
            {!blockedForget && <label className="source-forget-confirmation"><span>输入 <strong>{forgetPreview.source.name}</strong> 确认彻底移除目录记录</span><input data-initial-focus value={forgetConfirmation} onChange={(event) => setForgetConfirmation(event.target.value)} autoComplete="off" /></label>}
            <div className="button-row align-end"><button className="button secondary" disabled={Boolean(busy)} onClick={closeForget}>取消</button><button className="button danger" disabled={Boolean(busy) || blockedForget || forgetConfirmation !== forgetPreview.source.name} onClick={() => void confirmForget()}>{busy === `source-forget-${forgetPreview.source.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}彻底移除 SQLite 记录</button></div>
          </div>
        </div>
      )}
    </div></Localized>
  );
}

function signedScore(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}`;
}

function EvaluationRunSummary({ run }: { run: LLMEvaluationRun }) {
  const noteworthy = run.results.filter((item) =>
    item.status === "error" ||
    (item.score_delta != null && item.score_delta !== 0) ||
    (item.previous_score == null && item.name_conflicts.length > 0) ||
    item.recommendation === "ignore"
  );
  return (
    <Localized><div className={`refresh-summary ${run.failed ? "with-failures" : ""}`} role="status">
      <div className="refresh-summary-heading">
        {run.failed ? <AlertTriangle size={17} /> : <Check size={17} />}
        <div>
          <strong>本次评测已完成</strong>
          <p>已处理 {run.requested} 个 · {run.proposed} 个进入审核 · {run.unchanged} 个评分未变化（已记录） · {run.attention} 个提醒 · {run.failed} 个失败</p>
        </div>
      </div>
      {noteworthy.length > 0 && (
        <div className="evaluation-run-details">
          {noteworthy.slice(0, 8).map((item) => (
            <div className="evaluation-run-detail" key={item.id}>
              <strong>{item.skill_name}</strong>
              <div>
                {item.status === "error" && <span>{item.error || "评测失败"}</span>}
                {item.previous_score != null && item.score != null && item.score_delta != null && item.score_delta !== 0 && (
                  <span>评分 {item.previous_score.toFixed(1)} → {item.score.toFixed(1)}（{signedScore(item.score_delta)}）</span>
                )}
                {item.previous_score == null && item.name_conflicts.length > 0 && (
                  <span>名称与 {item.name_conflicts.map((conflict) => `${conflict.name} / ${conflict.source_name}`).join("、")} 冲突</span>
                )}
                {item.recommendation === "ignore" && (
                  <span>建议忽略：现有 {item.comparison.matched_skill_name || "Skill"} 已完整覆盖且评分更高</span>
                )}
              </div>
            </div>
          ))}
          {noteworthy.length > 8 && <small>另有 {noteworthy.length - 8} 项，请在 LLM 评测页查看。</small>}
        </div>
      )}
    </div></Localized>
  );
}

function EvaluationProposalCard({ proposal, busy, onApply, onReject }: {
  proposal: LLMEvaluation;
  busy: string | null;
  onApply: (evaluationId: string, replaceExisting: boolean) => void;
  onReject: (evaluationId: string) => void;
}) {
  const comparison = proposal.comparison;
  return (
    <Localized><article className={`proposal-card ${proposal.recommendation === "ignore" ? "ignore-recommended" : ""}`}>
      <div className="proposal-heading">
        <div><strong>{proposal.skill_name}</strong><span>{proposal.source_name} · {proposal.provider}{proposal.model ? `/${proposal.model}` : ""}</span></div>
        <div className="proposal-score"><strong>{proposal.score != null ? proposal.score.toFixed(1) : "—"}</strong><small>/ 10 质量分</small></div>
      </div>
      {proposal.previous_score != null && proposal.score != null && proposal.score_delta != null && (
        <div className="proposal-score-change">
          <span>评分变化</span>
          <strong>{proposal.previous_score.toFixed(1)} → {proposal.score.toFixed(1)}</strong>
          <i className={`badge ${proposal.score_delta > 0 ? "success" : "warning"}`}>{signedScore(proposal.score_delta)}</i>
        </div>
      )}
      <div className="proposal-category"><span>{proposal.category_l1}</span><ArrowRight size={13} /><span>{proposal.category_l2}</span>{proposal.category_candidate && <i className="badge warning">新二级分类候选</i>}</div>
      <p>{proposal.problem}</p>
      <small>{proposal.use_case}</small>
      {proposal.name_conflicts.length > 0 && (
        <div className="proposal-insight name-conflict">
          <AlertTriangle size={15} />
          <div>
            <strong>发现同名 Skill</strong>
            <p>{proposal.name_conflicts.map((conflict) => translate(
              `${conflict.name}（${conflict.source_name}${conflict.score != null ? `，${conflict.score.toFixed(1)} 分` : "，未评分"}）`,
              `${conflict.name} (${conflict.source_name}, ${conflict.score != null ? `${conflict.score.toFixed(1)} points` : "unscored"})`,
            )).join(translate("；", "; "))}</p>
            <small>请先确认它们是否是重复来源、分支版本或不同实现。</small>
          </div>
        </div>
      )}
      {proposal.recommendation === "ignore" && (
        <div className="proposal-insight ignore-advice">
          <ShieldCheck size={15} />
          <div>
            <strong>建议忽略此 Skill</strong>
            <p>{translate(
              `现有 ${comparison.matched_skill_name || "Skill"}（${comparison.matched_source_name || "未知来源"}，${comparison.existing_score?.toFixed(1) ?? "—"} 分）在本地能力比对中完整覆盖本 Skill，且评分高于当前 ${proposal.score?.toFixed(1) ?? "—"} 分。`,
              `Existing ${comparison.matched_skill_name || "Skill"} (${comparison.matched_source_name || "unknown source"}, ${comparison.existing_score?.toFixed(1) ?? "—"} points) fully covers this Skill in the local capability comparison and scores higher than the current ${proposal.score?.toFixed(1) ?? "—"} points.`,
            )}</p>
            {comparison.matched_capabilities?.length ? <small>{translate(`覆盖能力：${comparison.matched_capabilities.join("、")}`, `Covered capabilities: ${comparison.matched_capabilities.join(", ")}`)}</small> : null}
            <small>这是审核建议，已写入评测记录；系统不会自动停用、删除或隐藏 Skill。</small>
          </div>
        </div>
      )}
      <div className="proposal-actions">
        <button className="button ghost compact" disabled={Boolean(busy)} onClick={() => onReject(proposal.id)}>{busy === `evaluation-reject-${proposal.id}` ? <LoaderCircle className="spin" size={14} /> : <X size={14} />}拒绝提案</button>
        <button className="button primary compact" disabled={Boolean(busy) || !proposal.current_content} onClick={() => onApply(proposal.id, proposal.has_annotation)}>{busy === `evaluation-apply-${proposal.id}` ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}{proposal.recommendation === "ignore" ? "仍然应用" : proposal.has_annotation ? "替换现有整理" : "应用提案"}</button>
      </div>
    </article></Localized>
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

function EvaluationView({ snapshot, busy, onSaveProfile, onActivate, onDisable, onDelete, onTest, onEvaluate, onClearErrors, onApply, onReject }: {
  snapshot: AppSnapshot;
  busy: string | null;
  onSaveProfile: (profile: LLMProfileFormValue, secret?: string) => Promise<boolean>;
  onActivate: (profileId: string) => Promise<boolean>;
  onDisable: () => Promise<boolean>;
  onDelete: (profileId: string) => Promise<boolean>;
  onTest: (profileId: string) => Promise<LLMProfileTestResult | null>;
  onEvaluate: (sourceId: string) => Promise<LLMEvaluationRun | null>;
  onClearErrors: () => Promise<boolean>;
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
  const [lastRun, setLastRun] = useState<LLMEvaluationRun | null>(null);
  const pendingSources = snapshot.sources.filter((source) => source.pending_evaluation_count > 0);
  const active = snapshot.llm.active_profile;
  const activeAvailable = Boolean(active && snapshot.llm.availability[active.provider]);
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
    const activeName = active?.name || translate("当前模型", "the active model");
    const confirmed = window.confirm(translate(
      `将调用 ${activeName} 评测 ${source.name}。单次最多处理 ${current.max_per_run} 个 Skill，可能消耗模型额度。是否继续？`,
      `${activeName} will evaluate ${source.name}, processing up to ${current.max_per_run} Skills in this run and potentially consuming model quota. Continue?`,
    ));
    if (confirmed) {
      setLastRun(null);
      void onEvaluate(source.id).then((result) => {
        if (result) setLastRun(result);
      });
    }
  };
  const testProfile = (profile: LLMProfile) => {
    if (
      profile.provider === "openai-compatible" &&
      !window.confirm(translate(
        "连接测试会访问该服务的 /models 接口，是否继续？",
        "The connection test will access this service's /models endpoint. Continue?",
      ))
    ) return;
    void onTest(profile.id).then((result) => {
      if (!result?.ok) return;
      const detail = result.executable ? `\n${result.executable}` : "";
      window.alert(translate(`连接测试通过${detail}`, `Connection test passed${detail}`));
    });
  };
  const clearErrors = async () => {
    if (!window.confirm(translate(
      `确定清空全部失败记录？当前列表显示最近 ${snapshot.llm.recent_errors.length} 条。待审核提案和已应用结果不会受到影响，这些 Skill 仍可重新评测。`,
      `Clear all failure history? The list currently shows the most recent ${snapshot.llm.recent_errors.length} entries. Pending proposals and applied results are unaffected, and these Skills can be evaluated again.`,
    ))) return;
    if (await onClearErrors()) setLastRun(null);
  };
  const apply = (evaluationId: string, replaceExisting: boolean) => {
    if (replaceExisting && !window.confirm(translate(
      "此操作会替换现有人工或 Arena 整理结果。确认继续？",
      "This will replace the existing human or Arena curation. Continue?",
    ))) return;
    void onApply(evaluationId, replaceExisting);
  };

  return (
    <Localized><div className="stack gap-lg evaluation-page">
      <section className="panel evaluator-settings">
        <div className="panel-heading"><div><span className="eyebrow">Provider profiles</span><h3>模型连接</h3></div><div className="button-row"><button className="button ghost compact" disabled={!active || Boolean(busy)} onClick={() => void onDisable()}>暂停评测</button><button className="button primary compact" disabled={Boolean(busy)} onClick={() => { if (!hasDraft) resetForm(); setShowForm(true); }}><Plus size={15} />{hasDraft ? "继续未保存连接" : "添加连接"}</button></div></div>
        <p className="muted">支持 Codex CLI、Claude Code 和 OpenAI-compatible API。API Key 只写入系统凭据库，不进入目录配置、命令参数或评测记录。</p>
        <div className="llm-profile-list">
          {current.profiles.map((profile) => {
            const selected = current.active_profile_id === profile.id;
            const available = snapshot.llm.availability[profile.provider];
            return <article className={`llm-profile-card ${selected ? "active" : ""}`} key={profile.id}>
              <div><span className="eyebrow">{profile.provider}</span><h4>{profile.name}</h4><p>{profile.model || "默认模型"}{profile.base_url ? ` · ${profile.base_url}` : ""}</p></div>
              <div className="llm-profile-state">
                <span className={`badge ${available ? (selected ? "success" : "neutral") : "warning"}`}>{selected ? (available ? "当前使用" : "当前不可用") : available ? "可用" : "未检测到"}</span>
                {profile.provider === "openai-compatible"
                  ? <small>{profile.credential_configured ? "已配置凭据" : "无凭据 / 本地服务"}</small>
                  : <small title={snapshot.llm.executables[profile.provider] || undefined}>{snapshot.llm.executables[profile.provider] || "未找到 CLI"}</small>}
              </div>
              <div className="profile-actions"><button className="text-button" disabled={Boolean(busy)} onClick={() => editProfile(profile)}>编辑</button><button className="text-button" disabled={Boolean(busy)} onClick={() => testProfile(profile)}>测试</button>{!selected && <button className="text-button" disabled={Boolean(busy)} onClick={() => void onActivate(profile.id)}>启用</button>}<button className="text-button danger" disabled={Boolean(busy)} onClick={() => { if (window.confirm(translate(`删除模型连接“${profile.name}”？项目评测记录会保留。`, `Delete model connection “${profile.name}”? Project evaluation history will be retained.`))) void onDelete(profile.id); }}><Trash2 size={13} />删除</button></div>
            </article>;
          })}
          {!current.profiles.length && <div className="history-empty"><Settings2 size={20} /><span>还没有模型连接。添加一个连接后才能对新 Skill 生成分类和评分提案。</span></div>}
        </div>
        {active && !activeAvailable && <div className="bootstrap-notice warning"><AlertTriangle size={15} /><span>当前连接“{active.name}”不可用，评测按钮已暂停。桌面 App 会自动查找 NVM、FNM、Volta、Homebrew 等常见安装目录；也可以改用 OpenAI-compatible API 连接。</span></div>}
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

      {lastRun && <EvaluationRunSummary run={lastRun} />}

      {pendingSources.length > 0 && (
        <section className="panel pending-sources">
          <div className="panel-heading"><div><span className="eyebrow">Evaluation queue</span><h3>按来源评测</h3></div></div>
          <div className="pending-source-list">{pendingSources.map((source) => <div className="pending-source-row" key={source.id}><div><strong>{source.name}</strong><span>{source.pending_evaluation_count} 个待评测 Skill</span></div><button className="button secondary compact" disabled={Boolean(busy) || current.provider === "disabled" || !activeAvailable} title={activeAvailable ? "调用当前模型生成评测提案" : "当前模型连接不可用"} onClick={() => evaluate(source)}>{busy === `evaluate-${source.id}` ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}开始评测</button></div>)}</div>
        </section>
      )}

      {snapshot.llm.recent_errors.length > 0 && (
        <section className="panel evaluation-errors">
          <div className="panel-heading"><div><span className="eyebrow">Recent failures</span><h3>最近评测失败</h3></div><div className="button-row"><span className="badge warning">最近 {snapshot.llm.recent_errors.length} 项</span><button className="button ghost compact" disabled={Boolean(busy)} onClick={() => void clearErrors()}>{busy === "llm-errors-clear" ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}清空失败记录</button></div></div>
          <p className="muted">失败记录会保留，方便定位连接、模型输出或分类校验问题；Skill 内容变化或再次评测后会写入新结果。</p>
          <div className="evaluation-error-list">{snapshot.llm.recent_errors.map((item) => <div className="evaluation-error-row" key={item.id}><div><strong>{item.skill_name}</strong><span>{item.source_name} · {formatDate(item.created_at)}</span></div><p title={item.error || undefined}>{item.error || "评测失败"}</p></div>)}</div>
        </section>
      )}

      <section className="panel proposal-panel">
        <div className="panel-heading"><div><span className="eyebrow">Human review gate</span><h3>评测提案</h3></div><span className="badge neutral">{snapshot.llm.proposals.length} 项</span></div>
        {snapshot.llm.proposals.length ? <div className="proposal-list">{snapshot.llm.proposals.map((proposal) => <EvaluationProposalCard proposal={proposal} busy={busy} onApply={apply} onReject={(id) => void onReject(id)} key={proposal.id} />)}</div> : <div className="history-empty"><Sparkles size={20} /><span>还没有待审核的 LLM 评测提案。</span></div>}
      </section>
    </div></Localized>
  );
}

function projectHistoryLabel(event: ProjectHistoryEvent): string {
  const labels = {
    apply: translate("应用 Skills", "Apply Skills"),
    adopt: translate("迁移为受管理软链接", "Migrate to managed symlink"),
    sync: translate("同步来源变更", "Sync source changes"),
    unlink: translate("移除 Skills", "Remove Skills"),
  };
  return translate(`${labels[event.action]} · ${event.count} 项`, `${labels[event.action]} · ${event.count} items`);
}

function ProjectsView({ library, categories, onError }: { library: string; categories: CategoryFilter[]; onError: (message: string | null) => void }) {
  const initialDraft = useMemo(() => loadProjectDraft(localStorage, library), [library]);
  const [screen, setScreen] = useState<"list" | "detail">("list");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [project, setProject] = useState(initialDraft.project);
  const [requirement, setRequirement] = useState(initialDraft.requirement);
  const [discoveryMode, setDiscoveryMode] = useState(initialDraft.discoveryMode);
  const [categoryL1, setCategoryL1] = useState(initialDraft.categoryL1);
  const [categoryL2, setCategoryL2] = useState(initialDraft.categoryL2);
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
  const [agentTargets, setAgentTargets] = useState<AgentTarget[]>([]);
  const [matrix, setMatrix] = useState<ActivationMatrix | null>(null);
  const [matrixQuery, setMatrixQuery] = useState("");
  const [profiles, setProfiles] = useState<SkillProfileSummary[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [profilePreview, setProfilePreview] = useState<SkillProfilePreview | null>(null);
  const [profileImportPreview, setProfileImportPreview] = useState<SkillProfileImportPreview | null>(null);
  const [profileTransferMessage, setProfileTransferMessage] = useState("");
  const [captureName, setCaptureName] = useState("");
  const [planLoading, setPlanLoading] = useState(false);
  const categoryRequestId = useRef(0);
  useEscapeKey(confirming, () => setConfirming(false));

  const categoryLevelOnes = useMemo(() => {
    const counts = new Map<string, number>();
    categories.forEach((item) => {
      if (!item.category_l1) return;
      counts.set(item.category_l1, (counts.get(item.category_l1) || 0) + item.count);
    });
    return Array.from(counts, ([name, count]) => ({ name, count })).sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  }, [categories]);
  const categoryLevelTwos = useMemo(() => categories
    .filter((item) => item.category_l1 === categoryL1 && item.category_l2)
    .map((item) => ({ name: item.category_l2 as string, count: item.count }))
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN")), [categories, categoryL1]);

  useEffect(() => {
    saveProjectDraft(localStorage, library, {
      project, requirement, discoveryMode, categoryL1, categoryL2, target, allowRisk,
    });
  }, [library, project, requirement, discoveryMode, categoryL1, categoryL2, target, allowRisk]);

  useEffect(() => {
    if (discoveryMode !== "category" || !project.trim() || !categoryL1) {
      setPlanLoading(false);
      return;
    }
    const requestId = ++categoryRequestId.current;
    setPlan(null);
    setSelected(new Set());
    setRiskConfirmed(false);
    setPlanLoading(true);
    const timer = window.setTimeout(() => {
      onError(null);
      void api.projectPlan(
        library,
        project,
        "",
        target,
        allowRisk,
        categoryL1,
        categoryL2,
      ).then((next) => {
        if (categoryRequestId.current !== requestId) return;
        setPlan(next);
      }).catch((reason) => {
        if (categoryRequestId.current !== requestId) return;
        onError(reason instanceof Error ? reason.message : String(reason));
      }).finally(() => {
        if (categoryRequestId.current === requestId) setPlanLoading(false);
      });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      if (categoryRequestId.current === requestId) categoryRequestId.current += 1;
    };
  }, [allowRisk, categoryL1, categoryL2, discoveryMode, library, onError, project, target]);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try { setProjects(await api.projectList(library)); }
    catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setProjectsLoading(false); }
  }, [library, onError]);

  const loadMatrix = useCallback(async (query = "") => {
    try { setMatrix(await api.projectMatrix(library, query, 20)); }
    catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); }
  }, [library, onError]);

  const loadProfiles = useCallback(async () => {
    try {
      const nextProfiles = await api.profileList(library);
      setProfiles(nextProfiles);
      setSelectedProfileId((current) =>
        current && nextProfiles.some((item) => item.id === current) ? current : "",
      );
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [library, onError]);

  const loadAgentTargets = useCallback(async () => {
    try { setAgentTargets(await api.agentTargets(library)); }
    catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); }
  }, [library, onError]);

  useEffect(() => {
    if (
      agentTargets.length > 0
      && target !== "auto"
      && target !== "root"
      && !agentTargets.some((item) => item.id === target)
    ) {
      setTarget("auto");
    }
  }, [agentTargets, target]);

  useEffect(() => {
    void loadProjects();
    void loadMatrix();
    void loadProfiles();
    void loadAgentTargets();
  }, [loadAgentTargets, loadMatrix, loadProfiles, loadProjects]);

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
    const selectedPath = await open({ directory: true, multiple: false, title: translate("选择要初始化的项目", "Choose a project to set up") });
    if (typeof selectedPath === "string") {
      setProject(selectedPath); setPlan(null); setStatus(null); setSelected(new Set()); setProfilePreview(null);
      await loadProjectContext(selectedPath, true);
    }
  };

  const openManagedProject = async (item: ProjectSummary) => {
    if (item.status !== "active") return;
    setProject(item.path); setTarget(item.project_kind === "system" ? "root" : "auto"); setPlan(null); setSelected(new Set()); setProfilePreview(null); setScreen("detail");
    await loadProjectContext(item.path, true);
  };

  const addProject = async () => {
    const selectedPath = await open({ directory: true, multiple: false, title: translate("选择需要使用 Skills 的代码项目", "Choose a code project that needs Skills") });
    if (typeof selectedPath !== "string") return;
    if (project && project !== selectedPath && !window.confirm(translate(
      "选择其他项目会替换当前项目草稿，是否继续？",
      "Choosing another project will replace the current project draft. Continue?",
    ))) return;
    await run("project-add", async () => {
      const selectedStatus = await api.projectStatus(library, selectedPath);
      setPlan(null); setSelected(new Set()); setRiskConfirmed(false); setProfilePreview(null);
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
    const selectedPath = await open({ directory: true, multiple: false, title: translate(`重新定位 ${item.display_name}`, `Relocate ${item.display_name}`) });
    if (typeof selectedPath !== "string") return;
    await run("project-relink", async () => {
      await api.projectRelink(library, item.id, selectedPath);
      await loadProjects();
    });
  };

  const forgetProject = async (item: ProjectSummary) => {
    if (!window.confirm(translate(
      `从列表中移除“${item.display_name}”？项目目录和 manifest 都会保留。`,
      `Remove “${item.display_name}” from the list? The project directory and manifest will be retained.`,
    ))) return;
    await run("project-forget", async () => {
      await api.projectForget(library, item.id);
      await loadProjects();
    });
  };

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label); onError(null);
    try { await action(); } catch (reason) { onError(reason instanceof Error ? reason.message : String(reason)); } finally { setBusy(null); }
  }

  const refreshControlState = async () => {
    await Promise.all([
      loadProjects(),
      loadMatrix(matrixQuery),
      loadProfiles(),
      loadAgentTargets(),
    ]);
  };

  const chooseAgentTargetDirectory = async (title: string): Promise<string | null> => {
    const selectedPath = await open({ directory: true, multiple: false, title });
    return typeof selectedPath === "string" ? selectedPath : null;
  };

  const addAgentTarget = async (
    input: CustomAgentTargetInput,
  ): Promise<boolean> => {
    let succeeded = false;
    await run("agent-target-add", async () => {
      await api.agentTargetAdd(library, input);
      await refreshControlState();
      succeeded = true;
    });
    return succeeded;
  };

  const removeAgentTarget = (agentTarget: AgentTarget) => {
    if (agentTarget.built_in) return;
    if (!window.confirm(translate(
      "移除自定义 Agent 目标“" + agentTarget.label + "”？\n\n只删除当前仓库中的 SQLite 配置，不会删除目标目录或任何文件。若仍有受管 Skill，系统会阻止移除。",
      `Remove custom agent target “${agentTarget.label}”?\n\nOnly its SQLite configuration in the current library will be removed. No target directory or file will be deleted. Removal is blocked while managed Skills remain.`,
    ))) return;
    void run("agent-target-remove", async () => {
      await api.agentTargetRemove(library, agentTarget.id);
      if (target === agentTarget.id) setTarget("auto");
      await refreshControlState();
    });
  };

  const searchMatrix = () => run("matrix-load", async () => {
    await loadMatrix(matrixQuery);
  });

  const openMatrixTarget = (matrixTarget: ActivationTarget) => {
    if (!matrixTarget.exists && !matrixTarget.detected) return;
    setProject(matrixTarget.path);
    setTarget("root");
    setPlan(null);
    setSelected(new Set());
    setProfilePreview(null);
    setScreen("detail");
    void loadProjectContext(matrixTarget.path, true);
  };

  const installFromMatrix = (
    row: ActivationRow,
    cell: ActivationCell,
    matrixTarget: ActivationTarget,
  ) => {
    const riskCopy = isElevatedRisk(cell.audit_severity)
      ? translate(
        " 该 Skill 带有高风险信号，本次操作会记录风险确认。",
        " This Skill has elevated-risk signals; the action will record risk confirmation.",
      )
      : "";
    if (!window.confirm(translate(
      "将“" + row.name + "”以受管软链接安装到 " + matrixTarget.label +
      "？\n目标：" + cell.path + "。" + riskCopy,
      `Install “${row.name}” as a managed symlink in ${matrixTarget.label}?\nTarget: ${cell.path}.${riskCopy}`,
    ))) return;
    void run("matrix-install", async () => {
      await api.projectApply(
        library,
        matrixTarget.path,
        [cell.skill_id],
        translate("Agent 全局安装矩阵", "Agent-global installation matrix"),
        "root",
        allowRisk,
      );
      await refreshControlState();
    });
  };

  const uninstallFromMatrix = (
    row: ActivationRow,
    cell: ActivationCell,
    matrixTarget: ActivationTarget,
  ) => {
    if (!cell.installed_skill_id) return;
    if (!window.confirm(translate(
      "从 " + matrixTarget.label + " 卸载“" + row.name +
      "”的受管软链接？目录仓库中的 Skill 不会被删除。",
      `Uninstall the managed symlink for “${row.name}” from ${matrixTarget.label}? The library copy will not be deleted.`,
    ))) return;
    void run("matrix-uninstall", async () => {
      await api.projectUnlink(
        library,
        matrixTarget.path,
        [cell.installed_skill_id as string],
      );
      await refreshControlState();
    });
  };

  const adoptFromMatrix = (
    row: ActivationRow,
    cell: ActivationCell,
    matrixTarget: ActivationTarget,
  ) => {
    if (!cell.adopt_skill_id) return;
    if (!window.confirm(translate(
      "将 " + matrixTarget.label + " 中外部已有的“" + row.name +
      "”迁移为受管理软链接？\n\n原目录会先备份到该 Agent 目录的 .adaptive-skills/external-backups/，" +
      "再链接到 ~/skills 中的所选版本；任何一步失败都会恢复原目录。" +
      (cell.content_match === false ? "\n\n注意：所选仓库版本与外部副本内容不同，原内容会完整保留在备份中。" : ""),
      `Migrate the external “${row.name}” in ${matrixTarget.label} to a managed symlink?\n\nThe original directory will first be backed up to .adaptive-skills/external-backups/ in that agent directory, then linked to the selected version in ~/skills. Any failure restores the original directory.${cell.content_match === false ? "\n\nNote: the selected repository version differs from the external copy. The original content will remain intact in the backup." : ""}`,
    ))) return;
    void run("matrix-adopt", async () => {
      await api.projectAdopt(
        library,
        matrixTarget.path,
        row.name,
        cell.adopt_skill_id as string,
        allowRisk,
        cell.content_match === false,
      );
      await refreshControlState();
    });
  };

  const selectProfile = (profileId: string) => {
    setSelectedProfileId(profileId);
    setProfilePreview(null);
    setProfileTransferMessage("");
  };

  const previewProfile = () => {
    if (!selectedProfileId || !project) return;
    void run("profile-preview", async () => {
      setProfilePreview(
        await api.profilePreview(
          library,
          selectedProfileId,
          project,
          target,
          allowRisk,
        ),
      );
    });
  };

  const applyProfile = () => {
    if (!profilePreview || !selectedProfileId) return;
    if (!window.confirm(translate(
      "将配置集“" + profilePreview.profile.name + "”应用到当前目标？\n" +
      profilePreview.counts.install + " 项将创建受管软链接，" +
      profilePreview.counts["already-installed"] + " 项保持不变。",
      `Apply profile “${profilePreview.profile.name}” to the current target?\n${profilePreview.counts.install} managed symlinks will be created; ${profilePreview.counts["already-installed"]} existing items will remain unchanged.`,
    ))) return;
    void run("profile-apply", async () => {
      await api.profileApply(
        library,
        selectedProfileId,
        project,
        target,
        allowRisk,
      );
      await loadProjectContext(project);
      await refreshControlState();
      setProfilePreview(
        await api.profilePreview(
          library,
          selectedProfileId,
          project,
          target,
          allowRisk,
        ),
      );
    });
  };

  const captureProfile = () => {
    if (!captureName.trim() || !project) return;
    void run("profile-capture", async () => {
      const captured = await api.profileCapture(
        library,
        project,
        captureName.trim(),
      );
      setCaptureName("");
      await loadProfiles();
      setSelectedProfileId(captured.id);
      setProfilePreview(
        await api.profilePreview(
          library,
          captured.id,
          project,
          target,
          allowRisk,
        ),
      );
    });
  };

  const deleteProfile = () => {
    const selectedProfile = profiles.find((item) => item.id === selectedProfileId);
    if (!selectedProfile) return;
    if (!window.confirm(translate(
      "删除配置集“" + selectedProfile.name +
      "”？已经安装到 Agent 或项目中的 Skill 不会被卸载。",
      `Delete profile “${selectedProfile.name}”? Skills already installed in agents or projects will not be uninstalled.`,
    ))) return;
    void run("profile-delete", async () => {
      await api.profileDelete(library, selectedProfile.id);
      setSelectedProfileId("");
      setProfilePreview(null);
      await loadProfiles();
    });
  };

  const chooseProfileImport = async () => {
    const selectedPath = await open({
      directory: false,
      multiple: false,
      title: translate("选择 Adaptive Skills 配置集", "Choose an Adaptive Skills profile"),
      filters: [{ name: translate("Adaptive Skills 配置集", "Adaptive Skills profile"), extensions: ["json"] }],
    });
    if (typeof selectedPath !== "string") return;
    void run("profile-import-preview", async () => {
      setProfileTransferMessage("");
      setProfileImportPreview(
        await api.profileImportPreview(library, selectedPath),
      );
    });
  };

  const importProfile = () => {
    if (!profileImportPreview?.can_import) return;
    void run("profile-import", async () => {
      const imported = await api.profileImport(
        library,
        profileImportPreview.path,
        profileImportPreview.sha256,
      );
      await loadProfiles();
      setSelectedProfileId(imported.profile.id);
      setProfilePreview(null);
      setProfileImportPreview(null);
      setProfileTransferMessage(
        imported.changed
          ? translate(
            `已导入“${imported.profile.name}”；尚未安装任何 Skill。`,
            `Imported “${imported.profile.name}”; no Skills have been installed yet.`,
          )
          : translate(
            "完全相同的配置集已存在，本次没有创建重复记录。",
            "An identical profile already exists; no duplicate record was created.",
          ),
      );
    });
  };

  const exportProfile = async () => {
    const selectedProfile = profiles.find((item) => item.id === selectedProfileId);
    if (!selectedProfile) return;
    const safeName = selectedProfile.name
      .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "-")
      .slice(0, 80) || "skill-profile";
    const destination = await saveFile({
      title: translate("导出 Adaptive Skills 配置集", "Export Adaptive Skills profile"),
      defaultPath: safeName + ".adaptive-skills.json",
      filters: [{ name: translate("Adaptive Skills 配置集", "Adaptive Skills profile"), extensions: ["json"] }],
    });
    if (typeof destination !== "string") return;
    void run("profile-export", async () => {
      const exported = await api.profileExport(
        library,
        selectedProfile.id,
        destination,
        true,
      );
      setProfileTransferMessage(
        translate(
          `已导出“${exported.profile.name}”到 ${exported.path}`,
          `Exported “${exported.profile.name}” to ${exported.path}`,
        ),
      );
    });
  };

  const createPlan = () => run("plan", async () => {
    const next = await api.projectPlan(library, project, requirement, target, allowRisk);
    setPlan(next); setSelected(new Set()); setRiskConfirmed(false);
    await loadProjectContext(project);
  });

  const refreshPlanIfPresent = async () => {
    if (!plan) return;
    const next = await api.projectPlan(
      library,
      project,
      discoveryMode === "requirement" ? requirement : "",
      target,
      allowRisk,
      discoveryMode === "category" ? categoryL1 : "",
      discoveryMode === "category" ? categoryL2 : "",
    );
    setPlan(next);
    setSelected((current) => new Set(
      Array.from(current).filter((skillId) => {
        const skill = next.recommendations.find((item) => item.id === skillId);
        return skill ? canSelectSkill(skill, allowRisk) : false;
      }),
    ));
  };

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
    await api.projectApply(library, project, Array.from(selected), plan?.requirement || requirement, target, allowRisk);
    await refreshPlanIfPresent(); await loadProjectContext(project); await refreshControlState(); setConfirming(false); setSelected(new Set());
  });
  const sync = (force = false) => run("sync", async () => { await api.projectSync(library, project, allowRisk, force); await refreshPlanIfPresent(); await loadProjectContext(project); await refreshControlState(); });
  const unlinkEntry = (skillId: string, force = false) => run("unlink", async () => { await api.projectUnlink(library, project, [skillId], force); await refreshPlanIfPresent(); await loadProjectContext(project); await refreshControlState(); });
  const adoptExternal = (entry: ProjectExternalEntry, match: ProjectExternalMatch) => {
    const preservesCopy = entry.entry_type === "directory";
    const versionWarning = match.content_match
      ? ""
      : translate(
        "\n\n注意：目录中的内容与所选仓库版本不同。迁移后会使用仓库版本，原内容仍完整保留在备份中。",
        "\n\nNote: the directory content differs from the selected repository version. The repository version will be used after migration, while the original remains intact in the backup.",
      );
    const message = preservesCopy
      ? translate(
        `将“${entry.name}”迁移为受管理软链接？\n\n原目录：${project}/${entry.path}\n备份位置：${project}/.adaptive-skills/external-backups/\n链接目标：${match.target_path}${versionWarning}\n\nAdaptive Skills 会先完成备份，再替换为软链接；任何一步失败都会恢复原目录。以后卸载时也会恢复这份备份。`,
        `Migrate “${entry.name}” to a managed symlink?\n\nOriginal directory: ${project}/${entry.path}\nBackup location: ${project}/.adaptive-skills/external-backups/\nLink target: ${match.target_path}${versionWarning}\n\nAdaptive Skills will complete the backup before replacing the directory with a symlink. Any failure restores the original, and uninstalling later restores this backup.`,
      )
      : translate(
        `将“${entry.name}”关联到目录中的 ${match.source_name}/${match.name}？现有软链接会登记为 Adaptive Skills 管理，之后可以同步或卸载。`,
        `Associate “${entry.name}” with ${match.source_name}/${match.name} in the library? The existing symlink will become Adaptive-managed and can then be synchronized or uninstalled.`,
      );
    if (!window.confirm(message)) return;
    void run("adopt", async () => {
      await api.projectAdopt(library, project, entry.name, match.id, allowRisk, !match.content_match);
      await refreshPlanIfPresent();
      await loadProjectContext(project);
      await refreshControlState();
    });
  };
  const requestSync = () => {
    if (!status) return;
    const forceCount = status.entries.filter((entry) => projectEntryRequiresForce(entry.state)).length;
    if (forceCount && !window.confirm(translate(
      `检测到 ${forceCount} 个项目内已修改或被替换的条目。强制同步会用目录中的 Skill 覆盖这些项目内容，且无法由 Adaptive Skills 恢复。确认继续？`,
      `${forceCount} project entries were modified or replaced. Force sync will overwrite them with library Skills, and Adaptive Skills cannot restore those changes. Continue?`,
    ))) return;
    void sync(forceCount > 0);
  };
  const requestUnlinkEntry = (entry: ProjectEntryStatus) => {
    const force = projectEntryRequiresForce(entry.state);
    const entryName = entry.name || entry.skill_id;
    const message = entry.restores_external && !force
      ? translate(
        `卸载“${entryName}”的受管链接，并恢复关联前保留的外部目录？目录中的来源不会被删除。`,
        `Uninstall the managed link for “${entryName}” and restore the external directory preserved during adoption? The library source will not be deleted.`,
      )
      : force
      ? translate(
        `“${entryName}”在项目内已有改动或被其他内容替换。强制移除会删除当前路径及其中改动，且无法由 Adaptive Skills 恢复。确认继续？`,
        `“${entryName}” was modified or replaced in the project. Force removal deletes the current path and its changes, which Adaptive Skills cannot restore. Continue?`,
      )
      : translate(
        `从项目中移除“${entryName}”的受管链接？Skill 来源不会被删除。`,
        `Remove the managed link for “${entryName}” from the project? The Skill source will not be deleted.`,
      );
    if (!window.confirm(message)) return;
    void unlinkEntry(entry.skill_id, force);
  };
  const changeDiscoveryMode = (next: ProjectDraft["discoveryMode"]) => {
    if (next === discoveryMode) return;
    setDiscoveryMode(next);
    setPlan(null);
    setSelected(new Set());
    setRiskConfirmed(false);
    setProfilePreview(null);
  };
  const clearDraft = () => {
    clearProjectDraft(localStorage, library);
    setProject(EMPTY_PROJECT_DRAFT.project);
    setRequirement(EMPTY_PROJECT_DRAFT.requirement);
    setDiscoveryMode(EMPTY_PROJECT_DRAFT.discoveryMode);
    setCategoryL1(EMPTY_PROJECT_DRAFT.categoryL1);
    setCategoryL2(EMPTY_PROJECT_DRAFT.categoryL2);
    setTarget(EMPTY_PROJECT_DRAFT.target);
    setAllowRisk(EMPTY_PROJECT_DRAFT.allowRisk);
    setPlan(null); setStatus(null); setHistory([]); setSelected(new Set()); setProfilePreview(null); setCaptureName("");
  };

  if (screen === "list") {
    return <Localized><div className="stack gap-lg project-index">
      <section className="panel project-index-hero">
        <div><span className="eyebrow">Managed projects</span><h2>项目 Skills 工作区</h2><p>本机 Agent 全局目录会作为系统项目自动出现；也可以添加普通代码项目，按需求挂载目录中的 Skills。</p></div>
        <button className="button primary" disabled={Boolean(busy)} onClick={() => void addProject()}>{busy === "project-add" ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}添加项目</button>
      </section>
      {project && !projects.some((item) => item.path === project) && <button className="panel project-draft-card" onClick={() => { setScreen("detail"); void loadProjectContext(project); }}><div><span className="badge warning">继续当前草稿</span><strong>{project}</strong><p>{discoveryMode === "category" ? categoryL1
        ? translate(`分类：${categoryL1}${categoryL2 ? ` / ${categoryL2}` : ""}`, `Category: ${categoryL1}${categoryL2 ? ` / ${categoryL2}` : ""}`)
        : "尚未选择分类" : requirement || "尚未填写需求"}</p></div><ArrowRight size={18} /></button>}
      <section className="project-index-grid">
        {projects.map((item) => <article className={`panel managed-project-card status-${item.status}`} key={item.id}>
          <button className="managed-project-main" disabled={item.status !== "active"} onClick={() => void openManagedProject(item)}><div className="managed-project-icon">{item.project_kind === "system" ? <Database size={18} /> : <Link2 size={18} />}</div><div><div className="project-card-badges">{item.project_kind === "system" && <span className="badge neutral">系统项目</span>}<span className={`badge ${item.status === "active" && item.provisioned && item.clean ? "success" : "warning"}`}>{item.project_kind === "system" && !item.provisioned ? "待初始化" : item.project_kind === "system" && item.status === "active" ? item.clean ? "已连接" : "有漂移" : item.status === "active" ? item.clean ? "已同步" : "有漂移" : item.status === "missing" ? "目录已移动" : "manifest 异常"}</span></div><h3>{item.display_name}</h3><p title={item.path}>{item.path}</p></div><ChevronRight size={17} /></button>
          <div className="managed-project-meta"><span>{item.entry_count} 个受管</span>{item.project_kind === "system" && <span>{item.external_count} 个外部已有</span>}{item.project_kind === "system" && !item.provisioned && <span>首次安装时创建目录</span>}<span>{item.history_count} 条操作</span><span>{formatDate(item.last_activity_at)}</span></div>
          <div className="managed-project-actions">{item.project_kind === "system" ? <span className="protected-project-note"><ShieldCheck size={13} />与初始化发现范围同步 · 不可删除</span> : <>{item.status !== "active" && <button className="text-button" onClick={() => void relinkProject(item)}><FolderOpen size={13} />重新定位</button>}<button className="text-button danger" onClick={() => void forgetProject(item)}><Trash2 size={13} />仅从列表移除</button></>}</div>
        </article>)}
      </section>
      {projectsLoading && <div className="history-empty"><LoaderCircle className="spin" size={18} /><span>正在读取项目列表…</span></div>}
      {!projectsLoading && !projects.length && <div className="project-placeholder compact-placeholder"><Link2 size={25} /><h3>还没有可用的项目</h3><p>检测到 Agent 全局 Skills 目录后会自动显示系统项目；也可以添加普通代码项目并按需挂载 Skills。</p></div>}
      <AgentTargetRegistryPanel
        targets={agentTargets}
        busy={busy}
        onAdd={addAgentTarget}
        onRemove={removeAgentTarget}
        onChooseDirectory={chooseAgentTargetDirectory}
      />
      <ProjectActivationMatrix
        matrix={matrix}
        query={matrixQuery}
        busy={busy}
        allowRisk={allowRisk}
        onQuery={setMatrixQuery}
        onSearch={searchMatrix}
        onAllowRisk={(value) => {
          setAllowRisk(value);
          setProfilePreview(null);
        }}
        onInstall={installFromMatrix}
        onUninstall={uninstallFromMatrix}
        onAdopt={adoptFromMatrix}
        onOpenTarget={openMatrixTarget}
      />
    </div></Localized>;
  }

  return (
    <Localized><div className="project-layout">
      <section className="panel project-builder">
        <div className="project-draft-heading"><button className="text-button" type="button" onClick={() => { setScreen("list"); void loadProjects(); }}><ArrowLeft size={13} />项目列表</button><div className="step-label"><span>1</span> {status?.project_kind === "system" ? "Agent 全局映射" : "项目与发现方式"}</div>{status?.project_kind === "system" ? <span className="badge neutral">系统项目</span> : <button className="text-button" type="button" onClick={clearDraft}><Trash2 size={13} />清空草稿</button>}</div>
        <h2>{status?.project_kind === "system" ? "管理 Agent 全局 Skills" : "按项目选择 Skills"}</h2><p className="muted">{status?.project_kind === "system" ? "系统项目始终保留；可以卸载受管 Skill，外部已有内容默认只读。" : "按需求检索或按分类浏览；只有被勾选的 Skill 才会创建软链接。"}</p>
        {status && status.project_kind === "project" && !status.managed && <div className="project-setup-notice"><Sparkles size={16} /><span>这是尚未接入 Adaptive Skills 的普通项目。首次应用 Skill 后会创建 manifest，并加入项目历史列表。</span></div>}
        {status?.project_kind === "system" && <div className="project-setup-notice">{status.provisioned ? <ShieldCheck size={16} /> : <Sparkles size={16} />}<span>{status.provisioned ? "此映射来自初始化的 Discovery Scope，与本机 Agent 全局目录保持一致，不能从项目列表移除或重新定位。" : "已检测到此 Agent，但全局 Skills 目录尚未创建；首次安装 Skill 时会自动安全创建。"}</span></div>}
        <label className="input-field"><span>{status?.project_kind === "system" ? "Agent 全局 Skills 目录" : "项目目录"}</span><div className={status?.project_kind === "system" ? "input-with-button locked" : "input-with-button"}><input value={project} readOnly={status?.project_kind === "system"} onChange={(event) => { setProject(event.target.value); setPlan(null); setSelected(new Set()); }} placeholder="/path/to/project" />{status?.project_kind !== "system" && <button type="button" onClick={chooseProject}><FolderOpen size={17} /></button>}</div></label>
        <div className="input-field discovery-method-field"><span>查找方式</span><div className="discovery-method" role="group" aria-label="选择 Skill 查找方式"><button type="button" className={discoveryMode === "requirement" ? "active" : ""} aria-pressed={discoveryMode === "requirement"} onClick={() => changeDiscoveryMode("requirement")}><Search size={14} />需求检索</button><button type="button" className={discoveryMode === "category" ? "active" : ""} aria-pressed={discoveryMode === "category"} onClick={() => changeDiscoveryMode("category")}><Layers3 size={14} />分类浏览</button></div></div>
        {discoveryMode === "requirement" ? <label className="input-field"><span>项目需要什么能力？</span><textarea value={requirement} onChange={(event) => { setRequirement(event.target.value); setPlan(null); setSelected(new Set()); }} rows={5} placeholder="例如：根据技术方案制作结构清晰的中文演示文稿，并检查视觉一致性。" /></label> : <div className="category-browser"><div className="two-columns"><label className="input-field"><span>一级分类</span><select value={categoryL1} onChange={(event) => { setCategoryL1(event.target.value); setCategoryL2(""); }}><option value="" disabled>选择一级分类</option>{categoryLevelOnes.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.count}</option>)}</select></label><label className="input-field"><span>二级分类</span><select value={categoryL2} disabled={!categoryL1} onChange={(event) => setCategoryL2(event.target.value)}><option value="">全部二级分类</option>{categoryLevelTwos.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.count}</option>)}</select></label></div><p className="category-auto-state">{!project.trim() ? "先选择项目目录，分类结果会显示在右侧" : planLoading ? <><LoaderCircle className="spin" size={13} />正在读取分类…</> : categoryL1 ? <><Check size={13} />分类变化后，右侧会自动更新</> : "当前目录还没有可浏览的分类"}</p></div>}
        <div className="two-columns"><label className="input-field"><span>目标 Agent</span><select value={target} disabled={status?.project_kind === "system"} onChange={(event) => { setTarget(event.target.value as ProjectDraft["target"]); setPlan(null); setSelected(new Set()); setProfilePreview(null); }}><option value="root" disabled={status?.project_kind !== "system"}>当前 Agent 全局目录</option>{agentTargets.map((item) => <option key={item.id} value={item.id === "agents" ? "auto" : item.id}>{item.label} · {item.project_path}</option>)}</select></label><label className="risk-toggle"><input type="checkbox" checked={allowRisk} onChange={(event) => { setAllowRisk(event.target.checked); setPlan(null); setSelected(new Set()); setProfilePreview(null); }} /><span><ShieldAlert size={17} /><strong>显示高风险结果</strong><small>应用前仍需二次确认</small></span></label></div>
        {discoveryMode === "requirement" && <button className="button primary wide" disabled={!project.trim() || !requirement.trim() || Boolean(busy)} onClick={() => void createPlan()}>{busy === "plan" ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />}生成推荐方案</button>}
      </section>

      <section className="project-results">
        {planLoading ? (
          <div className="project-placeholder recommendation-placeholder recommendation-loading" role="status">
            <div className="recommendation-placeholder-heading"><div className="recommendation-placeholder-icon"><LoaderCircle className="spin" size={18} /></div><div><h3>正在读取分类</h3><p>正在从本地 SQLite 目录中整理“{categoryL2 || categoryL1}”下可用于当前 Agent 的 Skills。</p></div></div>
          </div>
        ) : !plan ? (
          <div className="project-placeholder recommendation-placeholder">
            <div className="recommendation-placeholder-heading"><div className="recommendation-placeholder-icon"><Sparkles size={18} /></div><div><h3>Skills 将在这里显示</h3><p>可以描述项目需求生成匹配方案，也可以切换到“分类浏览”，选择分类后直接查看本地 Skills。</p></div></div>
            <div className="recommendation-preview" aria-label="结果包含的信息"><span><Search size={13} />需求匹配</span><span><Layers3 size={13} />分类浏览</span><span><CircleGauge size={13} />质量评分</span><span><ShieldCheck size={13} />风险提示</span></div>
          </div>
        ) : (
          <div className="stack gap-md">
            <div className="result-heading"><div><span className="eyebrow">Step 2 · Review</span><h2>{plan.discovery_mode === "category"
              ? translate(`“${plan.category_l2 || plan.category_l1}”下的 ${plan.recommendations.length} 个 Skills`, `${plan.recommendations.length} Skills under “${plan.category_l2 || plan.category_l1}”`)
              : translate(`推荐 ${plan.recommendations.length} 个 Skills`, `${plan.recommendations.length} recommended Skills`)}</h2><p className="recommendation-scope" title={plan.library_root}>{plan.discovery_mode === "category"
                ? translate(`分类浏览 · ${plan.category_l1}${plan.category_l2 ? ` / ${plan.category_l2}` : ""}`, `Browse categories · ${plan.category_l1}${plan.category_l2 ? ` / ${plan.category_l2}` : ""}`)
                : "需求检索"} · 当前 Skill 仓库：{plan.library_root}</p></div><span className="selection-count">{translate(`已选择 ${selected.size}`, `${selected.size} selected`)}</span></div>
            {plan.recommendations.length ? <div className="recommendation-list">
              {plan.recommendations.map((skill, index) => {
                const selectable = canSelectSkill(skill, allowRisk);
                const projectState = skill.project_selection_state ?? "available";
                const projectBlocked = projectState !== "available";
                const stateDetail = skill.project_entry_state
                  ? projectEntryStateLabel(skill.project_entry_state)
                  : null;
                const blockedLabel = projectSelectionStateLabel(projectState);
                const blockedTitle = projectBlocked
                  ? `${blockedLabel}${stateDetail ? ` · ${stateDetail}` : ""}${skill.project_entry_path ? ` · ${skill.project_entry_path}` : ""}`
                  : undefined;
                return (
                  <button className={`recommendation ${selected.has(skill.id) ? "selected" : ""} ${!selectable ? "disabled" : ""} ${projectBlocked ? "already-added" : !selectable ? "unavailable" : ""}`} key={skill.id} onClick={() => toggle(skill)} disabled={!selectable} title={blockedTitle}>
                    <span className="rank">{String(index + 1).padStart(2, "0")}</span><span className={`checkbox ${projectBlocked ? "installed" : ""}`}>{(projectBlocked || selected.has(skill.id)) && <Check size={14} />}</span><span className="recommendation-body"><span className="recommendation-title"><strong>{skill.name}</strong>{projectBlocked && <i className={projectState === "installed" ? "badge success" : "badge warning"}>{blockedLabel}{stateDetail && skill.project_entry_state !== "clean" ? ` · ${stateDetail}` : ""}</i>}<i className={`badge risk-${skill.audit_severity}`}>{riskLabel(skill.audit_severity)}</i>{skill.annotation_score != null && plan.discovery_mode !== "category" && <i className="badge neutral">质量 {skill.annotation_score.toFixed(1)}/10</i>}{(skill.variant_count ?? 0) > 1 && <i className="badge neutral">已归并 {skill.variant_count} 个适配版本</i>}<GitHubStars url={skill.source_url} stars={skill.source_stars} className="recommendation-stars" /></span><p>{skill.description}</p><small>{plan.discovery_mode === "category" ? `${skill.category_l1}${skill.category_l2 ? ` / ${skill.category_l2}` : ""} · ${skill.source_name}` : skill.reason?.slice(0, 3).map((reason) => `${reason.field}: ${reason.terms.join("/") || reason.contribution}`).join(" · ") || skill.source_name}</small></span><span className="recommendation-score" title={plan.discovery_mode === "category" ? "按智能评分降序展示，未评分的 Skill 按名称排列" : "需求匹配排序分，不是 0–10 质量分"}><small>{plan.discovery_mode === "category" ? "质量" : "匹配"}</small>{plan.discovery_mode === "category" ? skill.annotation_score?.toFixed(1) || "—" : skill.score?.toFixed(1) || "—"}</span>
                  </button>
                );
              })}
            </div> : <div className="category-empty"><Layers3 size={20} /><strong>这个分类暂无可展示的 Skill</strong><span>可能是分类内没有有效 Skill，或高风险结果当前被隐藏。可以选择上一级分类或开启风险结果后重试。</span></div>}
            <button className="button primary wide" disabled={!selected.size || Boolean(busy)} onClick={() => setConfirming(true)}><Link2 size={17} />{translate(`预览并应用 ${selected.size} 个 Skills`, `Preview and apply ${selected.size} Skills`)}</button>
          </div>
        )}

        {status && status.entries.length > 0 && (
          <section className="panel project-status-panel">
            <div className="panel-heading"><div><span className="eyebrow">Adaptive managed</span><h3>Adaptive 管理</h3></div><span className={status.clean ? "badge success" : "badge warning"}>{status.clean ? "全部同步" : "检测到漂移"}</span></div>
            <div className="manifest-list">{status.entries.map((entry) => <div className="manifest-row" key={entry.skill_id}><div className={`state-icon ${entry.state === "clean" ? "clean" : "drift"}`}>{entry.state === "clean" ? <Check size={14} /> : <AlertTriangle size={14} />}</div><div><strong>{entry.name || entry.skill_id}</strong><span>{entry.path} · {entry.mode}{entry.restores_external ? " · 卸载时恢复原目录" : ""}</span></div><span className="entry-state">{projectEntryStateLabel(entry.state)}</span><button aria-label={translate(`卸载 ${entry.name || entry.skill_id}`, `Uninstall ${entry.name || entry.skill_id}`)} title={projectEntryRequiresForce(entry.state) ? "强制移除已改动的受管条目" : entry.restores_external ? "卸载软链接并恢复原目录" : "卸载受管链接"} disabled={Boolean(busy)} onClick={() => requestUnlinkEntry(entry)}><Unlink size={15} /></button></div>)}</div>
            {status.entries.some((entry) => entry.state === "catalog-missing") && <div className="project-drift-warning"><AlertTriangle size={16} /><span>有条目已不在 Skills 目录中，无法同步。确认项目内容后，请先用右侧按钮从 manifest 移除。</span></div>}
            {status.entries.some((entry) => projectEntryCanSync(entry.state)) && <button className={`button wide ${status.entries.some((entry) => projectEntryRequiresForce(entry.state)) ? "warning" : "secondary"}`} disabled={Boolean(busy)} onClick={requestSync}><RefreshCw size={16} />{status.entries.some((entry) => projectEntryRequiresForce(entry.state)) ? "确认并覆盖项目漂移" : "同步来源变更"}</button>}
          </section>
        )}

        {status?.project_kind === "system" && (
          <section className="panel project-status-panel external-skill-panel">
            <div className="panel-heading"><div><span className="eyebrow">External existing</span><h3>外部已有</h3></div><span className="badge neutral">{status.external_entries.length} 项 · 只读</span></div>
            <p className="external-panel-copy">这里区分宿主自带与用户实体副本。Claude/Codex 自带 Skill 保持宿主管理；用户实体副本只有与目录中的同名 Skill 内容完全一致时，才可先备份再迁移为受管理软链接。</p>
            {status.external_entries.length ? <div className="external-skill-list">{status.external_entries.map((entry) => <div className="external-skill-row" key={entry.path}><div className="external-skill-state">{entry.management_state === "provider-owned" ? <ShieldCheck size={15} /> : <FolderOpen size={15} />}</div><div className="external-skill-copy"><div><strong>{entry.name}</strong><span className="badge neutral">{entry.management_state === "provider-owned" ? translate(`${entry.provider || "宿主"} 自带`, `${entry.provider || "Host"} provided`) : "外部已有"}</span><span className="badge neutral">{entry.entry_type === "symlink" ? "软链接" : "实体目录"}</span></div><small title={`${project}/${entry.path}`}>{entry.path}</small>{entry.management_state === "provider-owned" ? <p>{entry.protected_reason || "由宿主自行更新，Adaptive Skills 不迁移。"}</p> : entry.matches.length === 0 ? <p>目录中没有发现同名的仓库 Skill，保持外部只读。</p> : null}{entry.matches.map((match) => <div className="external-match" key={match.id}><span>{entry.migration_mode === "backup-and-link" ? "迁移目标" : "可关联"}：{match.source_name}/{match.name} · {match.content_match ? "内容一致" : "版本不同"}</span><button className="button secondary" disabled={Boolean(busy) || !entry.migratable && entry.migration_mode !== "associate-link" || !match.valid || (isElevatedRisk(match.audit_severity) && !allowRisk)} title={isElevatedRisk(match.audit_severity) && !allowRisk ? "请先开启“显示高风险结果”并完成风险确认" : entry.migration_mode === "backup-and-link" ? "确认后先备份原目录，再建立受管理软链接" : "关联后由 Adaptive Skills 管理更新"} onClick={() => adoptExternal(entry, match)}><Link2 size={13} />{busy === "adopt" ? "正在迁移…" : entry.migration_mode === "backup-and-link" ? "迁移为受管理软链接" : "关联并纳管"}</button></div>)}</div></div>)}</div> : <div className="history-empty"><ShieldCheck size={20} /><span>没有未纳管的外部 Skill。</span></div>}
          </section>
        )}

        {status && (
          <ProjectProfilesPanel
            profiles={profiles}
            selectedProfileId={selectedProfileId}
            preview={profilePreview}
            importPreview={profileImportPreview}
            transferMessage={profileTransferMessage}
            status={status}
            captureName={captureName}
            busy={busy}
            onSelect={selectProfile}
            onPreview={previewProfile}
            onApply={applyProfile}
            onDelete={deleteProfile}
            onExport={() => void exportProfile()}
            onImportChoose={() => void chooseProfileImport()}
            onImportConfirm={importProfile}
            onImportDismiss={() => setProfileImportPreview(null)}
            onCaptureName={setCaptureName}
            onCapture={captureProfile}
          />
        )}

        {project.trim() && (
          <section className="panel project-history-panel">
            <div className="panel-heading"><div><span className="eyebrow">Project activity</span><h3>操作历史</h3></div><button className="text-button" disabled={historyLoading || Boolean(busy)} onClick={() => void loadProjectContext(project, true)}><RefreshCw className={historyLoading ? "spin" : ""} size={14} />刷新</button></div>
            {history.length ? <div className="project-history-list">{history.map((event) => <div className="project-history-row" key={event.id}><div className={`history-icon action-${event.action}`}><History size={14} /></div><div><strong>{projectHistoryLabel(event)}</strong><span>{event.skill_names?.join("、") || "没有需要变更的 Skill"}</span>{event.requirement && <small>{event.requirement}</small>}</div><time>{formatDate(event.created_at)}</time></div>)}</div> : <div className="history-empty"><History size={20} /><span>{historyLoading ? "正在读取历史…" : "还没有成功的应用、关联、同步或移除记录。"}</span></div>}
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
    </div></Localized>
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
    <Localized><div className={`finding finding-${tone}`}>
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
    </div></Localized>
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
    <Localized><section className="drawer-section finding-section">
      <div className="section-title-row"><div><h3>{title}</h3><p>{description}</p></div><span>{findings.length}</span></div>
      {findings.length ? <div className="finding-list">{findings.map((finding, index) => <FindingRow key={`${finding.rule}-${finding.file}-${finding.line ?? index}`} finding={finding} tone={tone} busy={busy} onReview={onReview} />)}</div> : <div className="clean-callout"><ShieldCheck size={18} /><span>{empty}</span></div>}
    </section></Localized>
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
    <Localized><div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="skill-drawer" role="dialog" aria-modal="true" aria-labelledby="skill-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="drawer-header"><div><span className="eyebrow">{skill.source_name} / {skill.rel_path}</span><h2 id="skill-drawer-title">{skill.name}</h2></div><button className="icon-button" autoFocus aria-label="关闭 Skill 详情" onClick={onClose}><X size={18} /></button></div>
        <p className="drawer-description">{skill.description}</p>
        <div className="drawer-badges"><span className={`badge risk-${skill.audit_severity}`}>{riskLabel(skill.audit_severity)}</span><span className={skill.validation.length ? "badge warning" : "badge success"}>{skill.validation.length ? translate(
          `${skill.valid ? "格式提示" : "格式不兼容"} ${skill.validation.length}`,
          `${skill.valid ? "Format guidance" : "Format incompatible"} ${skill.validation.length}`,
        ) : "格式兼容"}</span>{(skill.capability_hint_count ?? 0) > 0 && <span className="badge neutral">能力提示 {skill.capability_hint_count}</span>}{skill.score != null && <span className="badge neutral">智能评分 {skill.score}</span>}<GitHubStars url={skill.source_url} stars={skill.source_stars} className="drawer-stars" /></div>
        <div className="detail-grid"><div><span>一级分类</span><strong>{skill.category_l1 || "未分类"}</strong></div><div><span>二级分类</span><strong>{skill.category_l2 || "未分类"}</strong></div><div><span>许可证</span><strong>{skill.license || "未声明"}</strong></div><div><span>来源提交</span><strong>{shortSha(skill.head_sha)}</strong></div></div>
        {(skill.problem || skill.use_case) && <section className="drawer-section"><h3>AI 整理</h3>{skill.problem && <div className="insight-block"><span>解决的问题</span><p>{skill.problem}</p></div>}{skill.use_case && <div className="insight-block"><span>应用场景</span><p>{skill.use_case}</p></div>}</section>}
        <FindingSection title="格式兼容性" description="只判断 SKILL.md 与 frontmatter 是否符合加载规范，不参与安全风险等级。" findings={skill.validation} tone="format" empty="格式兼容，未发现阻止加载的问题。" />
        <FindingSection title="能力提示" description="来自文档描述或禁止名单，说明 Skill 涉及的能力，不作为真实风险。" findings={capabilityHints} tone="hint" empty="没有额外的敏感能力提示。" />
        <FindingSection title="未确认风险" description="实际命令或文件行为命中的保守规则；在审查前参与整体风险等级。" findings={unconfirmedRisks} tone="risk" empty="没有等待人工确认的真实风险。" busy={busy} onReview={onReview} />
        <FindingSection title="确认风险" description="人工确认是真实行为的风险，继续参与整体风险等级和项目门禁。" findings={confirmedRisks} tone="confirmed" empty="没有已确认风险。" busy={busy} onReview={onReview} />
        {excludedFindings.length > 0 && <FindingSection title="已排除误报" description="审查结论绑定当前源码摘要；源码变化后会自动回到未确认风险。" findings={excludedFindings} tone="excluded" empty="没有已排除误报。" busy={busy} onReview={onReview} />}
        <section className="drawer-section"><div className="section-title-row"><h3>SKILL.md</h3><span>{skill.skill_md_path}</span></div><pre className="skill-content">{skill.body || "（正文为空）"}</pre></section>
      </aside>
    </div></Localized>
  );
}

function ActivityToast({ label }: { label: string }) {
  const messages: Record<string, string> = {
    "source-add": "正在 Clone、扫描并建立评测队列…",
    "source-reconcile": "正在发现并扫描手动加入的 Git 仓库…",
    "refresh-all": "正在逐个更新并扫描全部来源…",
    "llm-config": "正在保存本地模型配置…",
    "llm-errors-clear": "正在清空失败评测记录…",
    "matrix-load": "正在读取各 Agent 的 Skill 安装状态…",
    "matrix-install": "正在为 Agent 安装受管 Skill 软链接…",
    "matrix-uninstall": "正在安全卸载 Agent 的受管 Skill…",
    "matrix-adopt": "正在将外部 Skill 关联为受管软链接…",
    "agent-target-add": "正在保存自定义 Agent 目标…",
    "agent-target-remove": "正在安全移除自定义 Agent 目标配置…",
    "profile-preview": "正在解析配置集与目标 Agent 的兼容性…",
    "profile-apply": "正在应用 Skill 配置集…",
    "profile-capture": "正在保存当前 Skill 组合…",
    "profile-delete": "正在删除配置集…",
    "profile-export": "正在导出可移植配置集…",
    "profile-import-preview": "正在验证配置集文件…",
    "profile-import": "正在保存配置集元数据…",
    plan: "正在匹配项目需求…",
    apply: "正在创建项目软链接…",
    sync: "正在同步项目链接…",
    unlink: "正在安全移除链接…",
  };
  const message = messages[label] || (label.startsWith("source-remove-preview-") ? "正在检查来源和受管引用…" : label.startsWith("source-remove-") ? "正在清理受管引用并移除来源…" : label.startsWith("source-restore-") ? "正在恢复并重新扫描来源…" : label.startsWith("audit-review-") ? "正在保存风险审查结论并重算等级…" : label.startsWith("evaluate-") ? "正在调用模型生成分类与评分提案…" : label.startsWith("evaluation-apply-") ? "正在应用评测提案…" : label.startsWith("evaluation-reject-") ? "正在拒绝评测提案…" : label.startsWith("update-") ? "正在更新并重新扫描来源…" : label.startsWith("scan-") ? "正在重新扫描来源…" : "正在执行本地操作…");
  return <Localized><div className="activity-toast"><LoaderCircle className="spin" size={17} /><span>{message}</span></div></Localized>;
}

export default App;
