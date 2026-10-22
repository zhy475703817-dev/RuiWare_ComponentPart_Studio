import { useEffect, useRef, useState } from "react";
import type { ComponentAction } from "../domain/componentReducer";
import { SharedDraftController, httpDraftApi, initialSharedState } from "./sharedDraft";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { loadReconstructionInfo } from "../library/templatePreview";
import type { StageId } from "./stages";

const initialMessage = "左侧可浏览已发布零部件，点击模板预览或使用加号加入组件";

export function useComponentWorkspace() {
  const [shared, setShared] = useState(initialSharedState);
  const controller = useRef<SharedDraftController | null>(null);
  const [stage, setStage] = useState<StageId>("definition");
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [previewTemplate, setPreviewTemplate] = useState<PublishedTemplate | null>(null);
  const [selectionResetToken, setSelectionResetToken] = useState(0);
  const [message, setMessage] = useState(initialMessage);
  const { document, checks } = shared;
  const editable = shared.status === "ready" || shared.status === "saving";
  const selectedMember = document.assembly.members.find(member => member.id === selectedMemberId);

  useEffect(() => {
    const workspace = new SharedDraftController(httpDraftApi);
    controller.current = workspace;
    const unsubscribe = workspace.subscribe(() => setShared(workspace.getSnapshot()));
    let preferredId = new URL(window.location.href).searchParams.get("draft");
    try { preferredId ||= localStorage.getItem("component-draft-id"); } catch { /* Storage may be disabled. */ }
    void workspace.start(preferredId);
    const timer = window.setInterval(() => { if (!window.document.hidden) void workspace.poll(); }, 2000);
    const refresh = () => { void workspace.poll(); };
    window.addEventListener("focus", refresh);
    return () => { unsubscribe(); workspace.dispose(); window.clearInterval(timer); window.removeEventListener("focus", refresh); controller.current = null; };
  }, []);

  useEffect(() => {
    if (!shared.draftId) return;
    try { localStorage.setItem("component-draft-id", shared.draftId); } catch { /* A share link still works. */ }
    const url = new URL(window.location.href);
    url.searchParams.set("draft", shared.draftId);
    window.history.replaceState(null, "", url);
  }, [shared.draftId]);

  useEffect(() => {
    if (!shared.dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [shared.dirty]);

  function dispatch(action: ComponentAction) { return controller.current?.dispatch(action) ?? false; }

  function resetSelection() {
    setStage("definition");
    setSelectedMemberId(null);
    setPreviewTemplate(null);
    setSelectionResetToken(token => token + 1);
    setMessage(initialMessage);
  }

  async function newDraft() {
    try { await controller.current?.create(); if (controller.current?.getSnapshot().status === "ready") resetSelection(); }
    catch { setMessage("请先处理未保存的修改，再新建草稿。"); }
  }

  async function selectDraft(id: string) {
    try { await controller.current?.select(id); if (controller.current?.getSnapshot().status === "ready") resetSelection(); }
    catch { setMessage("请先处理未保存的修改，再切换草稿。"); }
  }

  async function copyDraftLink() {
    try { await navigator.clipboard.writeText(window.location.href); setMessage("已复制草稿链接，可用于打开同一份草稿。"); }
    catch { setMessage("复制失败，可直接复制浏览器地址中的草稿链接。"); }
  }

  function removeMember(id: string) {
    if (!dispatch({ type: "member.remove", id })) return;
    if (selectedMemberId === id) setSelectedMemberId(null);
    setMessage("已删除成员，并清理关联的连接、接口及参数绑定");
  }

  async function addPublishedTemplate(template: PublishedTemplate) {
    if (!editable) return setMessage("共享草稿尚未就绪，请稍后再加入零部件");
    const current = controller.current?.getSnapshot().document;
    if (!current) return setMessage("共享草稿尚未就绪，请稍后再加入零部件");
    if (current.assembly.members.some(member => member.template.publicationId === template.publicationId)) {
      return setMessage(`${template.name} 的这个发布版本已经加入组件`);
    }
    setPreviewTemplate(template);
    setMessage(`正在读取 ${template.name} 的接口定义…`);
    try {
      const info = await loadReconstructionInfo(template, new AbortController().signal);
      const member = {
        id: crypto.randomUUID(),
        name: template.name,
        code: template.code,
        role: "零部件",
        template: { publicationId: template.publicationId, templateId: template.templateId, version: template.version, sha256: template.sha256 },
        position: [0, 0, 0] as [number, number, number],
        rotation: [0, 0, 0] as [number, number, number],
        parameterValues: Object.fromEntries(info.parameters.map(parameter => [parameter.id, parameter.default])),
        interfaces: info.interfaces,
      };
      const latest = controller.current?.getSnapshot().document;
      if (!latest || latest.assembly.members.some(item => item.template.publicationId === template.publicationId)) {
        return setMessage(`${template.name} 的这个发布版本已经加入组件`);
      }
      if (!dispatch({ type: "member.add", member })) return setMessage("加入失败，请检查共享草稿状态后重试");
      setSelectedMemberId(member.id);
      setMessage(`已加入 ${template.name} · V${template.version}，可在装配阶段建立连接`);
    } catch (cause) {
      setMessage(cause instanceof Error ? `无法加入 ${template.name}：${cause.message}` : `无法加入 ${template.name}，请重试`);
    }
  }

  function connectFirstInterfaces() {
    const [a, b] = document.assembly.members;
    if (!a || !b) return setMessage("至少加入两个零部件后才能记录内部连接");
    const from = a.interfaces[0], to = b.interfaces[0];
    if (!from || !to) return setMessage("成员缺少可连接接口，请先读取零部件接口定义");
    if (document.assembly.mates.some(mate => mate.from.memberId === a.id && mate.from.interfaceId === from.id && mate.to.memberId === b.id && mate.to.interfaceId === to.id)) return setMessage("这两个接口已记录连接关系");
    if (!dispatch({ type: "mate.add", mate: { id: crypto.randomUUID(), from: { memberId: a.id, interfaceId: from.id }, to: { memberId: b.id, interfaceId: to.id }, relation: "贴合", offset: 0 } })) return;
    setMessage("已记录接口贴合关系；装配求解尚未接入，成员位置未改变");
  }

  function inheritInterface() {
    if (!selectedMember) return setMessage("请先选择一个组件成员");
    const sourceInterface = selectedMember.interfaces.find(item => !document.ports.some(port => port.source.memberId === selectedMember.id && port.source.interfaceId === item.id));
    if (!sourceInterface) return setMessage("该成员没有尚未继承的接口");
    if (!dispatch({ type: "port.add", port: { id: crypto.randomUUID(), name: `${selectedMember.name}-${sourceInterface.name}`, source: { memberId: selectedMember.id, interfaceId: sourceInterface.id }, tags: [...sourceInterface.tags] } })) return;
    setStage("interfaces");
    setMessage(`已继承 ${sourceInterface.name} 为组件对外接口`);
  }

  async function exportDraft() {
    let exported;
    try { exported = await controller.current?.export(); if (!exported) return; }
    catch { setMessage("导出失败，请先确认草稿已保存并处理同步问题。"); return; }
    const blob = new Blob([JSON.stringify(exported, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    const filename = document.definition.name.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_") || "组件模板";
    link.href = url;
    link.download = `${filename}.component.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setMessage("组件草稿已导出，正式发布尚未接入");
  }

  function downloadLocalDraft() {
    const blob = new Blob([JSON.stringify(document, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    link.href = url;
    link.download = "组件草稿-本地未保存副本.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setMessage("已下载本地副本；这些修改尚未保存到共享草稿。");
  }

  const syncMessage = shared.error || (shared.status === "loading" ? "正在读取共享草稿…" : shared.status === "saving" ? "正在保存草稿…" : shared.notice || message);
  return { document, dispatch, stage, setStage, selectedMemberId, selectedMember, setSelectedMemberId, previewTemplate, setPreviewTemplate,
    selectionResetToken, message: syncMessage, setMessage, checks, newDraft, removeMember, addPublishedTemplate, connectFirstInterfaces, inheritInterface, exportDraft,
    shared, editable, selectDraft, copyDraftLink,
    downloadLocalDraft, dismissAgentChanges: () => controller.current?.dismissAgentChanges(),
    retrySave: () => { void controller.current?.retry().catch(() => {}); },
    reloadDraft: () => { void controller.current?.reload(); },
  };
}
