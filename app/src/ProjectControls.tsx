import { FormEvent, useState } from "react";
import {
  AlertTriangle,
  Check,
  Download,
  FolderOpen,
  Link2,
  LoaderCircle,
  PackageCheck,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Unlink,
  Upload,
  X,
} from "lucide-react";
import {
  activationStateLabel,
  isElevatedRisk,
  profileActionLabel,
} from "./domain";
import { Localized, translate } from "./i18n";
import type {
  ActivationCell,
  ActivationMatrix,
  ActivationRow,
  ActivationTarget,
  AgentTarget,
  CustomAgentTargetInput,
  ProjectStatus,
  SkillProfilePreview,
  SkillProfileImportPreview,
  SkillProfileImportStatus,
  SkillProfileSummary,
} from "./types";

const EMPTY_AGENT_TARGET: CustomAgentTargetInput = {
  id: "",
  name: "",
  globalPath: "",
  detectPath: "",
  projectPath: "",
};

export function AgentTargetRegistryPanel({
  targets,
  busy,
  onAdd,
  onRemove,
  onChooseDirectory,
}: {
  targets: AgentTarget[];
  busy: string | null;
  onAdd: (target: CustomAgentTargetInput) => Promise<boolean>;
  onRemove: (target: AgentTarget) => void;
  onChooseDirectory: (title: string) => Promise<string | null>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CustomAgentTargetInput>(EMPTY_AGENT_TARGET);
  const customTargets = targets.filter((target) => !target.built_in);
  const setField = <K extends keyof CustomAgentTargetInput,>(
    field: K,
    value: CustomAgentTargetInput[K],
  ) => setDraft((current) => ({ ...current, [field]: value }));
  const chooseDirectory = async (
    field: "globalPath" | "detectPath",
    title: string,
  ) => {
    const selected = await onChooseDirectory(title);
    if (selected) setField(field, selected);
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (await onAdd(draft)) {
      setDraft(EMPTY_AGENT_TARGET);
      setEditing(false);
    }
  };
  const complete = Object.values(draft).every((value) => value.trim());

  return (
    <Localized><section className="panel agent-target-registry-panel">
      <div className="panel-heading agent-target-registry-heading">
        <div>
          <h3>Agent 目标目录</h3>
          <p>内置目标保持只读；自定义目标保存在当前仓库的 SQLite 中，并参与系统项目、推荐和全局安装矩阵。</p>
        </div>
        <div className="agent-target-heading-actions">
          <span className="badge neutral">{targets.length - customTargets.length} 个内置 · {customTargets.length} 个自定义</span>
          <button
            className="button secondary compact"
            type="button"
            disabled={Boolean(busy)}
            onClick={() => setEditing((value) => !value)}
          >
            {editing ? <X size={14} /> : <Plus size={14} />}
            {editing ? "取消" : "添加自定义 Agent"}
          </button>
        </div>
      </div>

      {editing && (
        <form className="agent-target-form" onSubmit={submit}>
          <label className="input-field">
            <span>目标 ID</span>
            <input
              required
              maxLength={32}
              pattern="[a-z][a-z0-9-]{0,31}"
              aria-describedby="agent-target-id-hint"
              autoComplete="off"
              spellCheck={false}
              value={draft.id}
              onChange={(event) => setField("id", event.target.value)}
              placeholder="例如：nova"
            />
            <small id="agent-target-id-hint">小写字母开头，只能使用小写字母、数字和连字符。</small>
          </label>
          <label className="input-field">
            <span>显示名称</span>
            <input required maxLength={80} value={draft.name} onChange={(event) => setField("name", event.target.value)} placeholder="例如：Nova Agent" />
          </label>
          <label className="input-field wide-field">
            <span>全局 Skills 目录</span>
            <div className="input-with-button">
              <input required spellCheck={false} value={draft.globalPath} onChange={(event) => setField("globalPath", event.target.value)} placeholder="/Users/name/.nova/skills" />
              <button type="button" aria-label="选择全局 Skills 目录" onClick={() => void chooseDirectory("globalPath", translate("选择全局 Skills 目录", "Choose the global Skills directory"))}><FolderOpen size={16} /></button>
            </div>
          </label>
          <label className="input-field wide-field">
            <span>Agent 检测目录</span>
            <div className="input-with-button">
              <input required spellCheck={false} value={draft.detectPath} onChange={(event) => setField("detectPath", event.target.value)} placeholder="/Users/name/.nova" />
              <button type="button" aria-label="选择 Agent 检测目录" onClick={() => void chooseDirectory("detectPath", translate("选择 Agent 检测目录", "Choose the agent detection directory"))}><FolderOpen size={16} /></button>
            </div>
            <small>检测目录存在时，即使 Skills 子目录尚未创建，也会显示为待初始化。</small>
          </label>
          <label className="input-field">
            <span>项目内 Skills 路径</span>
            <input required spellCheck={false} value={draft.projectPath} onChange={(event) => setField("projectPath", event.target.value)} placeholder=".nova/skills" />
          </label>
          <div className="agent-target-form-actions">
            <small>目录必须位于当前用户主目录内；保存配置不会创建、移动或删除任何目录。</small>
            <button className="button primary compact" disabled={!complete || Boolean(busy)}>
              {busy === "agent-target-add" ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}
              保存目标
            </button>
          </div>
        </form>
      )}

      {customTargets.length > 0 && (
        <div className="custom-agent-target-list">
          {customTargets.map((target) => (
            <div className="custom-agent-target-row" key={target.id}>
              <div className={`custom-agent-target-state ${target.detected ? "detected" : "missing"}`}><Link2 size={15} /></div>
              <div className="custom-agent-target-copy">
                <div><strong>{target.label}</strong><span className="badge neutral">{target.id}</span></div>
                <span title={target.global_path}>{target.global_path}</span>
                <small>{target.project_path} · {target.detected ? "已检测" : "未检测"}</small>
              </div>
              <button className="text-button danger" type="button" disabled={Boolean(busy)} onClick={() => onRemove(target)}><Trash2 size={13} />移除配置</button>
            </div>
          ))}
        </div>
      )}
    </section></Localized>
  );
}

function profileImportStatusLabel(status: SkillProfileImportStatus): string {
  return {
    exact: "精确匹配",
    compatible: "可适配",
    ambiguous: "待确认",
    missing: "暂未发现",
  }[status];
}

export function ProjectActivationMatrix({
  matrix,
  query,
  busy,
  allowRisk,
  onQuery,
  onSearch,
  onAllowRisk,
  onInstall,
  onUninstall,
  onAdopt,
  onOpenTarget,
}: {
  matrix: ActivationMatrix | null;
  query: string;
  busy: string | null;
  allowRisk: boolean;
  onQuery: (value: string) => void;
  onSearch: () => void;
  onAllowRisk: (value: boolean) => void;
  onInstall: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onUninstall: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onAdopt: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onOpenTarget: (target: ActivationTarget) => void;
}) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSearch();
  };
  return (
    <Localized><section className="panel activation-matrix-panel">
      <div className="panel-heading activation-heading">
        <div>
          <h3>Agent 全局安装</h3>
          <p>每一列对应初始化发现的全局目录；安装和卸载仍由 Manifest 管理。</p>
        </div>
        <span className="badge neutral">{matrix?.total ?? 0} 个唯一 Skill</span>
      </div>
      <form className="activation-toolbar" onSubmit={submit}>
        <label className="activation-search">
          <Search size={15} />
          <span className="sr-only">搜索目录中的 Skill</span>
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="按名称、能力或来源搜索"
          />
        </label>
        <button className="button secondary compact" disabled={Boolean(busy)}>
          {busy === "matrix-load" ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}
          查找
        </button>
        <label className="matrix-risk-toggle">
          <input
            type="checkbox"
            checked={allowRisk}
            onChange={(event) => onAllowRisk(event.target.checked)}
          />
          <ShieldAlert size={14} />
          <span>允许高风险操作</span>
        </label>
      </form>
      {!matrix ? (
        <div className="history-empty">
          <LoaderCircle className="spin" size={18} />
          <span>正在读取 Agent 安装状态…</span>
        </div>
      ) : matrix.rows.length ? (
        <div className="activation-table-scroll">
          <table
            className="activation-table"
            style={{ minWidth: `${320 + matrix.targets.length * 150}px` }}
          >
            <thead>
              <tr>
                <th scope="col">Skill</th>
                {matrix.targets.map((target) => (
                  <th scope="col" key={target.id}>
                    <button
                      type="button"
                      className="target-heading"
                      onClick={() => onOpenTarget(target)}
                      disabled={!target.exists && !target.detected}
                    >
                      <strong>{target.label}</strong>
                      <span title={target.path}>{target.status === "pending" ? `${translate("待初始化", "Pending setup")} · ` : ""}{target.path}</span>
                      <i className={"target-dot status-" + target.status} />
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.map((row) => (
                <tr key={row.name}>
                  <th scope="row">
                    <strong>{row.name}</strong>
                    <span>{row.description}</span>
                    {row.variant_count > 1 && <small>{row.variant_count} 个 Agent 适配版本</small>}
                  </th>
                  {matrix.targets.map((target) => {
                    const cell = row.cells.find((item) => item.target_id === target.id);
                    return cell ? (
                      <td key={target.id}>
                        <ActivationCellControl
                          row={row}
                          cell={cell}
                          target={target}
                          busy={busy}
                          allowRisk={allowRisk}
                          onInstall={onInstall}
                          onUninstall={onUninstall}
                          onAdopt={onAdopt}
                          onOpenTarget={onOpenTarget}
                        />
                      </td>
                    ) : null;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="history-empty">
          <Search size={19} />
          <span>当前仓库中没有匹配的 Skill。</span>
        </div>
      )}
      {matrix && matrix.total > matrix.rows.length && (
        <p className="matrix-result-note">
          当前显示前 {matrix.rows.length} 项；继续输入更具体的名称或能力可缩小范围。
        </p>
      )}
    </section></Localized>
  );
}

function ActivationCellControl({
  row,
  cell,
  target,
  busy,
  allowRisk,
  onInstall,
  onUninstall,
  onAdopt,
  onOpenTarget,
}: {
  row: ActivationRow;
  cell: ActivationCell;
  target: ActivationTarget;
  busy: string | null;
  allowRisk: boolean;
  onInstall: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onUninstall: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onAdopt: (row: ActivationRow, cell: ActivationCell, target: ActivationTarget) => void;
  onOpenTarget: (target: ActivationTarget) => void;
}) {
  const risky = isElevatedRisk(cell.audit_severity);
  const disabled = Boolean(busy) || !cell.valid || (risky && !allowRisk);
  const icon = {
    managed: <Check size={13} />,
    drift: <AlertTriangle size={13} />,
    "external-match": <Link2 size={13} />,
    external: <FolderOpen size={13} />,
    absent: <PackageCheck size={13} />,
    unavailable: <FolderOpen size={13} />,
  }[cell.state];
  const title = !cell.valid
    ? "Skill 格式无效，不能安装"
    : risky && !allowRisk
      ? "先开启高风险操作并完成确认"
      : cell.path;
  if (cell.state === "managed") {
    return (
      <Localized><button
        type="button"
        className="activation-control state-managed"
        title={translate(`卸载受管 Skill：${cell.path}`, `Uninstall managed Skill: ${cell.path}`)}
        disabled={Boolean(busy)}
        onClick={() => onUninstall(row, cell, target)}
      >
        {icon}<span>{activationStateLabel(cell.state)}</span><Unlink size={12} />
      </button></Localized>
    );
  }
  if (cell.state === "drift") {
    return (
      <Localized><button
        type="button"
        className="activation-control state-drift"
        title="打开系统项目检查漂移后再处理"
        onClick={() => onOpenTarget(target)}
      >
        {icon}<span>{activationStateLabel(cell.state)}</span>
      </button></Localized>
    );
  }
  if (cell.state === "external-match") {
    return (
      <Localized><button
        type="button"
        className="activation-control state-external"
        title={title}
        disabled={disabled}
        onClick={() => onAdopt(row, cell, target)}
      >
        {icon}<span>{activationStateLabel(cell.state)}</span>
      </button></Localized>
    );
  }
  if (cell.state === "absent") {
    return (
      <Localized><button
        type="button"
        className="activation-control state-absent"
        title={title}
        disabled={disabled}
        onClick={() => onInstall(row, cell, target)}
      >
        {icon}<span>{activationStateLabel(cell.state)}</span>
      </button></Localized>
    );
  }
  return (
    <Localized><span
      className={"activation-control passive state-" + cell.state}
      title={cell.path}
    >
      {icon}<span>{activationStateLabel(cell.state)}</span>
    </span></Localized>
  );
}

export function ProjectProfilesPanel({
  profiles,
  selectedProfileId,
  preview,
  importPreview,
  transferMessage,
  status,
  captureName,
  busy,
  onSelect,
  onPreview,
  onApply,
  onDelete,
  onExport,
  onImportChoose,
  onImportConfirm,
  onImportDismiss,
  onCaptureName,
  onCapture,
}: {
  profiles: SkillProfileSummary[];
  selectedProfileId: string;
  preview: SkillProfilePreview | null;
  importPreview: SkillProfileImportPreview | null;
  transferMessage: string;
  status: ProjectStatus;
  captureName: string;
  busy: string | null;
  onSelect: (profileId: string) => void;
  onPreview: () => void;
  onApply: () => void;
  onDelete: () => void;
  onExport: () => void;
  onImportChoose: () => void;
  onImportConfirm: () => void;
  onImportDismiss: () => void;
  onCaptureName: (value: string) => void;
  onCapture: () => void;
}) {
  return (
    <Localized><section className="panel project-profile-panel">
      <div className="panel-heading">
        <div>
          <h3>Skill 配置集</h3>
          <p>保存可复用的 Skill 组合；应用前先解析目标 Agent 版本和路径冲突。</p>
        </div>
        <span className="badge neutral">{profiles.length} 个配置集</span>
      </div>
      <div className="profile-controls">
        <label>
          <span>选择配置集</span>
          <select
            value={selectedProfileId}
            onChange={(event) => onSelect(event.target.value)}
          >
            <option value="">请选择</option>
            {profiles.map((profile) => (
              <option value={profile.id} key={profile.id}>
                {profile.name} · {profile.entry_count} Skills
              </option>
            ))}
          </select>
        </label>
        <button
          className="button secondary"
          disabled={!selectedProfileId || Boolean(busy)}
          onClick={onPreview}
        >
          {busy === "profile-preview" ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
          预览应用
        </button>
        <button
          className="button ghost"
          disabled={!selectedProfileId || Boolean(busy)}
          onClick={onDelete}
          title="只删除配置集，不卸载任何 Skill"
        >
          <Trash2 size={14} />删除配置集
        </button>
      </div>
      <div className="profile-transfer">
        <div className="profile-transfer-copy">
          <strong>迁移配置集</strong>
          <span>JSON 只保存来源定位信息，不复制、下载或安装 Skill；应用到项目仍需单独预览。</span>
        </div>
        <div className="profile-transfer-actions">
          <button
            type="button"
            className="button ghost"
            disabled={Boolean(busy)}
            onClick={onImportChoose}
          >
            {busy === "profile-import-preview" ? <LoaderCircle className="spin" size={14} /> : <Upload size={14} />}
            导入 JSON
          </button>
          <button
            type="button"
            className="button ghost"
            disabled={!selectedProfileId || Boolean(busy)}
            onClick={onExport}
          >
            {busy === "profile-export" ? <LoaderCircle className="spin" size={14} /> : <Download size={14} />}
            导出所选
          </button>
        </div>
      </div>
      {transferMessage && (
        <p className="profile-transfer-message" role="status">{transferMessage}</p>
      )}
      {importPreview && (
        <div className="profile-import-preview">
          <div className="profile-import-heading">
            <div>
              <strong>{importPreview.profile.name}</strong>
              <span title={importPreview.path}>{importPreview.path}</span>
            </div>
            <button
              type="button"
              className="icon-button"
              aria-label="关闭导入预览"
              title="关闭导入预览"
              disabled={Boolean(busy)}
              onClick={onImportDismiss}
            >
              <X size={14} />
            </button>
          </div>
          <div className="profile-preview-summary">
            <span>{importPreview.profile.entry_count} 个 Skills</span>
            <span>{importPreview.counts.exact} 精确匹配</span>
            <span>{importPreview.counts.compatible} 可适配</span>
            <span className={importPreview.counts.ambiguous ? "warning" : ""}>
              {importPreview.counts.ambiguous} 待确认
            </span>
            <span className={importPreview.counts.missing ? "warning" : ""}>
              {importPreview.counts.missing} 暂未发现
            </span>
          </div>
          <div className="profile-preview-list">
            {importPreview.items.map((item, index) => (
              <div
                className={"profile-preview-row import-" + item.status}
                key={item.skill_name + "-" + index}
              >
                {item.status === "exact" || item.status === "compatible"
                  ? <ShieldCheck size={14} />
                  : <AlertTriangle size={14} />}
                <div>
                  <strong>{item.skill_name}</strong>
                  <span>{item.reason}</span>
                </div>
                <i>{profileImportStatusLabel(item.status)}</i>
              </div>
            ))}
          </div>
          <p className="profile-import-note">
            {importPreview.action === "already-exists"
              ? "完全相同的配置集已经存在，本次不会创建重复记录。"
              : importPreview.counts.missing || importPreview.counts.ambiguous
                ? "可以先保存配置集；缺失或待确认的 Skill 在应用时仍会阻止落地。"
                : "导入只新增一条配置集记录，不会修改任何 Agent 或项目目录。"}
          </p>
          <button
            type="button"
            className={`button wide ${importPreview.can_import ? "primary" : "secondary"}`}
            disabled={!importPreview.can_import || Boolean(busy)}
            onClick={onImportConfirm}
          >
            {busy === "profile-import" ? <LoaderCircle className="spin" size={14} /> : <Upload size={14} />}
            {importPreview.can_import ? "确认导入配置集" : "相同配置集已存在"}
          </button>
        </div>
      )}
      {preview && (
        <div className="profile-preview">
          <div className="profile-preview-summary">
            <strong>{preview.profile.name}</strong>
            <span>{preview.counts.install} 将安装</span>
            <span>{preview.counts["already-installed"]} 已存在</span>
            <span className={preview.counts.conflict || preview.counts.unresolved ? "warning" : ""}>
              {preview.counts.conflict + preview.counts.unresolved} 需处理
            </span>
          </div>
          <div className="profile-preview-list">
            {preview.items.map((item, index) => (
              <div
                className={"profile-preview-row action-" + item.action}
                key={item.skill_name + "-" + index}
              >
                {item.action === "install" || item.action === "already-installed"
                  ? <ShieldCheck size={14} />
                  : <AlertTriangle size={14} />}
                <div>
                  <strong>{item.resolved_name}</strong>
                  <span>{item.reason + (item.path ? " · " + item.path : "")}</span>
                </div>
                <i>{profileActionLabel(item.action)}</i>
              </div>
            ))}
          </div>
          <button
            className={`button wide ${preview.can_apply ? "primary" : "secondary"}`}
            disabled={!preview.can_apply || Boolean(busy)}
            onClick={onApply}
          >
            {busy === "profile-apply" ? <LoaderCircle className="spin" size={15} /> : <PackageCheck size={15} />}
            {preview.can_apply
              ? translate(`应用配置集 · ${preview.counts.install} 项变更`, `Apply profile · ${preview.counts.install} changes`)
              : "请先解决冲突或未解析项"}
          </button>
        </div>
      )}
      <div className="profile-capture">
        <div>
          <strong>将当前受管 Skill 保存为配置集</strong>
          <span>只保存来源定位信息，不复制 Skill，也不记录机器绝对路径。</span>
        </div>
        <label>
          <span className="sr-only">新配置集名称</span>
          <input
            value={captureName}
            onChange={(event) => onCaptureName(event.target.value)}
            placeholder="例如：前端项目基础能力"
          />
        </label>
        <button
          className="button secondary"
          disabled={!status.entries.length || !captureName.trim() || Boolean(busy)}
          onClick={onCapture}
        >
          {busy === "profile-capture" ? <LoaderCircle className="spin" size={14} /> : <PackageCheck size={14} />}
          保存当前组合
        </button>
      </div>
    </section></Localized>
  );
}
