import { Fragment, useEffect, useRef, useState } from "react";
import { AlertTriangle, Box, Boxes, Layers, Plus, RefreshCw, Search, Star } from "lucide-react";
import { classifyPublishedTemplate, formatLibraryError, libraryCategories, parseStoredClassifications, parseStoredPublicationKeys, pendingPublishedTemplates, readCachedLibrary, publicationKey } from "./publishedLibrary";
import type { LibraryCategory, PublishedTemplate } from "./publishedLibrary";

const formatDate = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const formatSize = (size: number) => size < 1024 ? `${size} B` : size < 1024 ** 2 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 ** 2).toFixed(1)} MB`;
const libraryGroups = [
  { kind: "part", source: "零部件平台", title: "已发布零部件", description: "基础零件，用于搭建组件", Icon: Box },
  { kind: "component", source: "组件平台", title: "可复用组件", description: "已发布组件，可再次加入装配", Icon: Layers },
] as const;
const favoritesStorageKey = "ruiware-library-favorites";
const recentStorageKey = "ruiware-library-recent";
const classificationsStorageKey = "ruiware-library-classifications-v1";
type LibraryView = "all" | "favorites" | "recent" | "pending";

function readKeys(key: string): string[] {
  try { return parseStoredPublicationKeys(globalThis.localStorage.getItem(key)); } catch { return []; }
}

function storeKeys(key: string, values: string[]) {
  try { globalThis.localStorage.setItem(key, JSON.stringify(values)); } catch { /* Storage may be disabled. */ }
}

function readClassifications(): Record<string, LibraryCategory> | null {
  try {
    const value = globalThis.localStorage.getItem(classificationsStorageKey);
    return value === null ? null : parseStoredClassifications(value);
  } catch { return null; }
}

function storeClassifications(value: Record<string, LibraryCategory>) {
  try { globalThis.localStorage.setItem(classificationsStorageKey, JSON.stringify(value)); } catch { /* Storage may be disabled. */ }
}

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
  const [view, setView] = useState<LibraryView>("all");
  const [category, setCategory] = useState<LibraryCategory | "all">("all");
  const [characteristic, setCharacteristic] = useState("all");
  const [favorites, setFavorites] = useState(() => readKeys(favoritesStorageKey));
  const [recent, setRecent] = useState(() => readKeys(recentStorageKey));
  const [classifications, setClassifications] = useState<Record<string, LibraryCategory> | null>(readClassifications);
  const [pendingChoices, setPendingChoices] = useState<Record<string, LibraryCategory>>({});
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
  useEffect(() => {
    if (!items.length || classifications !== null) return;
    const initial = Object.fromEntries(items.filter(item => (item.templateKind || "part") === "part").map(item => [publicationKey(item), classifyPublishedTemplate(item).category])) as Record<string, LibraryCategory>;
    setClassifications(initial); storeClassifications(initial);
  }, [items, classifications]);

  const term = query.trim().toLocaleLowerCase();
  const confirmed = classifications || {};
  const pendingItems = classifications === null ? [] : pendingPublishedTemplates(items, confirmed);
  const classificationFor = (item: PublishedTemplate) => {
    const suggested = classifyPublishedTemplate(item);
    const categoryKey = confirmed[publicationKey(item)] ?? suggested.category;
    return { ...suggested, category: categoryKey, categoryLabel: libraryCategories.find(([key]) => key === categoryKey)![1] };
  };
  const traits = [...new Set(items.filter(item => (item.templateKind || "part") === "part").map(item => classifyPublishedTemplate(item).characteristic))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const categoryCounts = Object.fromEntries(libraryCategories.map(([key]) => [key, items.filter(item => confirmed[publicationKey(item)] === key).length]));
  const visible = items.filter(item => {
    const key = publicationKey(item);
    const classification = classificationFor(item);
    const matchesView = view === "all" || view === "favorites" && favorites.includes(key) || view === "recent" && recent.includes(key);
    const confirmedOrComponent = (item.templateKind || "part") === "component" || confirmed[key] !== undefined;
    const matchesCategory = category === "all" || (item.templateKind || "part") === "part" && classification.category === category;
    const matchesCharacteristic = characteristic === "all" || classification.characteristic === characteristic;
    return confirmedOrComponent && matchesView && matchesCategory && matchesCharacteristic && `${item.name} ${item.code} ${classification.categoryLabel} ${classification.characteristic}`.toLocaleLowerCase().includes(term);
  }).sort((a, b) => view === "recent" ? recent.indexOf(publicationKey(a)) - recent.indexOf(publicationKey(b)) : 0);
  const selected = items.find(item => publicationKey(item) === selectedId);
  const filtersActive = Boolean(term) || view !== "all" || category !== "all" || characteristic !== "all";
  const addTemplate = (item: PublishedTemplate) => {
    const next = [publicationKey(item), ...recent.filter(key => key !== publicationKey(item))].slice(0, 12);
    setRecent(next); storeKeys(recentStorageKey, next); onAdd(item);
  };
  const toggleFavorite = (item: PublishedTemplate) => {
    const key = publicationKey(item);
    const next = favorites.includes(key) ? favorites.filter(value => value !== key) : [key, ...favorites];
    setFavorites(next); storeKeys(favoritesStorageKey, next);
  };
  const confirmClassification = (item: PublishedTemplate) => {
    const key = publicationKey(item);
    const next = { ...confirmed, [key]: pendingChoices[key] || classifyPublishedTemplate(item).category };
    setClassifications(next); storeClassifications(next);
  };

  return <section className="published-library" aria-label="已发布模板库">
    <div className="panel-title"><span><Boxes size={16}/>已发布模板</span><div className="library-actions"><small>{initialized || items.length ? items.length : "—"}</small><button className="library-refresh" onClick={() => void refresh("sync")} disabled={loading} aria-label={error ? "重试读取发布库" : "刷新发布库"} title="刷新发布库"><RefreshCw size={14} className={loading ? "spinning" : ""}/></button></div></div>
    <p className="hint">按功能和零件特性筛选 · 新零件先确认分类</p>
    <label className="library-search"><Search size={14}/><input aria-label="搜索模板名称或编码" placeholder="搜索名称或编码" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="library-view-tabs" aria-label="模板视图">
      {([['all', '全部'], ['favorites', `收藏 ${favorites.length}`], ['recent', `最近 ${recent.length}`]] as const).map(([key, label]) => <button key={key} type="button" className={view === key ? "active" : ""} onClick={() => setView(key)}>{label}</button>)}
      <button type="button" disabled title="需要装配端选中接口，并由发布列表提供接口契约摘要">仅兼容</button>
    </div>
    <button className={`library-pending-toggle ${view === "pending" ? "active" : ""}`} type="button" onClick={() => { setView("pending"); setCategory("all"); setCharacteristic("all"); }}>待确认分类 <b>{pendingItems.length}</b></button>
    <div className="library-category-filter" aria-label="按功能分类">
      <button type="button" className={category === "all" && view !== "pending" ? "active" : ""} onClick={() => { setCategory("all"); setView("all"); }}>全部零部件</button>
      {libraryCategories.map(([key, label]) => <button key={key} type="button" className={category === key && view !== "pending" ? "active" : ""} onClick={() => { setCategory(key); setView("all"); }}>{label}<small>{categoryCounts[key]}</small></button>)}
    </div>
    <label className="library-trait-filter"><span>零件特性</span><select value={characteristic} onChange={event => setCharacteristic(event.target.value)}><option value="all">全部特性</option>{traits.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    <p className="library-classification-note">已确认分类用于正式目录；新零件先进入待确认分类，由名称和编码生成建议。</p>
    <div className="library-status" role="status" aria-live="polite">{loading ? initialized || items.length ? "正在检查模板更新…" : "正在准备模板…" : updatedAt ? `上次检查：${formatDate(updatedAt)}` : "尚未连接发布库"}</div>
    {updateCount > 0 && <div className="library-update" role="status"><span>发现 {updateCount} 项模板更新</span><button className="ghost" onClick={() => void refresh("apply")}>更新列表</button></div>}
    {error && <div className="library-error" role="alert"><AlertTriangle size={15}/><div><b>{items.length ? "暂时无法检查模板更新" : "发布库暂未完整加载"}</b><p>{formatLibraryError(error, items.length > 0)}</p><button className="ghost" onClick={() => void refresh("sync")} disabled={loading}>重试</button></div></div>}
    {view === "pending" ? <section className="library-pending-list" aria-label="待确认分类列表">
      <header><b>待确认分类</b><small>{pendingItems.length} 项新零件</small></header>
      {pendingItems.length === 0 && <p>暂无需要确认的新零件。</p>}
      {pendingItems.filter(item => `${item.name} ${item.code}`.toLocaleLowerCase().includes(term)).map(item => {
        const suggestion = classifyPublishedTemplate(item); const key = publicationKey(item);
        return <div className="library-pending-row" key={key}><span><b>{item.name}</b><small>{item.code} · 建议：{suggestion.categoryLabel}</small></span><select aria-label={`${item.name} 分类`} value={pendingChoices[key] || suggestion.category} onChange={event => setPendingChoices(current => ({ ...current, [key]: event.target.value as LibraryCategory }))}>{libraryCategories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button type="button" onClick={() => confirmClassification(item)}>确认</button></div>;
      })}
    </section> : <div aria-busy={loading}>{libraryGroups.map(group => {
      const groupItems = visible.filter(item => (item.templateKind || "part") === group.kind);
      const total = items.filter(item => (item.templateKind || "part") === group.kind).length;
      return <section key={group.kind} className={`library-source-group library-source-${group.kind}`} aria-label={`${group.source} · ${group.title}`}>
        <header className="library-source-heading">
          <span className="library-source-icon"><group.Icon size={18} aria-hidden="true"/></span>
          <div><small>{group.source}</small><h3>{group.title}</h3></div>
          <span className="library-source-count" aria-label={term ? `匹配 ${groupItems.length} 项，共 ${total} 项` : `共 ${total} 项`}>{term ? `${groupItems.length} / ${total}` : total}</span>
        </header>
        <p className="library-source-description">{group.description}</p>
        {groupItems.length === 0 && <p className="library-source-empty">{filtersActive ? "当前筛选下没有模板。" : loading ? "正在读取此来源的模板…" : error || !initialized ? "暂未读取到此来源的模板。" : `暂无${group.title}。`}</p>}
        {groupItems.map(item => {
      const added = memberPublicationIds.includes(item.publicationId);
      const classification = classificationFor(item);
      const favorite = favorites.includes(publicationKey(item));
      return <Fragment key={publicationKey(item)}><div className="publication-card-row">
      <button className={`part-card publication-card ${selectedId === publicationKey(item) ? "selected" : ""}`} draggable={!addDisabled} onDragStart={event => { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", item.publicationId); onDragTemplate?.(item); }} onDragEnd={() => onDragTemplate?.(null)} onClick={() => { setSelectedId(current => current === publicationKey(item) ? null : publicationKey(item)); onPreview(item); }} aria-pressed={selectedId === publicationKey(item)} title="拖动到三维画布以加入组件">
      <i/><span><b>{item.name}</b><small className="publication-source-badge">{group.source}发布</small><small className="publication-classification">{item.templateKind === "component" ? "可复用组件" : `${classification.categoryLabel} · ${classification.characteristic}`}</small><small>{item.code} · V{item.version}</small><small>{formatDate(item.createdAt)}</small></span>
    </button>
    <button className={`publication-favorite ${favorite ? "active" : ""}`} type="button" onClick={() => toggleFavorite(item)} aria-label={favorite ? `取消收藏 ${item.name}` : `收藏 ${item.name}`} title={favorite ? "取消收藏" : "收藏"}><Star size={15} fill={favorite ? "currentColor" : "none"}/></button>
    <button className="publication-add" type="button" disabled={added || addDisabled} onClick={() => addTemplate(item)} aria-label={added ? `已加入 ${item.name}` : `加入 ${item.name}`} title={added ? "已加入组件" : "加入组件"}><Plus size={16}/></button></div>
    {selected && publicationKey(selected) === publicationKey(item) && <section className="publication-details" aria-label="所选模板发布详情"><h3>发布详情</h3><dl>
      <dt>发布来源</dt><dd>{group.source} · {group.title}</dd>
      <dt>模板名称</dt><dd>{selected.name}</dd>
      {selected.templateKind !== "component" && <><dt>功能分类</dt><dd>{classificationFor(selected).categoryLabel}</dd><dt>零件特性</dt><dd>{classificationFor(selected).characteristic}</dd></>}
      <dt>编码 / 版本</dt><dd>{selected.code} / V{selected.version}</dd>
      <dt>发布时间</dt><dd>{formatDate(selected.createdAt)}</dd>
      <dt>发布记录 ID</dt><dd>{selected.publicationId}</dd>
      <dt>模板 ID</dt><dd>{selected.templateId}</dd>
      <dt>模板包大小</dt><dd>{formatSize(selected.size)}</dd>
      <dt>SHA-256</dt><dd className="publication-hash">{selected.sha256}</dd>
    </dl>{selected.templateKind === "component" && selected.engineeringStatus === "incomplete" && <p className="hint">结构版本已发布，工程检查尚未全部通过。</p>}
    <button className="primary full" disabled={addDisabled || added} onClick={() => addTemplate(selected)}>{added ? "已加入当前组件" : "加入当前组件"}</button>
    {selected.templateKind === "component" && <a className="ghost publication-download" href={`/api/published-components/${encodeURIComponent(selected.publicationId)}/download`} download>下载组件包</a>}
    <p className="hint">加入时固定此发布版本；后续发布不会自动替换已有成员。</p></section>}
    </Fragment>;
    })}</section>;
    })}</div>}
  </section>;
}
