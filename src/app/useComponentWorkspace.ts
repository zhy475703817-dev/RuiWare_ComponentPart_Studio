import { useEffect, useRef, useState } from "react";
import type { ComponentAction } from "../domain/componentReducer";
import { SharedDraftController, httpDraftApi, initialSharedState } from "./sharedDraft";
import type { PublishedTemplate } from "../library/publishedLibrary";
import type { ComponentMember, MemberParameterDefinition, Vector3 } from "../domain/component";
import { interfacesForReconstruction, loadReconstructionInfo } from "../library/templatePreview";
import { STAGES, type StageId } from "./stages";
import { componentPublicationMember, fetchPublishedComponent, publishComponentDraft } from "../library/componentPublications";
import { exportEvidenceFiles } from "../domain/evidenceStore";

const initialMessage = "左侧可浏览已发布零部件，点击模板预览或使用加号加入组件";

export function useComponentWorkspace() {
  const [shared, setShared] = useState(initialSharedState);
  const controller = useRef<SharedDraftController | null>(null);
  const [stage, setStageState] = useState<StageId>(() => {
    const requested = new URL(window.location.href).searchParams.get("stage");
    return STAGES.some(item => item.id === requested) ? requested as StageId : "definition";
  });
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [previewTemplate, setPreviewTemplate] = useState<PublishedTemplate | null>(null);
  const [selectionResetToken, setSelectionResetToken] = useState(0);
  const [message, setMessage] = useState(initialMessage);
  const [publishing, setPublishing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);
  const { document, checks } = shared;
  const editable = (shared.status === "ready" || shared.status === "saving") && !publishing && !adding;
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

  function setStage(next: StageId) {
    setStageState(next);
    const url = new URL(window.location.href);
    url.searchParams.set("stage", next);
    window.history.replaceState(null, "", url);
  }

  function resetSelection() {
    setStage("definition");
    setSelectedMemberId(null);
    setPreviewTemplate(null);
    setSelectionResetToken(token => token + 1);
    setMessage(initialMessage);
  }

  async function newDraft() {
    if (publishing || adding) return;
    try { await controller.current?.create(); if (controller.current?.getSnapshot().status === "ready") resetSelection(); }
    catch { setMessage("请先处理未保存的修改，再新建草稿。"); }
  }

  async function selectDraft(id: string) {
    if (publishing || adding) return;
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

  function copyMember(id: string) {
    const source = document.assembly.members.find(member => member.id === id);
    if (!source) return;
    const member: ComponentMember = structuredClone(source);
    member.id = crypto.randomUUID();
    member.name = `${source.name} 副本`;
    member.role = `${source.role} 副本`;
    member.position = [source.position[0] + 40, source.position[1], source.position[2]];
    if (!dispatch({ type: "member.add", member })) return;
    setSelectedMemberId(member.id);
    setMessage(`已复制 ${source.name}`);
  }

  function moveMember(id: string, position: Vector3) {
    dispatch({ type: "member.patch", id, patch: { position } });
  }

  async function openDraftLink(link: string) {
    let id = link.trim();
    if (!id) return;
    try {
      id = new URL(id, window.location.href).searchParams.get("draft") || id;
      await selectDraft(id);
    } catch { setMessage("链接格式不正确，请粘贴包含 draft 参数的草稿链接。"); }
  }

  function connectFirstInterfaces() {
    const [a, b] = document.assembly.members;
    if (!a || !b) return setMessage("至少加入两个零部件后才能记录内部连接");
    const from = a.interfaces[0], to = b.interfaces[0];
    if (!from || !to) return setMessage("成员缺少可连接接口，请先读取零部件接口定义");
    if (document.assembly.mates.some(mate => mate.from.memberId === a.id && mate.from.interfaceId === from.id && mate.to.memberId === b.id && mate.to.interfaceId === to.id)) return setMessage("这两个接口已记录连接关系");
    if (!dispatch({ type: "mate.add", mate: { id: crypto.randomUUID(), from: { memberId: a.id, interfaceId: from.id }, to: { memberId: b.id, interfaceId: to.id }, relation: "贴合", offset: 0 } })) return;
    setMessage("已记录接口贴合关系；请在预览中确认成员位置，连接关系会随草稿保存");
  }

  function inheritInterface() {
    if (!selectedMember) return setMessage("请先选择一个组件成员");
    const sourceInterface = selectedMember.interfaces.find(item => !document.ports.some(port => port.source.memberId === selectedMember.id && port.source.interfaceId === item.id));
    if (!sourceInterface) return setMessage("该成员没有尚未继承的接口");
    if (!dispatch({ type: "port.add", port: { id: crypto.randomUUID(), name: `${selectedMember.name}-${sourceInterface.name}`, source: { memberId: selectedMember.id, interfaceId: sourceInterface.id }, tags: [...sourceInterface.tags], interface: structuredClone(sourceInterface) } })) return;
    setStage("interfaces");
    setMessage(`已继承 ${sourceInterface.name} 为组件对外接口`);
  }

  async function exportDraft() {
    let exported;
    try { exported = await controller.current?.export(); if (!exported) return; }
    catch { setMessage("导出失败，请先确认草稿已保存并处理同步问题。"); return; }
    const packageWithEvidence = { ...exported as object, evidenceFiles: await exportEvidenceFiles(document.definition.evidence) };
    const blob = new Blob([JSON.stringify(packageWithEvidence, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement("a");
    const filename = document.definition.name.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_") || "组件模板";
    link.href = url;
    link.download = `${filename}.component.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setMessage("组件草稿已导出；可在发布阶段创建独立发布版本。");
  }

  async function publishDraft() {
    const workspace = controller.current;
    if (!workspace || !editable) return;
    const draftId = workspace.getSnapshot().draftId;
    setPublishing(true);
    try {
      await workspace.flush();
      const saved = workspace.getSnapshot();
      if (!draftId || saved.draftId !== draftId) throw new Error("草稿已切换，请重新发布。");
      const publication = await publishComponentDraft(draftId, saved.document.revision);
      setLibraryRefreshToken(token => token + 1);
      setMessage(`已发布 ${publication.name} · V${publication.version}，可在左侧列表选用。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "发布失败，请检查连接后重试。"); }
    finally { setPublishing(false); }
  }

  async function addPublishedTemplate(template: PublishedTemplate) {
    const workspace = controller.current;
    if (!workspace || !editable) return;
    const draftId = workspace.getSnapshot().draftId;
    setAdding(true);
    try {
      let member;
      if (template.templateKind === "component") {
        const publication = await fetchPublishedComponent(template.publicationId);
        if (publication.templateId !== template.templateId || publication.version !== template.version || publication.sha256.toLowerCase() !== template.sha256.toLowerCase()) throw new Error("发布版本已变化，请刷新列表后重试。");
        member = componentPublicationMember(publication, crypto.randomUUID());
      } else {
        const info = await loadReconstructionInfo(template, AbortSignal.timeout(20000));
        const parameterDefinitions: MemberParameterDefinition[] = info.parameters.map(parameter => ({ id: parameter.id, label: parameter.label, unit: parameter.unit,
          valueType: parameter.valueType, minimum: parameter.minimum, maximum: parameter.maximum, default: parameter.default }));
        member = { id: crypto.randomUUID(), name: template.name, code: template.code, role: template.name,
          template: { publicationId: template.publicationId, templateId: template.templateId, version: template.version, sha256: template.sha256 },
          position: [0, 0, 0] as [number, number, number], rotation: [0, 0, 0] as [number, number, number],
          parameterValues: Object.fromEntries(info.parameters.map(parameter => [parameter.id, parameter.default])), parameterDefinitions,
          interfaces: interfacesForReconstruction(info),
          ...(info.resolvedInterfacesAvailable ? { resolvedInterfaces: info.resolvedInterfaces } : {}),
        };
      }
      if (workspace !== controller.current || workspace.getSnapshot().draftId !== draftId) throw new Error("草稿已切换，请重新选择模板。");
      if (!workspace.dispatch({ type: "member.add", member })) throw new Error("草稿暂不可编辑，请先处理同步问题。");
      await workspace.flush();
      setSelectedMemberId(member.id);
      setStage("assembly");
      setMessage(`已加入 ${template.name} · V${template.version}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加入失败，请重试。"); }
    finally { setAdding(false); }
  }

  async function downloadLocalDraft() {
    const backupWithEvidence = { ...document, evidenceFiles: await exportEvidenceFiles(document.definition.evidence) };
    const blob = new Blob([JSON.stringify(backupWithEvidence, null, 2)], { type: "application/json" });
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
    selectionResetToken, message: syncMessage, setMessage, checks, newDraft, removeMember, connectFirstInterfaces, inheritInterface, exportDraft,
    shared, editable, selectDraft, openDraftLink, copyDraftLink, publishDraft, publishing, adding, addPublishedTemplate, libraryRefreshToken,
    copyMember, moveMember,
    downloadLocalDraft, dismissAgentChanges: () => controller.current?.dismissAgentChanges(),
    retrySave: () => { void controller.current?.retry().catch(() => {}); },
    reloadDraft: () => { void controller.current?.reload(); },
  };
}
