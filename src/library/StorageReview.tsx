import { useEffect, useRef, useState } from "react";
import "../styles/storage-review.css";

type Scores = { G: number; P: number; F: number; M: number; S: number };
type ReviewItem = {
  publicationId: string; templateKind?: "part" | "component"; name: string; code: string; version: number;
  size: number; candidateClass: string; status: string; scores: Scores | null; similarityReason?: string;
  matches: { publicationId: string; candidateClass: string; scores: Scores; differences?: Record<string, { leftOnly: string[]; rightOnly: string[] }> }[];
  references: { kind: string; id: string; name: string; clientId?: string }[];
};
type Report = {
  scanId: string; checkedAt: string; scanComplete: boolean; deletionEnabled: boolean;
  sharedReferences: { clientCount: number; staleClientCount: number };
  summary: { totalCount: number; totalBytes: number; strictCandidateCount: number; strictCandidateBytes: number; highSimilarityCount: number; blockedCount: number };
  items: ReviewItem[]; issues: { code: string; sourceId?: string }[];
};
type Backup = { backupId: string; publicationId: string; createdAt: string; expiresAt: string };
type Outcome = { publicationId: string; status: string; errorCode?: string; warningCode?: string; recoveryStatus?: string; recoveryCode?: string; backupId?: string };
const categories: Record<string, string> = {
  "old-release": "旧版本", "exact-duplicate": "完全重复", "high-similarity": "高度相似",
  "suspected-similarity": "疑似相似", unclassified: "无法分类", none: "保留", "component-release": "组件发布版本",
};
const statuses: Record<string, string> = { "auto-clean": "自动清理候选", "awaiting-confirmation": "等待逐项确认", notice: "仅提示", blocked: "扫描不完整，暂停清理", retained: "保留", unclassified: "定义不足或格式不支持" };
const outcomes: Record<string, string> = { deleted: "已删除", failed: "删除失败", partial: "部分删除，需处理恢复", "backup-failed": "备份失败，未删除", rejected: "未删除，请重新检查" };
const errorNames: Record<string, string> = {
  DELETE_PERMISSION_DENIED: "MinIO 账户没有删除权限", BACKUP_FAILED: "本地备份未完成",
  CLEANUP_SCAN_NOT_FOUND: "扫描已过期，请重新检查", PUBLICATION_CHANGED: "发布包已变化，请重新检查",
  CLEANUP_KEEP_ONE_REQUIRED: "同一相似组至少需要保留一个包", BACKUP_TARGET_EXISTS: "原位置已有对象，无法覆盖恢复",
  CLEANUP_SCAN_INCOMPLETE: "扫描不完整，请先解决读取问题", BACKUP_NOT_EXPIRED: "备份未到清理日期",
  CLEANUP_HISTORY_REMAINS: "此模板仍有未清理的旧包，暂时保留最新版",
  CLEANUP_CONDITIONAL_WRITE_FAILED: "无法安全恢复，请检查 MinIO 连接与条件写入支持",
  CLEANUP_RECOVERY_CONFLICT: "原位置已变化，未覆盖，恢复包仍在本机备份中",
  CLEANUP_RECOVERY_UNVERIFIED: "无法验证原位置，恢复包仍在本机备份中",
  CLEANUP_LOCAL_UPDATE_FAILED: "远端已删除，本地更新未全部完成，请重新检查",
};
const size = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export function StorageReview({ onCleaned }: { onCleaned?: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef<AbortController | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("candidates");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<Outcome[]>([]);
  useEffect(() => () => request.current?.abort(), []);

  async function refresh(signal?: AbortSignal) {
    const [response, savedResponse] = await Promise.all([
      fetch("/api/template-library/cleanup-preview", { method: "POST", cache: "no-store", signal }),
      fetch("/api/template-library/backups", { cache: "no-store", signal }),
    ]);
    if (!response.ok) throw new Error("暂时无法检查发布库，请确认 MinIO 连接后重试。");
    const next: Report = await response.json();
    const saved: Backup[] = savedResponse.ok ? (await savedResponse.json()).items : [];
    if (signal?.aborted) return;
    setReport(next); setBackups(saved); setSelected(new Set());
  }

  async function scan(syncFirst = false) {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setLoading(true); setError("");
    try {
      if (syncFirst) {
        const response = await fetch("/api/template-library/references/sync", { method: "POST", signal: controller.signal });
        if (!response.ok) throw new Error("本机引用同步失败，草稿已保存在本机。");
      }
      await refresh(controller.signal);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error && !(cause instanceof TypeError) ? cause.message : "无法连接组件后端。");
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }

  async function cleanup(ids: string[]) {
    if (!report || !ids.length) return;
    const rows = report.items.filter(item => ids.includes(item.publicationId));
    const refs = rows.flatMap(item => item.references.map(ref => `${ref.name}（${ref.id}）`));
    if (!window.confirm(`确认删除这 ${rows.length} 个发布包？\n${rows.map(item => `${item.name} V${item.version}（${item.publicationId}）`).join("\n")}\n\n${refs.length ? `已扫描到的引用：\n${refs.join("\n")}\n` : "已扫描范围内未发现引用。\n"}固定引用不会迁移，使用这些包的组件可能无法重新加载。删除前会保存本地恢复备份。`)) return;
    setLoading(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/template-library/cleanup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scanId: report.scanId, publicationIds: ids }) });
      const data = await response.json();
      if (!data.items) throw new Error(errorNames[data.error?.code] || "清理失败，请重新检查。");
      setResults(data.items);
      if (data.items.some((item: Outcome) => item.status === "deleted")) onCleaned?.();
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法完成清理。"); }
    finally { setLoading(false); }
  }

  async function backupAction(backup: Backup, action: "restore" | "delete") {
    if (!window.confirm(action === "restore" ? `将 ${backup.publicationId} 恢复到原发布库位置？之后再次同步时，仍会按清理规则检查。` : `永久删除 ${backup.publicationId} 的这份本地备份？`)) return;
    setLoading(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/template-library/backups/${backup.backupId}${action === "restore" ? "/restore" : ""}`, { method: action === "restore" ? "POST" : "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(errorNames[data.error?.code] || "备份操作失败，请稍后重试。");
      setNotice(action === "restore" ? "备份已恢复。左侧发布库将在重新检查后更新。" : "本地备份已删除。");
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "操作失败。"); }
    finally { setLoading(false); }
  }

  function exportReport() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify({ ...report, cleanupResults: results }, null, 2)], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = "模板包存储检查.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  const items = report?.items.filter(item => filter === "all" || (filter === "candidates" ? ["auto-clean", "awaiting-confirmation", "notice"].includes(item.status) : item.status === filter)) || [];
  const strict = report?.items.filter(item => item.status === "auto-clean").map(item => item.publicationId) || [];
  return <>
    <button type="button" className="ghost storage-review-open" onClick={() => { dialog.current?.showModal(); void scan(); }}>存储检查</button>
    <dialog className="storage-review" ref={dialog} aria-labelledby="storage-review-title" onCancel={event => { if (loading) event.preventDefault(); }} onClose={() => request.current?.abort()}>
      <header><h2 id="storage-review-title">模板包存储检查</h2><button type="button" className="ghost" disabled={loading} onClick={() => dialog.current?.close()}>关闭</button></header>
      <p className="storage-review-note">同步发布库时自动清理旧版本和完全重复包。高度相似包需逐项确认；疑似相似只作提示。清理前保存本地备份，固定引用不会迁移。</p>
      <div className="action-row">
        <button type="button" className="primary" onClick={() => void scan()} disabled={loading}>{loading ? "正在处理…" : "重新检查"}</button>
        <button type="button" className="ghost" onClick={exportReport} disabled={!report || loading}>导出检查报告</button>
        <a href="/api/template-library/cleanup-audit" download="模板包清理记录.json">导出清理记录</a>
        <button type="button" className="ghost" onClick={() => void scan(true)} disabled={loading}>同步本机引用</button>
      </div>
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      {results.length > 0 && <ul aria-label="清理结果">{results.map(result => <li key={result.publicationId}>{result.publicationId}：{outcomes[result.status] || result.status}{[result.errorCode, result.warningCode, result.recoveryCode].filter(Boolean).map(code => ` · ${errorNames[code!] || code}`).join("")}{result.recoveryStatus === "restored-for-retry" ? " · 当前包已恢复，可重试清理" : ""}</li>)}</ul>}
      {report && <>
        <p className="hint">检查时间：{date(report.checkedAt)} · {report.scanComplete ? "扫描完成" : "扫描不完整，清理已暂停"} · 已登记 {report.sharedReferences.clientCount} 个客户端（{report.sharedReferences.staleClientCount} 个超过一天未同步）。引用统计不涵盖未登记设备。</p>
        <div className="storage-review-metrics">
          <div><small>发布包容量</small><b>{size(report.summary.totalBytes)}</b><span>{report.summary.totalCount} 个包</span></div>
          <div><small>严格清理候选</small><b>{size(report.summary.strictCandidateBytes)}</b><span>{report.summary.strictCandidateCount} 个旧版本或完全重复包</span></div>
          <div><small>高度相似 / 暂缓清理</small><b>{report.summary.highSimilarityCount} / {report.summary.blockedCount}</b><span>预览操作不会删除对象</span></div>
        </div>
        {report.issues.length > 0 && <details><summary>{report.issues.length} 项扫描问题</summary><ul>{report.issues.map((issue, index) => <li key={index}>{issue.code} {issue.sourceId || ""}</li>)}</ul></details>}
        <div className="action-row">
          <button type="button" className="ghost" disabled={loading || !report.deletionEnabled || !strict.length} onClick={() => void cleanup(strict)}>清理 / 重试严格候选（{strict.length}）</button>
          <button type="button" className="primary" disabled={loading || !report.deletionEnabled || !selected.size} onClick={() => void cleanup([...selected])}>删除已选相似包（{selected.size}）</button>
        </div>
        <label className="storage-review-filter">显示范围<select value={filter} onChange={event => setFilter(event.target.value)}><option value="candidates">重复与旧版候选</option><option value="retained">保留</option><option value="unclassified">无法分类</option><option value="blocked">扫描不完整</option><option value="all">全部</option></select></label>
        <div className="storage-review-table"><table><thead><tr><th>模板 / 版本</th><th>大小</th><th>检查结果与引用影响</th></tr></thead><tbody>{items.map(item => <tr key={`${item.templateKind || "part"}:${item.publicationId}`}>
          <td><b>{item.name} · V{item.version}</b><small>{item.code} · {item.templateKind === "component" ? "组件" : "零件"}</small><small>{item.publicationId}</small>
            {item.status === "awaiting-confirmation" && <label><input type="checkbox" disabled={loading} checked={selected.has(item.publicationId)} onChange={event => setSelected(previous => { const next = new Set(previous); if (event.target.checked) next.add(item.publicationId); else next.delete(item.publicationId); return next; })}/>选择删除此包</label>}
          </td><td>{size(item.size)}</td><td><b>{categories[item.candidateClass] || item.candidateClass}</b><small>{statuses[item.status] || item.status}</small>
            {item.scores && <small>总分 {item.scores.S}% · 几何 {item.scores.G}% · 参数 {item.scores.P}% · 特征接口 {item.scores.F}% · 材料制造 {item.scores.M}%</small>}
            {item.similarityReason && <small>{item.similarityReason}</small>}
            {item.matches.map(match => <details key={match.publicationId}><summary>{categories[match.candidateClass]}：{match.publicationId}（{match.scores.S}%）</summary>{match.differences && Object.entries(match.differences).map(([group, diff]) => <div key={group}><small>{group} · 各显示前 20 项差异</small><pre>{[...diff.leftOnly.map(field => `当前 ${field}`), ...diff.rightOnly.map(field => `对比 ${field}`)].join("\n") || "相同"}</pre></div>)}</details>)}
            {item.references.map(ref => <small key={`${ref.clientId || "local"}:${ref.kind}:${ref.id}`}>引用：{ref.name}（{ref.id}）{ref.clientId ? ` · ${ref.clientId}` : ""}</small>)}
          </td></tr>)}</tbody></table></div>
        {!items.length && <p className="empty-text">此范围暂无记录。</p>}
      </>}
      <details className="storage-review-backups"><summary>本地恢复备份（{backups.length}）</summary><p className="hint">备份保存在执行清理的这台电脑。30 天后可手工删除，不会自动过期删除。</p>
        {backups.map(backup => <div key={backup.backupId}><b>{backup.publicationId}</b><small>备份于 {date(backup.createdAt)} · 可清理日期 {date(backup.expiresAt)}</small><div className="action-row"><a href={`/api/template-library/backups/${backup.backupId}/download`}>下载包</a><button type="button" className="ghost" disabled={loading} onClick={() => void backupAction(backup, "restore")}>恢复到发布库</button><button type="button" className="ghost" disabled={loading || new Date(backup.expiresAt).getTime() > Date.now()} onClick={() => void backupAction(backup, "delete")}>删除过期备份</button></div></div>)}
      </details>
    </dialog>
  </>;
}
