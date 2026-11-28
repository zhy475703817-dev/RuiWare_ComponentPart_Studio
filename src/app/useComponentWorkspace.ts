import { useEffect, useRef, useState } from "react";
import type { ComponentAction } from "../domain/componentReducer";
import { componentReducer } from "../domain/componentReducer";
import { resolveComponentComposition } from "../domain/composition";
import { placeNextMember } from "../domain/assemblyPlacement";
import { memberBounds } from "../library/memberModels";
import { SharedDraftController, httpDraftApi, initialSharedState } from "./sharedDraft";
import type { PublishedTemplate } from "../library/publishedLibrary";
import type { ComponentMember, ComponentSlot, InterfaceSource, Mate, MemberParameterDefinition, Vector3 } from "../domain/component";
import { interfacesForReconstruction, loadReconstructionInfo } from "../library/templatePreview";
import { STAGES, type StageId } from "./stages";
import { componentPublicationMember, fetchPublishedComponent, publishComponentDraft } from "../library/componentPublications";
import { downloadComponentExport, downloadDraftBackup } from "./documentExport";
import { assessInterfaceCompatibility } from "../domain/interfaceCompatibility";
import { alignInterfaceFaces } from "../domain/faceAlignment";
import { loadConnectionGeometry } from "../library/connectionGeometry";
import { connectionStateKey } from "../domain/connectionState";
import { useModelEditing } from "./useModelEditing";

const initialMessage = "完善组件定义信息后，可进入装配阶段选择成员";

export function useComponentWorkspace() {
  const [shared, setShared] = useState(initialSharedState);
  const controller = useRef<SharedDraftController | null>(null);
  const [stage, setStageState] = useState<StageId>(() => {
    const requested = new URL(window.location.href).searchParams.get("stage");
    return STAGES.some(item => item.id === requested) ? requested as StageId : "definition";
  });
  const [previewTemplate, setPreviewTemplate] = useState<PublishedTemplate | null>(null);
  const [selectionResetToken, setSelectionResetToken] = useState(0);
  const [message, setMessage] = useState(initialMessage);
  const [draftBackupMessage, setDraftBackupMessage] = useState("");
  useEffect(() => setDraftBackupMessage(""), [shared.draftId]);
  const [publishing, setPublishing] = useState(false);
  const [adding, setAdding] = useState(false);
  const addingLock = useRef(false);
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);
  const { document, checks } = shared;
  const model = useModelEditing({ controller, draftId: shared.draftId, document,
    enabled: (shared.status === "ready" || shared.status === "saving") && !publishing && !adding,
    setMessage, setPreviewTemplate });
  const { editable, selectedMemberId, selectedMemberIds, setSelectedMemberId, selectMembers, cancelPaste,
    removeMember, copyMember, duplicateMembers, copySelection, startPaste, pasteAt, pasteMembers, pasteAnchor,
    removeSelection, toggleGroup, enterComponent, moveMember, transformMember, transformMembers,
    setPlanarMovement, groundMember, undoModel, undoCount, undoing, rememberModel } = model;
  const assemblyStateKey = connectionStateKey(shared.draftId, document);
  const geometryKey = JSON.stringify([shared.draftId, document.assembly.members.map(member => [member.id, member.template, member.parameterValues]), document.assembly.mates]);
  const syncedGeometry = useRef("");
  const [geometryStatus, setGeometryStatus] = useState<{ key: string; error: string } | null>(null);

  useEffect(() => {
    const workspace = controller.current;
    if (!workspace || stage === "parameters" || document.geometryDesign || shared.status !== "ready" || syncedGeometry.current === geometryKey) return;
    const abort = new AbortController();
    const initial = workspace.getSnapshot();
    void (async () => {
      try {
        setGeometryStatus({ key: geometryKey, error: "" });
        const parts = document.assembly.members.filter(member => member.template.templateKind !== "component");
        const loaded = await Promise.all(parts.map(member => loadConnectionGeometry(member, abort.signal)));
        const current = workspace.getSnapshot();
        if (abort.signal.aborted || connectionStateKey(current.draftId, current.document) !== connectionStateKey(initial.draftId, initial.document)) return;
        const refreshed = document.assembly.members.map(member => loaded.find(item => item.member.id === member.id)?.member ?? member);
        const solved = refreshed; // Loading a draft must never reposition its geometry.
        syncedGeometry.current = geometryKey;
        for (const member of solved) {
          const old = document.assembly.members.find(item => item.id === member.id)!;
          const patch = { interfaces: member.interfaces, resolvedInterfaces: member.resolvedInterfaces, position: member.position, rotation: member.rotation };
          if (JSON.stringify(patch) !== JSON.stringify({ interfaces: old.interfaces, resolvedInterfaces: old.resolvedInterfaces, position: old.position, rotation: old.rotation })) {
            if (!workspace.dispatch({ type: "member.patch", id: member.id, patch })) throw new Error("更新后的接口暂时无法保存。");
          }
        }
        setGeometryStatus(null);
        setMessage("已读取当前零件的接口几何信息。");
      } catch (error) {
        if (!abort.signal.aborted) setGeometryStatus({ key: geometryKey, error: error instanceof Error ? error.message : "接口位置更新失败" });
      }
    })();
    return () => abort.abort();
  }, [assemblyStateKey, shared.status, geometryKey, stage]);

  useEffect(() => {
    const workspace = new SharedDraftController(httpDraftApi);
    controller.current = workspace;
    const unsubscribe = workspace.subscribe(() => setShared(workspace.getSnapshot()));
    let preferredId: string | null = null;
    try { preferredId = new URLSearchParams(window.location.search).get("draft") || localStorage.getItem("component-draft-id"); } catch { /* Storage may be disabled. */ }
    void workspace.start(preferredId);
    const timer = window.setInterval(() => { if (!window.document.hidden) void workspace.poll(); }, 2000);
    const refresh = () => { void workspace.poll(); };
    window.addEventListener("focus", refresh);
    return () => { unsubscribe(); workspace.dispose(); window.clearInterval(timer); window.removeEventListener("focus", refresh); controller.current = null; };
  }, []);

  useEffect(() => {
    if (!shared.draftId) return;
    try { localStorage.setItem("component-draft-id", shared.draftId); } catch { /* The current session remains usable. */ }
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
    cancelPaste();
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
    if (publishing || adding || !id || id === shared.draftId) return;
    try { await controller.current?.select(id); if (controller.current?.getSnapshot().status === "ready") resetSelection(); }
    catch { setMessage("请先处理未保存的修改，再切换草稿。"); }
  }

  function addMate(mate: Mate) {
    if (!dispatch({ type: "mate.add", mate })) {
      setMessage("连接未记录：接口可能不兼容、需要复核、已被占用或已存在连接");
      return false;
    }
    setMessage("已记录声明兼容的接口连接；装配求解尚未接入，成员位置未改变");
    return true;
  }

  async function connectInterfaces(from: InterfaceSource, to: InterfaceSource) {
    if (from.memberId === to.memberId) { setMessage("请选择两个不同零部件的接口"); return false; }
    const occupied = (source: InterfaceSource) => document.assembly.mates.some(mate =>
      mate.from.memberId === source.memberId && mate.from.interfaceId === source.interfaceId ||
      mate.to.memberId === source.memberId && mate.to.interfaceId === source.interfaceId);
    if (occupied(from) || occupied(to)) { setMessage("一个零部件接口只能连接一次，请先删除原有连接"); return false; }
    const aMember = document.assembly.members.find(member => member.id === from.memberId);
    const bMember = document.assembly.members.find(member => member.id === to.memberId);
    const aInterface = aMember?.interfaces.find(item => item.id === from.interfaceId);
    const bInterface = bMember?.interfaces.find(item => item.id === to.interfaceId);
    const relation = (["贴合", "同轴", "重合"] as const).find(value => assessInterfaceCompatibility(aInterface, bInterface, value, aMember, bMember).compatible);
    if (!relation || !aMember || !bMember || !aInterface || !bInterface) {
      setMessage("连接失败：接口不存在或连接状态已变化"); return false;
    }
    const workspace = controller.current;
    if (!workspace) return false;
    const initial = workspace.getSnapshot();
    try {
      setMessage("正在读取三维连接面并计算贴合位置…");
      const signal = new AbortController().signal;
      const [aGeometry, bGeometry] = await Promise.all([loadConnectionGeometry(aMember, signal), loadConnectionGeometry(bMember, signal)]);
      const freshA = aGeometry.member.interfaces.find(item => item.id === from.interfaceId);
      const freshB = bGeometry.member.interfaces.find(item => item.id === to.interfaceId);
      if (!freshA || !freshB) throw new Error("参数变化后所选接口已不存在，请重新选择。");
      if (!assessInterfaceCompatibility(freshA, freshB, relation, aGeometry.member, bGeometry.member).compatible) throw new Error("重建后的接口未通过配合检查。");
      const patch = alignInterfaceFaces(aMember, aGeometry.bounds, freshA, bMember, bGeometry.bounds, freshB);
      const current = workspace.getSnapshot();
      if (controller.current !== workspace || connectionStateKey(current.draftId, current.document) !== connectionStateKey(initial.draftId, initial.document)) {
        setMessage("模型或连接状态已变化，请重新连接。"); return false;
      }
      await workspace.commit([
        { type: "mate.add", mate: { id: crypto.randomUUID(), from, to, relation, offset: 0 } },
        { type: "member.patch", id: bMember.id, patch },
      ], saved => {
        if (controller.current === workspace && workspace.getSnapshot().draftId === initial.draftId) rememberModel(initial.document, saved.document, saved.reconciled);
      });
      if (controller.current !== workspace || workspace.getSnapshot().draftId !== initial.draftId) return false;
      setMessage("已保存连接，三维连接面中心重合、法向相反。");
      return true;
    } catch (error) {
      setMessage(`连接失败：${error instanceof Error ? error.message : "无法计算连接面"}`);
      return false;
    }
  }

  function removeConnection(id: string) {
    if (!editable || !document.assembly.mates.some(mate => mate.id === id)) return;
    const workspace = controller.current;
    if (!workspace) return;
    const initial = workspace.getSnapshot();
    void workspace.commit([{ type: "mate.remove", id }], saved => {
      if (controller.current !== workspace || workspace.getSnapshot().draftId !== initial.draftId) return;
      rememberModel(initial.document, saved.document, saved.reconciled);
      setMessage("已删除接口之间的连接；原有接口保留，零部件恢复独立移动");
    }).catch(() => setMessage("删除连接失败，请处理草稿同步状态后重试。"));
  }

  function setInterfaceExposure(memberId: string, interfaceId: string, exposed: boolean) {
    const existing = document.ports.find(port => port.source.memberId === memberId && port.source.interfaceId === interfaceId);
    if (!exposed) {
      if (existing) dispatch({ type: "port.remove", id: existing.id });
      return;
    }
    if (existing) return;
    const member = document.assembly.members.find(item => item.id === memberId);
    const sourceInterface = member?.interfaces.find(item => item.id === interfaceId);
    if (!member || !sourceInterface) return;
    dispatch({ type: "port.add", port: {
      id: crypto.randomUUID(), name: `${member.name}-${sourceInterface.name}`,
      source: { memberId, interfaceId }, tags: [...sourceInterface.tags], interface: structuredClone(sourceInterface),
    } });
  }

  function renamePort(id: string, name: string) {
    dispatch({ type: "port.rename", id, name });
  }

  function setInterfaceType(memberId: string, interfaceId: string, connectionTypeId: string | null, customTypeLabel?: string, connectableTypeIds?: string[]) {
    dispatch({ type: "interface.type.set", memberId, interfaceId, connectionTypeId, ...(customTypeLabel === undefined ? {} : { customTypeLabel }), ...(connectableTypeIds === undefined ? {} : { connectableTypeIds }) });
  }

  function setInterfaceConnectableTypes(memberId: string, interfaceId: string, connectableTypeIds: string[]) {
    dispatch({ type: "interface.connectable.set", memberId, interfaceId, connectableTypeIds });
  }

  function addSlot() {
    const slot: ComponentSlot = { id: crypto.randomUUID(), name: "新槽位", required: false, baseInterface: null, memberId: null, interfaceId: null };
    if (dispatch({ type: "slot.add", slot })) setMessage("已新增槽位，请配置基准接口");
  }

  function patchSlot(id: string, patch: { name?: string; required?: boolean; baseInterface?: InterfaceSource | null; memberId?: string | null; interfaceId?: string | null }) {
    dispatch({ type: "slot.patch", id, patch });
  }

  function removeSlot(id: string) {
    if (dispatch({ type: "slot.remove", id })) setMessage("已删除槽位");
  }

  async function exportDraft() {
    try {
      const exported = await controller.current?.export();
      if (!exported) return;
      downloadComponentExport(exported);
      setMessage("组件草稿已导出；可在发布阶段创建独立发布版本。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "导出失败，请确认草稿已保存。"); }
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
    if (!workspace || !editable || addingLock.current) return;
    addingLock.current = true;
    const draftId = workspace.getSnapshot().draftId;
    setAdding(true);
    setMessage(`正在加入 ${template.name}…`);
    try {
      let member: ComponentMember;
      let inheritedInterfaceTypes: Array<{ id: string; label: string }> = [];
      if (template.templateKind === "component") {
        const publication = await fetchPublishedComponent(template.publicationId);
        if (publication.templateId !== template.templateId || publication.version !== template.version || publication.sha256.toLowerCase() !== template.sha256.toLowerCase()) throw new Error("发布版本已变化，请刷新列表后重试。");
        member = componentPublicationMember(publication, crypto.randomUUID());
        inheritedInterfaceTypes = publication.document.interfaceTypes ?? [];
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
      const snapshot = workspace.getSnapshot();
      const actions: ComponentAction[] = inheritedInterfaceTypes.length ? [{ type: "interface.types.merge", types: inheritedInterfaceTypes }] : [];
      const merged = actions.reduce(componentReducer, snapshot.document);
      let position: Vector3 | undefined;
      if (template.templateKind !== "component") {
        // Resolve automatic parameter bindings before measuring the displayed shapes.
        const projected = componentReducer(merged, { type: "member.add", member });
        const instances = resolveComponentComposition(projected);
        const bounds = await Promise.all(instances.map(item => memberBounds(item)));
        const index = instances.findIndex(item => item.id === member.id);
        position = placeNextMember(bounds[index], bounds.filter((_, i) => i !== index));
        if (workspace !== controller.current || workspace.getSnapshot().draftId !== draftId ||
            workspace.getSnapshot().document.revision !== snapshot.document.revision) throw new Error("草稿已更新，请重新加入模板。");
      }
      actions.push({ type: "member.add", member });
      // Preserve action order: infer dimensions before placing the new instance.
      if (position) actions.push({ type: "member.patch", id: member.id, patch: { position } });
      await workspace.commit(actions, saved => {
        if (workspace === controller.current && workspace.getSnapshot().draftId === draftId) rememberModel(snapshot.document, saved.document, saved.reconciled);
      });
      if (workspace !== controller.current || workspace.getSnapshot().draftId !== draftId) return;
      setSelectedMemberId(member.id);
      setPreviewTemplate(null);
      setStage("assembly");
      setMessage(`已加入 ${template.name} · V${template.version}，可在画布中选择并移动`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加入失败，请重试。"); }
    finally { addingLock.current = false; setAdding(false); }
  }

  async function downloadLocalDraft() {
    const snapshot = controller.current?.getSnapshot() ?? shared;
    setDraftBackupMessage("正在准备本地副本…");
    try {
      await downloadDraftBackup(snapshot.draftId, snapshot.document);
      if (controller.current?.getSnapshot().draftId === snapshot.draftId) setDraftBackupMessage("已下载本地副本，包含当前未保存的输入和附件。");
    } catch (error) {
      if (controller.current?.getSnapshot().draftId === snapshot.draftId) setDraftBackupMessage(error instanceof Error ? error.message : "本地副本下载失败。");
    }
  }

  const geometryMessage = geometryStatus?.key === geometryKey ? geometryStatus.error ? `接口位置更新失败：${geometryStatus.error}` : "正在按当前参数更新接口位置和连接面…" : "";
  const syncMessage = shared.error || geometryMessage || (shared.status === "loading" ? "正在读取组件草稿…" : shared.status === "saving" ? "正在保存组件草稿…" : shared.notice || message);
  return { document, dispatch, stage, setStage, selectedMemberId, selectedMemberIds, setSelectedMemberId, selectMembers, previewTemplate, setPreviewTemplate,
    selectionResetToken, message: syncMessage, setMessage, checks, newDraft, removeMember, addMate, connectInterfaces, removeConnection, setInterfaceExposure, renamePort, setInterfaceType, setInterfaceConnectableTypes, addSlot, patchSlot, removeSlot, exportDraft,
    shared, editable, selectDraft, publishDraft, publishing, adding, addPublishedTemplate, libraryRefreshToken,
    copyMember, duplicateMembers, copySelection, startPaste, cancelPaste, pasteAt, pasteMembers, pasteAnchor,
    removeSelection, toggleGroup, enterComponent,
    moveMember, transformMember, transformMembers, setPlanarMovement, groundMember, undoModel, undoCount, undoing,
    downloadLocalDraft, draftBackupMessage,
    onEvidenceUploaded: (draftId: string, additions: import("../domain/component").EvidenceFile[]) => controller.current?.appendEvidence(draftId, additions) ?? false,
    dismissAgentChanges: () => controller.current?.dismissAgentChanges(),
    retrySave: () => { void controller.current?.retry().catch(() => {}); },
    reloadDraft: () => { void controller.current?.reload(); },
  };
}
