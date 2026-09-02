import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Download, LoaderCircle, RefreshCw, Rocket, X } from "lucide-react";
import { Localized, translate, useLanguage } from "./i18n";
import {
  createTauriUpdateAdapter,
  EMPTY_DOWNLOAD_PROGRESS,
  installConfirmedUpdate,
  nextDownloadProgress,
  performUpdateCheck,
  recordUpdateCheck,
  shouldRunAutomaticCheck,
  type DownloadProgress,
  type UpdateAdapter,
  type UpdateLike,
} from "./update";

type CheckState = "idle" | "checking" | "current" | "available" | "unsupported" | "error";

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UpdateCenter() {
  const { language } = useLanguage();
  const adapter = useMemo<UpdateAdapter | null>(() => createTauriUpdateAdapter(), []);
  const [state, setState] = useState<CheckState>("idle");
  const [currentVersion, setCurrentVersion] = useState<string>("—");
  const [update, setUpdate] = useState<UpdateLike | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [progress, setProgress] = useState<DownloadProgress>(EMPTY_DOWNLOAD_PROGRESS);
  const [message, setMessage] = useState<string | null>(null);
  const updateRef = useRef<UpdateLike | null>(null);

  const replaceUpdate = useCallback((next: UpdateLike | null) => {
    const previous = updateRef.current;
    updateRef.current = next;
    setUpdate(next);
    if (previous && previous !== next) void previous.close?.();
  }, []);

  const runCheck = useCallback(async (manual: boolean) => {
    if (!adapter || state === "checking" || installing) return;
    setState("checking");
    setMessage(null);
    try {
      if (!await adapter.supportsUpdates()) {
        setState("unsupported");
        return;
      }
      const candidate = await performUpdateCheck(adapter);
      replaceUpdate(candidate);
      if (candidate) {
        setCurrentVersion(candidate.currentVersion);
        setState("available");
        if (manual) setDialogOpen(true);
      } else {
        setCurrentVersion(await adapter.currentVersion());
        setState("current");
        if (manual) setMessage(translate("当前已是最新版本", "You are up to date"));
      }
    } catch (reason) {
      setState("error");
      if (manual) {
        setMessage(reason instanceof Error ? reason.message : String(reason));
        setDialogOpen(true);
      }
    } finally {
      try { recordUpdateCheck(localStorage); } catch { /* best effort */ }
    }
  }, [adapter, installing, replaceUpdate, state]);

  useEffect(() => {
    if (!adapter) return;
    let cancelled = false;
    let timer: number | undefined;
    void (async () => {
      try { setCurrentVersion(await adapter.currentVersion()); } catch { /* best effort */ }
      if (cancelled) return;
      try {
        if (!await adapter.supportsUpdates()) {
          if (!cancelled) setState("unsupported");
          return;
        }
      } catch {
        if (!cancelled) setState("error");
        return;
      }
      let shouldCheck = false;
      try { shouldCheck = shouldRunAutomaticCheck(localStorage); } catch { shouldCheck = true; }
      if (shouldCheck && !cancelled) {
        timer = window.setTimeout(() => void runCheck(false), 2500);
      }
    })();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [adapter, runCheck]);

  useEffect(() => () => { void updateRef.current?.close?.(); }, []);

  const install = async () => {
    if (!adapter || !update || installing) return;
    setInstalling(true);
    setMessage(null);
    setProgress(EMPTY_DOWNLOAD_PROGRESS);
    try {
      await installConfirmedUpdate(adapter, update, (event) => {
        setProgress((current) => nextDownloadProgress(current, event));
      });
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : String(reason));
      setInstalling(false);
    }
  };

  if (!adapter) return null;
  const statusLabel = state === "available"
    ? translate(`发现 v${update?.version}`, `v${update?.version} available`)
    : state === "checking"
      ? translate("正在检查…", "Checking…")
      : state === "error"
        ? translate("检查失败", "Check failed")
        : state === "unsupported"
          ? translate("请通过包管理器更新", "Update via package manager")
        : state === "current"
          ? translate("已是最新版", "Up to date")
          : translate(`当前 v${currentVersion}`, `Current v${currentVersion}`);

  return <Localized>
    <div className={`update-center ${state === "available" ? "available" : ""}`}>
      <button
        className="update-center-button"
        onClick={() => state === "available" ? setDialogOpen(true) : void runCheck(true)}
        disabled={state === "checking" || state === "unsupported" || installing}
        title="检查应用更新"
      >
        {state === "available" ? <Rocket size={14} /> : <RefreshCw size={14} className={state === "checking" ? "spin" : ""} />}
        <span><strong>应用更新</strong><small>{statusLabel}</small></span>
      </button>
      {message && !dialogOpen && <p role="status">{message}</p>}
    </div>

    {dialogOpen && (
      <div className="modal-backdrop" role="presentation" onMouseDown={() => !installing && setDialogOpen(false)}>
        <div className="confirm-modal update-modal" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
          <button className="update-modal-close" aria-label="关闭更新窗口" disabled={installing} onClick={() => setDialogOpen(false)}><X size={17} /></button>
          <div className="confirm-icon"><Download size={22} /></div>
          <h2 id="update-dialog-title">{update ? `Adaptive Skills v${update.version}` : "应用更新"}</h2>
          {update ? (
            <>
              <p>{translate(`当前版本 v${update.currentVersion}。下载完成后应用会重启以完成升级。`, `Current version: v${update.currentVersion}. The app will restart after the download to finish the update.`)}</p>
              {update.date && <small className="update-date">{new Date(update.date).toLocaleDateString(language)}</small>}
              {update.body && <pre className="update-notes">{update.body}</pre>}
              {installing && (
                <div className="update-progress" aria-live="polite">
                  <div><span style={{ width: `${progress.percent ?? 18}%` }} /></div>
                  <small>{progress.percent != null
                    ? `${progress.percent}%`
                    : translate(`已下载 ${formatBytes(progress.downloaded)}`, `Downloaded ${formatBytes(progress.downloaded)}`)}</small>
                </div>
              )}
              {message && <div className="update-error" role="alert">{message}</div>}
              <div className="button-row">
                <button className="button ghost" disabled={installing} onClick={() => setDialogOpen(false)}>稍后</button>
                <button className="button primary" disabled={installing} onClick={() => void install()}>
                  {installing ? <><LoaderCircle className="spin" size={15} /> 正在安装…</> : <><Download size={15} /> 安装并重启</>}
                </button>
              </div>
            </>
          ) : (
            <>
              <p>{message ?? "未发现可用更新。"}</p>
              {state === "current" && <div className="update-current"><CheckCircle2 size={16} /> 当前已是最新版本</div>}
              <div className="button-row"><button className="button secondary" onClick={() => setDialogOpen(false)}>关闭</button></div>
            </>
          )}
        </div>
      </div>
    )}
  </Localized>;
}
