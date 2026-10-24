import { Fragment, useEffect, useRef, useState } from "react";
import { AlertTriangle, Boxes, Plus, RefreshCw, Search } from "lucide-react";
import { fetchReusableTemplates, publicationKey } from "./publishedLibrary";
import type { PublishedTemplate } from "./publishedLibrary";

const formatDate = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const formatSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;

type Props = {
  onPreview: (template: PublishedTemplate) => void;
  onAdd: (template: PublishedTemplate) => void;
  memberPublicationIds: string[];
  selectionResetToken: number;
  refreshToken: number;
  addDisabled: boolean;
};

export function PublishedTemplateLibrary({ onPreview, onAdd, memberPublicationIds, selectionResetToken, refreshToken, addDisabled }: Props) {
  const [items, setItems] = useState<PublishedTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);

  async function refresh() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError("");
    try {
      const result = await fetchReusableTemplates(controller.signal, available => {
        if (!controller.signal.aborted) setItems(available);
      });
      if (controller.signal.aborted) return;
      setItems(result);
      setUpdatedAt(new Date().toISOString());
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof TypeError ? "无法连接组件平台服务，请检查服务是否运行后重试。" : cause instanceof Error ? cause.message : "读取发布库失败，请重试。");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  useEffect(() => { void refresh(); return () => request.current?.abort(); }, [refreshToken]);
  useEffect(() => setSelectedId(null), [selectionResetToken]);

  const term = query.trim().toLocaleLowerCase();
  const visible = items.filter(item => `${item.name} ${item.code}`.toLocaleLowerCase().includes(term));
  const selected = items.find(item => publicationKey(item) === selectedId);
  const alreadyAdded = (item: PublishedTemplate) => memberPublicationIds.includes(item.publicationId);

  return <section className="published-library" aria-label="已发布模板库">
    <div className="panel-title"><span><Boxes size={16}/>已发布模板</span><div className="library-actions"><small>{updatedAt || items.length ? items.length : "—"}</small><button className="library-refresh" onClick={() => void refresh()} disabled={loading} aria-label={error ? "重试读取发布库" : "刷新发布库"} title="刷新发布库"><RefreshCw size={14} className={loading ? "spinning" : ""}/></button></div></div>
    <p className="hint">零部件与组件 · 点击查看版本，或加入当前组件</p>
    <label className="library-search"><Search size={14}/><input aria-label="搜索模板名称或编码" placeholder="搜索名称或编码" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="library-status" role="status" aria-live="polite">{loading ? "正在读取发布库…" : updatedAt ? `上次读取：${formatDate(updatedAt)}` : "尚未连接发布库"}</div>
    {error && <div className="library-error" role="alert"><AlertTriangle size={15}/><div><b>发布库暂未完整加载</b><p>{error}</p><button className="ghost" onClick={() => void refresh()} disabled={loading}>重试</button></div></div>}
    {!loading && !error && updatedAt && items.length === 0 && <p className="library-empty">暂无可用模板。</p>}
    {!loading && items.length > 0 && visible.length === 0 && <p className="library-empty">没有匹配的模板，请调整搜索内容。</p>}
    <div aria-busy={loading}>{visible.map(item => <Fragment key={publicationKey(item)}>
      <div className="publication-card-row">
        <button className={`part-card publication-card ${selectedId === publicationKey(item) ? "selected" : ""}`} onClick={() => { setSelectedId(current => current === publicationKey(item) ? null : publicationKey(item)); onPreview(item); }} aria-pressed={selectedId === publicationKey(item)}>
          <i/><span><b>{item.name}</b><small>{item.templateKind === "component" ? "组件" : "零部件"} · {item.code} · V{item.version}</small><small>{formatDate(item.createdAt)}</small></span>
        </button>
        {item.templateKind !== "component" && <button className="publication-add" type="button" disabled={addDisabled || alreadyAdded(item)} onClick={() => onAdd(item)} aria-label={alreadyAdded(item) ? `已加入 ${item.name}` : `加入 ${item.name}`} title={alreadyAdded(item) ? "已加入组件" : "加入组件"}><Plus size={16}/></button>}
      </div>
      {selected && publicationKey(selected) === publicationKey(item) && <section className="publication-details" aria-label="所选模板发布详情"><h3>发布详情</h3><dl>
        <dt>模板名称</dt><dd>{selected.name}</dd>
        <dt>编码 / 版本</dt><dd>{selected.code} / V{selected.version}</dd>
        <dt>发布时间</dt><dd>{formatDate(selected.createdAt)}</dd>
        <dt>发布记录 ID</dt><dd>{selected.publicationId}</dd>
        <dt>模板 ID</dt><dd>{selected.templateId}</dd>
        <dt>模板包大小</dt><dd>{formatSize(selected.size)}</dd>
        <dt>SHA-256</dt><dd className="publication-hash">{selected.sha256}</dd>
      </dl>
      {selected.templateKind === "component" && selected.engineeringStatus === "incomplete" && <p className="hint">结构版本已发布，工程检查尚未全部通过。</p>}
      {selected.templateKind === "component" && <button className="primary full" disabled={addDisabled || alreadyAdded(selected)} onClick={() => onAdd(selected)}>{alreadyAdded(selected) ? "已加入当前组件" : "加入当前组件"}</button>}
      {selected.templateKind === "component" && <a className="ghost publication-download" href={`/api/published-components/${encodeURIComponent(selected.publicationId)}/download`} download>下载组件包</a>}
      <p className="hint">加入时固定此发布版本；后续发布不会自动替换已有成员。</p></section>}
    </Fragment>)}</div>
    {items.length === 1000 && <p className="hint">当前接口最多返回最近 1000 条发布记录。</p>}
  </section>;
}
