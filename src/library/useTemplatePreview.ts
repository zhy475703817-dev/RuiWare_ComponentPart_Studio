import { useEffect, useRef, useState } from "react";
import type { PublishedTemplate } from "./publishedLibrary";
import { loadReconstructionInfo, loadTemplatePreview } from "./templatePreview";
import type { LoadedPreview, ReconstructionInfo } from "./templatePreview";

export function useTemplatePreview(selected: PublishedTemplate | null, initialValues: Record<string, unknown> = {}) {
  const initialValuesKey = JSON.stringify(initialValues);
  const initialValuesRef = useRef(initialValues);
  const [attempt, setAttempt] = useState(0);
  const [infoState, setInfoState] = useState<{ selected: PublishedTemplate | null; info: ReconstructionInfo | null; error: string }>({ selected: null, info: null, error: "" });
  const [valueState, setValueState] = useState<{ selected: PublishedTemplate | null; values: Record<string, unknown> }>({ selected: null, values: {} });
  const [modelState, setModelState] = useState<{ selected: PublishedTemplate | null; result: LoadedPreview | null; error: string; loading: boolean }>({ selected: null, result: null, error: "", loading: false });

  useEffect(() => { initialValuesRef.current = initialValues; }, [initialValuesKey]);

  useEffect(() => {
    const controller = new AbortController();
    setInfoState({ selected, info: null, error: "" });
    setValueState({ selected, values: {} });
    setModelState({ selected, result: null, error: "", loading: Boolean(selected) });
    if (selected) {
      void loadReconstructionInfo(selected, controller.signal).then(info => {
        if (controller.signal.aborted) return;
        setInfoState({ selected, info, error: "" });
        setValueState({ selected, values: { ...Object.fromEntries(info.parameters.map(item => [item.id, item.default])), ...initialValuesRef.current } });
      }).catch(error => {
        if (!controller.signal.aborted) {
          const message = error instanceof TypeError ? "无法连接组件后端，请检查服务后重试。" : error instanceof Error ? error.message : "零件读取失败，请重试。";
          setInfoState({ selected, info: null, error: message });
          setModelState({ selected, result: null, error: message, loading: false });
        }
      });
    }

    return () => controller.abort();
  }, [selected]);

  useEffect(() => {
    if (!selected || valueState.selected !== selected) return;
    setValueState(current => ({ selected, values: { ...current.values, ...initialValues } }));
  }, [selected, initialValuesKey]);

  useEffect(() => {
    const info = infoState.selected === selected ? infoState.info : null;
    if (!selected || !info || valueState.selected !== selected) return;
    const controller = new AbortController();
    let resource: LoadedPreview | null = null;
    setModelState(current => ({ selected, result: current.selected === selected ? current.result : null, error: "", loading: true }));
    const timer = window.setTimeout(() => {
      void loadTemplatePreview(selected, valueState.values, controller.signal).then(result => {
        if (controller.signal.aborted) { result.geometry.dispose(); return; }
        resource = result;
        setModelState({ selected, result, error: "", loading: false });
      }).catch(error => {
        if (!controller.signal.aborted) setModelState({ selected, result: null, error: error instanceof TypeError ? "无法连接组件后端，请检查服务后重试。" : error instanceof Error ? error.message : "零件暂时无法显示，请重试。", loading: false });
      });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); resource?.geometry.dispose(); };
  }, [selected, infoState, valueState, attempt]);

  return {
    result: modelState.selected === selected ? modelState.result : null,
    error: modelState.selected === selected ? modelState.error : "",
    loading: modelState.selected === selected ? modelState.loading : Boolean(selected),
    info: infoState.selected === selected ? infoState.info : null,
    values: valueState.selected === selected ? valueState.values : {},
    setParameter: (id: string, value: unknown) => setValueState(current => current.selected === selected ? { selected, values: { ...current.values, [id]: value } } : current),
    retry: () => setAttempt(value => value + 1),
  };
}
