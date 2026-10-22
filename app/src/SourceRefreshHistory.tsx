import { useEffect, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Copy, History, LoaderCircle, RefreshCw } from "lucide-react";
import type { SourceRefreshHistoryRecord } from "./drafts";
import type { SourceSummary } from "./types";
import { formatDate } from "./domain";
import { Localized, translate } from "./i18n";
import { sanitizeSourceError, sourceFailureKind, type SourceRefreshFailure } from "./sourceRefresh";

const guidance = {
  local_changes: {
    title: ["存在本地改动", "Local changes"],
    advice: ["先在仓库中检查并提交或暂存改动，再重试。若要长期保留自定义内容，可在来源卡片中设为本地维护。", "Review and commit or stash local changes before retrying. For ongoing customization, switch the source to local maintenance."],
  },
  local_source: {
    title: ["来源未配置远程更新", "Remote updates unavailable"],
    advice: ["检查来源的远程地址与维护策略；本地维护的来源可使用“重新扫描”。", "Check the source remote and maintenance policy. Use Rescan for locally maintained sources."],
  },
  network: {
    title: ["连接远程仓库失败", "Remote connection failed"],
    advice: ["检查网络、DNS、代理或证书配置，确认 Git 能访问远程地址后重试。", "Check the network, DNS, proxy, or certificate configuration, then retry once Git can reach the remote."],
  },
  access: {
    title: ["仓库访问失败", "Repository access failed"],
    advice: ["检查仓库地址、访问权限及 Git 凭据或 SSH 配置。私有仓库无权限时也可能返回“仓库不存在”。", "Check the repository URL, permissions, Git credentials, and SSH setup. Private repositories may appear missing when access is denied."],
  },
  diverged: {
    title: ["无法快进合并", "Fast-forward merge unavailable"],
    advice: ["本地与远程提交已分叉。请先在仓库中确认并处理分叉，再重试更新。", "Local and remote commits have diverged. Review and resolve the divergence in the repository before retrying."],
  },
  branch: {
    title: ["跟踪分支异常", "Tracking branch issue"],
    advice: ["检查远程分支是否已改名或删除，以及当前分支的上游设置，再重试。", "Check whether the remote branch was renamed or removed and verify the current branch's upstream before retrying."],
  },
  missing: {
    title: ["本地仓库不可用", "Local repository unavailable"],
    advice: ["检查目录是否被移动或删除。目录缺失时，可在来源卡片中重新 Clone 并扫描。", "Check whether the directory was moved or deleted. If missing, use Re-clone and scan on the source card."],
  },
  locked: {
    title: ["Git 仓库被占用", "Git repository busy"],
    advice: ["等待其他 Git 操作完成后重试；若仍失败，请先确认没有运行中的 Git 进程再处理锁文件。", "Wait for other Git operations to finish and retry. If it persists, verify no Git process is running before addressing the lock file."],
  },
  unknown: {
    title: ["更新或扫描失败", "Update or scan failed"],
    advice: ["展开错误详情确认原因；可复制详情排查，或查看来源的目录与配置。", "Expand the error details to investigate. Copy the details or inspect the source directory and configuration."],
  },
} as const;

type Props = {
  records: SourceRefreshHistoryRecord[];
  sources: SourceSummary[];
  busy: string | null;
  onRetry: (id: string) => Promise<void>;
};

function FailureItem({ failure, source, busy, onRetry }: {
  failure: SourceRefreshFailure;
  source?: SourceSummary;
  busy: string | null;
  onRetry: Props["onRetry"];
}) {
  const [retry, setRetry] = useState<{ ok: boolean; error?: string } | null>(null);
  const [retrying, setRetrying] = useState(false);
  const message = guidance[sourceFailureKind(failure.error)];
  const retryAllowed = Boolean(source?.url && source.update_policy === "remote" && source.repository_exists !== false);
  const retryUpdate = async () => {
    if (!retryAllowed || retrying || busy) return;
    setRetrying(true);
    setRetry(null);
    try {
      await onRetry(failure.source_id);
      setRetry({ ok: true });
    } catch (error) {
      setRetry({ ok: false, error: sanitizeSourceError(error instanceof Error ? error.message : String(error)) });
    } finally {
      setRetrying(false);
    }
  };
  return <Localized><li className="source-failure-item">
    <div className="source-failure-heading">
      <div><strong>{failure.source}</strong><span className="badge warning">{translate(message.title[0], message.title[1])}</span></div>
      <div className="button-row">
        {source && <button className="button ghost compact" onClick={() => {
          const card = document.getElementById(`source-${source.id}`);
          card?.scrollIntoView({ block: "center" });
          card?.focus({ preventScroll: true });
        }}>查看来源</button>}
        {retryAllowed && <button className="button secondary compact" disabled={Boolean(busy) || retrying} onClick={() => void retryUpdate()}>
          {retrying ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
          {retrying ? "正在重试…" : "重试此来源"}
        </button>}
      </div>
    </div>
    <p>{translate(message.advice[0], message.advice[1])}</p>
    {!source && <p>来源已移除或不在当前目录中，保留此条历史供排查。</p>}
    <details className="source-error-details">
      <summary>错误详情{failure.type ? ` · ${failure.type}` : ""}</summary>
      <pre>{failure.error || translate("未返回错误详情")}</pre>
    </details>
    {retry && <div className={`source-retry-result ${retry.ok ? "success" : "failed"}`} role="status">
      {retry.ok ? <Check size={15} /> : <AlertTriangle size={15} />}
      <div><strong>{retry.ok ? "本次重试成功；原记录保留当时的失败信息。" : "本次重试失败"}</strong>
        {retry.error && <pre>{retry.error}</pre>}
      </div>
    </div>}
  </li></Localized>;
}

export default function SourceRefreshHistory({ records, sources, busy, onRetry }: Props) {
  const latestId = records[0]?.id;
  const latestFailed = records[0]?.failed;
  const [expanded, setExpanded] = useState<string | null>(latestFailed ? latestId : null);
  const [copyState, setCopyState] = useState<{ id: string; ok: boolean } | null>(null);
  useEffect(() => {
    if (latestFailed) setExpanded(latestId);
    setCopyState(null);
  }, [latestId, latestFailed]);
  if (!records.length) return null;
  const copyFailures = async (record: SourceRefreshHistoryRecord) => {
    try {
      await navigator.clipboard.writeText([
        record.completedAt,
        ...(record.failures ?? []).map((failure) => `${failure.source}\n${failure.type || ""}\n${failure.error}`),
      ].join("\n\n"));
      setCopyState({ id: record.id, ok: true });
    } catch {
      setCopyState({ id: record.id, ok: false });
    }
  };
  return <Localized><section className="panel source-refresh-history">
    <div className="panel-heading"><h3>最近全部更新记录</h3><span className="badge neutral">{translate(`保留 ${records.length} 次`, `${records.length} runs saved`)}</span></div>
    <div className="source-refresh-history-list">{records.map((record) => <details className="source-refresh-record" key={record.id} open={expanded === record.id}>
      <summary className="source-refresh-history-row" onClick={(event) => { event.preventDefault(); setExpanded(expanded === record.id ? null : record.id); }}>
        <History size={14} aria-hidden="true" /><time dateTime={record.completedAt}>{formatDate(record.completedAt)}</time>
        <div className="source-refresh-counts">
          <span>{translate(`${record.updated} 更新`, `${record.updated} updated`)}</span>
          <span>{translate(`${record.unchanged} 无变化`, `${record.unchanged} unchanged`)}</span>
          <span>{translate(`${record.local} 本地保留`, `${record.local} local`)}</span>
          <span className={record.failed ? "failed" : "success"}>{translate(`${record.failed} 失败`, `${record.failed} failed`)}</span>
        </div>
        <span className="source-history-toggle">{expanded === record.id ? "收起详情" : "查看详情"}<ChevronDown size={14} aria-hidden="true" /></span>
      </summary>
      <div className="source-refresh-record-details">
        {record.failed === 0 ? <p className="source-history-empty"><Check size={15} />此次更新没有失败来源。</p> : <>
          {(!record.failures?.length || record.failures.length < record.failed) && <p className="source-history-legacy">旧记录未保存完整的失败详情，无法还原当时的原因。重新执行“全部更新”后可查看新的失败明细。</p>}
          {!!record.failures?.length && <>
            <div className="source-failures-toolbar"><strong>{translate(`失败来源 · ${record.failures.length}`, `Failed sources · ${record.failures.length}`)}</strong>
              <button className="button ghost compact" onClick={() => void copyFailures(record)}><Copy size={14} />复制失败详情</button>
            </div>
            {copyState?.id === record.id && <p role="status">{copyState.ok ? "已复制脱敏详情" : "复制失败，请从下方选中错误详情复制。"}</p>}
            <ul className="source-failure-list">{record.failures.map((failure, index) => <FailureItem key={`${failure.source_id}-${index}`} failure={failure} source={sources.find((source) => source.id === failure.source_id)} busy={busy} onRetry={onRetry} />)}</ul>
          </>}
        </>}
      </div>
    </details>)}</div>
    <p>仅在本机保留最近 10 次更新记录及失败详情；常见凭据已脱敏，长日志会截断。历史结果不代表当前仓库状态。</p>
  </section></Localized>;
}
