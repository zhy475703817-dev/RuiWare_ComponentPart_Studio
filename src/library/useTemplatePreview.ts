import { useEffect, useRef, useState } from "react";
import type { PublishedTemplate } from "./publishedLibrary";
import { loadReconstructionInfo, loadTemplatePreview } from "./templatePreview";
import type { LoadedPreview, ReconstructionInfo } from "./templatePreview";

function templateKey(selected: PublishedTemplate | null) {
  if (!selected) return "";
  return [selected.publicationId, selected.templateId, selected.version, selected.sha256].join("|");
}

const reconstructionCache = new Map<string, Promise<ReconstructionInfo>>();
type PreviewCacheEntry = {
  promise: Promise<LoadedPreview>;
  refs: number;
  lastUsed: number;
};
const previewCache = new Map<string, PreviewCacheEntry>();
let cacheClock = 0;

function cachedReconstruction(selected: PublishedTemplate, values: Record<string, unknown>) {
  const key = `${templateKey(selected)}|${JSON.stringify(values)}`;
  const existing = reconstructionCache.get(key);
  if (existing) return existing;
  const promise = loadReconstructionInfo(selected, new AbortController().signal, values).catch(error => {
    reconstructionCache.delete(key);
    throw error;
  });
  reconstructionCache.set(key, promise);
  return promise;
}

function trimPreviewCache() {
  if (previewCache.size <= 24) return;
  const candidates = [...previewCache.entries()]
    .filter(([, entry]) => entry.refs === 0)
    .sort(([, left], [, right]) => left.lastUsed - right.lastUsed);
  while (previewCache.size > 24 && candidates.length) {
    const [key, entry] = candidates.shift()!;
    previewCache.delete(key);
    void entry.promise.then(result => result.geometry.dispose()).catch(() => undefined);
  }
}

function cachedPreview(selected: PublishedTemplate, values: Record<string, unknown>) {
  const key = `${templateKey(selected)}|${JSON.stringify(values)}`;
  let entry = previewCache.get(key);
  if (!entry) {
    const promise = loadTemplatePreview(selected, values, new AbortController().signal).catch(error => {
      previewCache.delete(key);
      throw error;
    });
    entry = { promise, refs: 0, lastUsed: ++cacheClock };
    previewCache.set(key, entry);
  }
  entry.refs += 1;
  entry.lastUsed = ++cacheClock;
  trimPreviewCache();
  return {
    promise: entry.promise,
    release: () => {
      entry!.refs = Math.max(0, entry!.refs - 1);
      entry!.lastUsed = ++cacheClock;
      trimPreviewCache();
    },
  };
}

export function useTemplatePreview(selected: PublishedTemplate | null, initialValues: Record<string, unknown> = {}) {
  const initialValuesKey = JSON.stringify(initialValues);
  const initialValuesRef = useRef(initialValues);
  const selectedKey = templateKey(selected);
  const [attempt, setAttempt] = useState(0);
  const [infoState, setInfoState] = useState<{ selectedKey: string; selected: PublishedTemplate | null; info: ReconstructionInfo | null; error: string }>({ selectedKey: "", selected: null, info: null, error: "" });
  const [valueState, setValueState] = useState<{ selectedKey: string; selected: PublishedTemplate | null; values: Record<string, unknown> }>({ selectedKey: "", selected: null, values: {} });
  const [modelState, setModelState] = useState<{ selectedKey: string; valueKey: string; selected: PublishedTemplate | null; result: LoadedPreview | null; error: string; loading: boolean }>({ selectedKey: "", valueKey: "", selected: null, result: null, error: "", loading: false });

  useEffect(() => { initialValuesRef.current = initialValues; }, [initialValuesKey]);

  useEffect(() => {
    const controller = new AbortController();
    setInfoState({ selectedKey, selected, info: null, error: "" });
    setValueState({ selectedKey, selected, values: {} });
    setModelState({ selectedKey, valueKey: "", selected, result: null, error: "", loading: Boolean(selected) });
    if (selected) {
      void cachedReconstruction(selected, initialValuesRef.current).then(info => {
        if (controller.signal.aborted) return;
        setInfoState({ selectedKey, selected, info, error: "" });
        setValueState({ selectedKey, selected, values: { ...Object.fromEntries(info.parameters.map(item => [item.id, item.default])), ...initialValuesRef.current } });
      }).catch(error => {
        if (!controller.signal.aborted) {
          const message = error instanceof TypeError ? "无法连接组件后端，请检查服务后重试。" : error instanceof Error ? error.message : "零件读取失败，请重试。";
          setInfoState({ selectedKey, selected, info: null, error: message });
          setModelState({ selectedKey, valueKey: "", selected, result: null, error: message, loading: false });
        }
      });
    }

    return () => controller.abort();
  }, [selectedKey, initialValuesKey]);

  useEffect(() => {
    if (!selected || valueState.selectedKey !== selectedKey) return;
    setValueState(current => ({ selectedKey, selected, values: { ...current.values, ...initialValues } }));
  }, [selectedKey, initialValuesKey]);

  useEffect(() => {
    const info = infoState.selectedKey === selectedKey ? infoState.info : null;
    if (!selected || !info || valueState.selectedKey !== selectedKey) return;
    const valueKey = JSON.stringify(valueState.values);
    let active = true;
    let release: (() => void) | null = null;
    setModelState({ selectedKey, valueKey, selected, result: null, error: "", loading: true });
    const timer = window.setTimeout(() => {
      const cached = cachedPreview(selected, valueState.values);
      release = cached.release;
      void cached.promise.then(result => {
        if (!active) return;
        setModelState({ selectedKey, valueKey, selected, result, error: "", loading: false });
      }).catch(error => {
        if (active) setModelState({ selectedKey, valueKey, selected, result: null, error: error instanceof TypeError ? "无法连接组件后端，请检查服务后重试。" : error instanceof Error ? error.message : "零件暂时无法显示，请重试。", loading: false });
      });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); release?.(); };
  }, [selectedKey, infoState, valueState, attempt]);

  return {
    result: modelState.selectedKey === selectedKey && modelState.valueKey === JSON.stringify(valueState.values) ? modelState.result : null,
    error: modelState.selectedKey === selectedKey && modelState.valueKey === JSON.stringify(valueState.values) ? modelState.error : "",
    loading: modelState.selectedKey !== selectedKey || modelState.valueKey !== JSON.stringify(valueState.values) ? Boolean(selected) : modelState.loading,
    info: infoState.selectedKey === selectedKey ? infoState.info : null,
    values: valueState.selectedKey === selectedKey ? valueState.values : {},
    setParameter: (id: string, value: unknown) => setValueState(current => current.selectedKey === selectedKey ? { selectedKey, selected, values: { ...current.values, [id]: value } } : current),
    retry: () => setAttempt(value => value + 1),
  };
}
