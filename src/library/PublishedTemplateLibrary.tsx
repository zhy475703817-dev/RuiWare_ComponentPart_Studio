import { Fragment, useEffect, useRef, useState } from "react";
import { AlertTriangle, Box, Boxes, Layers, Plus, RefreshCw, Search } from "lucide-react";
import { formatLibraryError, readCachedLibrary, publicationKey } from "./publishedLibrary";
import type { PublishedTemplate } from "./publishedLibrary";

const formatDate = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const formatSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;
const libraryGroups = [
  { kind: "part", source: "零部件平台", title: "已发布零部件", description: "基础零件，用于搭建组件", Icon: Box },
  { kind: "component", source: "组件平台", title: "可复用组件", description: "已发布组件，可再次加入装配", Icon: Layers },
] as const;

export function PublishedTemplateLibrary({ onPreview, onAdd, onDragTemplate, memberPublicationIds = [], selectionResetToken, refreshToken, addDisabled }: {
  onPreview: (template: PublishedTemplate) => void; onAdd: (template: PublishedTemplate) => void; onDragTemplate?: (template: PublishedTemplate | null) => void;
  memberPublicationIds?: string[];
  selectionResetToken: number; refreshToken: number; addDisabled: boolean;
}) {
  const [items, setItems] = useState<PublishedTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [query, setQuery] = useState("");
  const [updateCount, setUpdateCount] = useState(0);
  const [initialized, setInitialized] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const applyAfterCheck = useRef(false);

  async function refresh(action?: "sync" | "apply") {
    if (timer.current) clearTimeout(timer.current);
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    if (action === "sync") applyAfterCheck.current = true;
    let syncing = false;
    try {
      let result = await readCachedLibrary(controller.signal, action);
      if (!result.syncing && applyAfterCheck.current) {
        if (result.updateCount) result = await readCachedLibrary(controller.signal, "apply");
        applyAfterCheck.current = false;
      }
      if (controller.signal.aborted) return;
      setItems(result.items);
      setUpdateCount(result.updateCount);
      setInitialized(result.initialized);
      setError(result.error);
      setUpdatedAt(result.checkedAt === null ? "" : new Date(result.checkedAt * 1000).toISOString());
      syncing = result.syncing;
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof TypeError ? "无法读取共享发布库，请检查组件平台服务是否运行后重试。" : cause instanceof Error ? cause.message : "读取共享发布库失败，请重试。");
    } finally {
      if (!controller.signal.aborted) {
        setLoading(syncing);
        timer.current = setTimeout(() => void refresh(), syncing ? 1000 : 15000);
      }
    }
  }

  useEffect(() => {
    void refresh(refreshToken > 0 ? "sync" : undefined);
    return () => {
      request.current?.abort();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refreshToken]);

  useEffect(() => setSelectedId(null), [selectionResetToken]);

  const term = query.trim().toLocaleLowerCase();
  const visible = items.filter(item => `${item.name} ${item.code}`.toLocaleLowerCase().includes(term));
  const selected = items.find(item => publicationKey(item) === selectedId);

  return <section className="published-library" aria-label="已发布模板库">
    <div className="panel-title"><span><Boxes size={16}/>已发布模板</span><div className="library-actions"><small>{initialized || items.length ? items.length : "—"}</small><button className="library-refresh" onClick={() => void refresh("sync")} disabled={loading} aria-label={error ? "重试读取发布库" : "刷新发布库"} title="刷新发布库"><RefreshCw size={14} className={loading ? "spinning" : ""}/></button></div></div>
    <p className="hint">按发布来源分区 · 点击预览，或加入当前组件</p>
    <label className="library-search"><Search size={14}/><input aria-label="搜索模板名称或编码" placeholder="搜索名称或编码" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="library-status" role="status" aria-live="polite">{loading ? initialized || items.length ? "正在检查模板更新…" : "正在准备模板…" : updatedAt ? `上次检查：${formatDate(updatedAt)}` : "尚未连接发布库"}</div>
    {updateCount > 0 && <div className="library-update" role="status"><span>发现 {updateCount} 项模板更新</span><button className="ghost" onClick={() => void refresh("apply")}>更新列表</button></div>}
    {error && <div className="library-error" role="alert"><AlertTriangle size={15}/><div><b>{items.length ? "暂时无法检查模板更新" : "发布库暂未完整加载"}</b><p>{formatLibraryError(error, items.length > 0)}</p><button className="ghost" onClick={() => void refresh("sync")} disabled={loading}>重试</button></div></div>}
    <div aria-busy={loading}>{libraryGroups.map(group => {
      const groupItems = visible.filter(item => (item.templateKind || "part") === group.kind);
      const total = items.filter(item => (item.templateKind || "part") === group.kind).length;
      return <section key={group.kind} className={`library-source-group library-source-${group.kind}`} aria-label={`${group.source} · ${group.title}`}>
        <header className="library-source-heading">
          <span className="library-source-icon"><group.Icon size={18} aria-hidden="true"/></span>
          <div><small>{group.source}</small><h3>{group.title}</h3></div>
          <span className="library-source-count" aria-label={term ? `匹配 ${groupItems.length} 项，共 ${total} 项` : `共 ${total} 项`}>{term ? `${groupItems.length} / ${total}` : total}</span>
        </header>
        <p className="library-source-description">{group.description}</p>
        {groupItems.length === 0 && <p className="library-source-empty">{term ? "没有匹配的模板，请调整搜索内容。" : loading ? "正在读取此来源的模板…" : error || !initialized ? "暂未读取到此来源的模板。" : `暂无${group.title}。`}</p>}
        {groupItems.map(item => {
      const added = memberPublicationIds.includes(item.publicationId);
      return <Fragment key={publicationKey(item)}><div className="publication-card-row">
      <button className={`part-card publication-card ${selectedId === publicationKey(item) ? "selected" : ""}`} draggable={!addDisabled} onDragStart={event => { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", item.publicationId); onDragTemplate?.(item); }} onDragEnd={() => onDragTemplate?.(null)} onClick={() => { setSelectedId(current => current === publicationKey(item) ? null : publicationKey(item)); onPreview(item); }} aria-pressed={selectedId === publicationKey(item)} title="拖动到三维画布以加入组件">
      <i/><span><b>{item.name}</b><small className="publication-source-badge">{group.source}发布</small><small>{item.code} · V{item.version}</small><small>{formatDate(item.createdAt)}</small></span>
    </button>
    <button className="publication-add" type="button" disabled={added || addDisabled} onClick={() => onAdd(item)} aria-label={added ? `已加入 ${item.name}` : `加入 ${item.name}`} title={added ? "已加入组件" : "加入组件"}><Plus size={16}/></button></div>
    {selected && publicationKey(selected) === publicationKey(item) && <section className="publication-details" aria-label="所选模板发布详情"><h3>发布详情</h3><dl>
      <dt>发布来源</dt><dd>{group.source} · {group.title}</dd>
      <dt>模板名称</dt><dd>{selected.name}</dd>
      <dt>编码 / 版本</dt><dd>{selected.code} / V{selected.version}</dd>
      <dt>发布时间</dt><dd>{formatDate(selected.createdAt)}</dd>
      <dt>发布记录 ID</dt><dd>{selected.publicationId}</dd>
      <dt>模板 ID</dt><dd>{selected.templateId}</dd>
      <dt>模板包大小</dt><dd>{formatSize(selected.size)}</dd>
      <dt>SHA-256</dt><dd className="publication-hash">{selected.sha256}</dd>
    </dl>{selected.templateKind === "component" && selected.engineeringStatus === "incomplete" && <p className="hint">结构版本已发布，工程检查尚未全部通过。</p>}
    <button className="primary full" disabled={addDisabled || added} onClick={() => onAdd(selected)}>{added ? "已加入当前组件" : "加入当前组件"}</button>
    {selected.templateKind === "component" && <a className="ghost publication-download" href={`/api/published-components/${encodeURIComponent(selected.publicationId)}/download`} download>下载组件包</a>}
    <p className="hint">加入时固定此发布版本；后续发布不会自动替换已有成员。</p></section>}
    </Fragment>;
    })}</section>;
    })}</div>
  </section>;
}
