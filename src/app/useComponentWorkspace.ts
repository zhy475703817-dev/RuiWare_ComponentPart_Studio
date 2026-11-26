import { useEffect, useRef, useState } from "react";
import type { ComponentAction } from "../domain/componentReducer";
import { componentReducer } from "../domain/componentReducer";
import { resolveComponentComposition } from "../domain/composition";
import { placeNextMember } from "../domain/assemblyPlacement";
import { memberBounds, memberGroundPosition } from "../library/memberModels";
import { SharedDraftController, httpDraftApi, initialSharedState } from "./sharedDraft";
import type { PublishedTemplate } from "../library/publishedLibrary";
import type { ComponentDocument, ComponentMember, ComponentSlot, InterfaceSource, Mate, MemberParameterDefinition, PartTemplateReference, Vector3 } from "../domain/component";
import { interfacesForReconstruction, loadReconstructionInfo } from "../library/templatePreview";
import { STAGES, type StageId } from "./stages";
import { componentPublicationMember, fetchPublishedComponent, publishComponentDraft } from "../library/componentPublications";
import { exportEvidenceFiles } from "../domain/evidenceStore";
import { assessInterfaceCompatibility } from "../domain/interfaceCompatibility";
import { alignInterfaceFaces } from "../domain/faceAlignment";
import { loadConnectionGeometry } from "../library/connectionGeometry";
import { connectionStateKey } from "../domain/connectionState";
import { captureModel, cloneSelectionModel, createSelectionGroup, groupForMember, materializeComponentModel, ungroupSelection } from "../domain/assemblyGroups";

const initialMessage = "完善组件定义信息后，可进入装配阶段选择成员";
type SelectionMode = "replace" | "add" | "remove";
type MemberTransform = { id: string; position: Vector3; rotation: Vector3 };
type ModelClipboard = { document: ComponentDocument; memberIds: string[]; anchor: Vector3 };

export function useComponentWorkspace() {
  const [shared, setShared] = useState(initialSharedState);
  const controller = useRef<SharedDraftController | null>(null);
  const [stage, setStageState] = useState<StageId>(() => {
    const requested = new URL(window.location.href).searchParams.get("stage");
    return STAGES.some(item => item.id === requested) ? requested as StageId : "definition";
  });
  const [selectedMemberIds, setSelectedMemberIds] = useState<string[]>([]);
  const selectedMemberId = selectedMemberIds[0] ?? null;
  const clipboard = useRef<ModelClipboard | null>(null);
  const pasteActive = useRef(false);
  const [pasteMembers, setPasteMembers] = useState<ComponentMember[] | null>(null);
  const [pasteAnchor, setPasteAnchor] = useState<Vector3>([0, 0, 0]);
  const [previewTemplate, setPreviewTemplate] = useState<PublishedTemplate | null>(null);
  const [selectionResetToken, setSelectionResetToken] = useState(0);
  const [message, setMessage] = useState(initialMessage);
  const [publishing, setPublishing] = useState(false);
  const [adding, setAdding] = useState(false);
  const addingLock = useRef(false);
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);
  const { document, checks } = shared;
  type ModelState = Extract<ComponentAction, { type: "model.restore" }>["model"];
  const modelHistory = useRef<ModelState[]>([]);
  const [undoCount, setUndoCount] = useState(0);
  const [undoing, setUndoing] = useState(false);
  const undoLock = useRef(false);
  const componentEditLock = useRef(false);
  useEffect(() => {
    modelHistory.current = []; setUndoCount(0);
    clipboard.current = null; pasteActive.current = false; setSelectedMemberIds([]); setPasteMembers(null);
  }, [shared.draftId]);
  useEffect(() => {
    const liveIds = new Set(document.assembly.members.map(member => member.id));
    setSelectedMemberIds(ids => ids.some(id => !liveIds.has(id)) ? ids.filter(id => liveIds.has(id)) : ids);
  }, [document.assembly.members]);
  function modelSnapshot(current: ComponentDocument): ModelState {
    return captureModel(current);
  }
  function rememberModel(current = controller.current?.getSnapshot().document) {
    if (!current) return;
    modelHistory.current = [...modelHistory.current, modelSnapshot(current)].slice(-15);
    setUndoCount(modelHistory.current.length);
  }
  function commitModel(model: ModelState): boolean {
    const workspace = controller.current;
    if (!workspace || !editable || undoLock.current) return false;
    const current = workspace.getSnapshot().document;
    if (JSON.stringify(modelSnapshot(current)) === JSON.stringify(model)) return false;
    if (!workspace.dispatch({ type: "model.restore", model })) return false;
    rememberModel(current);
    return true;
  }
  async function undoModel() {
    const workspace = controller.current;
    if (!workspace || undoLock.current || !editable || !modelHistory.current.length) return;
    const draftId = workspace.getSnapshot().draftId;
    undoLock.current = true;
    setUndoing(true);
    try {
      await workspace.flush();
      if (controller.current !== workspace || workspace.getSnapshot().draftId !== draftId) return;
      const model = modelHistory.current.at(-1)!;
      if (!workspace.dispatch({ type: "model.restore", model })) return;
      modelHistory.current.pop(); setUndoCount(modelHistory.current.length);
      setSelectedMemberIds([]); cancelPaste(); setPreviewTemplate(null);
      await workspace.flush();
      setMessage("已撤销上一步模型操作，最多保留 15 步。");
    } catch { setMessage("撤回尚未保存，请处理草稿同步状态后重试。"); }
    finally { undoLock.current = false; setUndoing(false); }
  }
  const assemblyStateKey = connectionStateKey(shared.draftId, document);
  const geometryKey = JSON.stringify([shared.draftId, document.assembly.members.map(member => [member.id, member.template, member.parameterValues]), document.assembly.mates]);
  const syncedGeometry = useRef("");
  const [geometryStatus, setGeometryStatus] = useState<{ key: string; error: string } | null>(null);
  const editable = (shared.status === "ready" || shared.status === "saving") && !publishing && !adding && !undoing;

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

  function selectMembers(ids: string[], mode: SelectionMode = "replace") {
    const liveIds = new Set((controller.current?.getSnapshot().document ?? document).assembly.members.map(member => member.id));
    const requested = [...new Set(ids)].filter(id => liveIds.has(id));
    setSelectedMemberIds(current => mode === "add" ? [...new Set([...current, ...requested])]
      : mode === "remove" ? current.filter(id => !requested.includes(id)) : requested);
  }

  function setSelectedMemberId(id: string | null, mode: SelectionMode = "replace") {
    const group = id ? groupForMember((controller.current?.getSnapshot().document ?? document).assembly, id) : undefined;
    selectMembers(group?.memberIds ?? (id ? [id] : []), mode);
  }

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

  function removeMember(id: string) {
    removeMembers([id]);
  }

  function removeMembers(ids: string[]) {
    const current = controller.current?.getSnapshot().document;
    if (!editable || !current) return;
    const next = ids.reduce((value, id) => componentReducer(value, { type: "member.remove", id }), current);
    if (!commitModel(modelSnapshot(next))) return;
    setSelectedMemberIds(selected => selected.filter(id => !ids.includes(id)));
    setMessage("已删除成员，并清理关联的连接、接口及参数绑定");
  }

  function copyMember(id: string) {
    const current = controller.current?.getSnapshot().document;
    if (!current || !editable) return;
    const group = groupForMember(current.assembly, id);
    const result = cloneSelectionModel(current, group?.memberIds ?? [id], [40, 0, 0], () => crypto.randomUUID());
    if (!commitModel(result.model)) return;
    selectMembers(result.memberIds);
    setMessage("已复制所选零部件或组件。");
  }

  function duplicateMembers(ids: string[], offset: Vector3) {
    const current = controller.current?.getSnapshot().document;
    if (!current || !editable || !offset.every(Number.isFinite) || offset.every(value => value === 0)) return;
    const result = cloneSelectionModel(current, ids, offset, () => crypto.randomUUID());
    if (!result.memberIds.length || !commitModel(result.model)) return;
    selectMembers(result.memberIds);
    setMessage(`已平移复制 ${result.memberIds.length} 个成员。`);
  }

  function copySelection() {
    const current = controller.current?.getSnapshot().document;
    if (!current || !selectedMemberIds.length) return;
    const ids = selectedMemberIds.filter(id => current.assembly.members.some(member => member.id === id));
    const copied = resolveComponentComposition(current).filter(member => ids.includes(member.sourceMemberId));
    if (!copied.length) return;
    const anchor: Vector3 = [
      (Math.min(...copied.map(member => member.position[0])) + Math.max(...copied.map(member => member.position[0]))) / 2,
      (Math.min(...copied.map(member => member.position[1])) + Math.max(...copied.map(member => member.position[1]))) / 2,
      Math.min(...copied.map(member => member.position[2])),
    ];
    clipboard.current = { document: structuredClone(current), memberIds: ids, anchor };
    cancelPaste();
    setMessage(`已复制 ${ids.length} 个成员，按 Ctrl+V 后在视图中单击放置。`);
  }

  function startPaste() {
    if (!editable || !clipboard.current) return;
    const copied = clipboard.current;
    pasteActive.current = true;
    setPasteMembers(resolveComponentComposition(copied.document).filter(member => copied.memberIds.includes(member.sourceMemberId)));
    setPasteAnchor(copied.anchor);
    setPreviewTemplate(null);
    setMessage("移动鼠标预览粘贴位置，单击放置；按 Esc 取消。");
  }

  function cancelPaste() { pasteActive.current = false; setPasteMembers(null); }

  function pasteAt(point: Vector3) {
    const current = controller.current?.getSnapshot().document;
    const copied = clipboard.current;
    if (!current || !copied || !pasteActive.current || !point.every(Number.isFinite)) return;
    const offset = point.map((coordinate, index) => coordinate - copied.anchor[index]) as Vector3;
    const result = cloneSelectionModel(copied.document, copied.memberIds, offset, () => crypto.randomUUID(), current);
    if (!commitModel(result.model)) return;
    setSelectedMemberIds(result.memberIds);
    cancelPaste();
    setMessage(`已粘贴 ${result.memberIds.length} 个成员。`);
  }

  function moveMember(id: string, position: Vector3) {
    const member = document.assembly.members.find(item => item.id === id);
    if (!member) return;
    moveConnectedMembers(id, [position[0] - member.position[0], position[1] - member.position[1], position[2] - member.position[2]]);
  }

  function transformMember(id: string, position: Vector3, rotation: Vector3) {
    transformMembers([{ id, position, rotation }]);
  }

  function transformMembers(transforms: MemberTransform[]) {
    const current = controller.current?.getSnapshot().document;
    if (!current || !editable) return;
    const patches = new Map<string, MemberTransform>();
    for (const transform of transforms) {
      if (![...transform.position, ...transform.rotation].every(Number.isFinite)) return;
      const source = current.assembly.members.find(member => member.id === transform.id);
      if (!source) continue;
      patches.set(transform.id, transform);
    }
    const explicit = new Set(patches.keys());
    for (const transform of [...patches.values()]) {
      const source = current.assembly.members.find(member => member.id === transform.id)!;
      const delta = transform.position.map((value, index) => value - source.position[index]);
      for (const id of connectedMemberIds(transform.id, current)) {
        if (explicit.has(id) || patches.has(id)) continue;
        const member = current.assembly.members.find(item => item.id === id)!;
        patches.set(id, { id, position: member.position.map((value, index) => value + delta[index]) as Vector3, rotation: member.rotation });
      }
    }
    const model = modelSnapshot(current);
    model.assembly.members = model.assembly.members.map(member => {
      const patch = patches.get(member.id);
      return patch ? { ...member, position: patch.position, rotation: patch.rotation } : member;
    });
    commitModel(model);
  }

  function setPlanarMovement(id: string, enabled: boolean) {
    const current = controller.current?.getSnapshot().document;
    if (!current) return;
    const ids = selectedMemberIds.includes(id) ? selectedMemberIds : [id];
    const model = modelSnapshot(current);
    model.assembly.members = model.assembly.members.map(member => ids.includes(member.id) ? { ...member, planarMovement: enabled } : member);
    commitModel(model);
  }

  function connectedMemberIds(id: string, current = document): Set<string> {
    const result = new Set<string>([id]);
    const queue = [id];
    while (queue.length) {
      const currentId = queue.shift()!;
      current.assembly.mates.forEach(mate => {
        const neighbor = mate.from.memberId === currentId ? mate.to.memberId : mate.to.memberId === currentId ? mate.from.memberId : null;
        if (neighbor && !result.has(neighbor)) { result.add(neighbor); queue.push(neighbor); }
      });
    }
    return result;
  }

  function moveConnectedMembers(id: string, delta: Vector3) {
    const item = document.assembly.members.find(candidate => candidate.id === id);
    if (item) transformMembers([{ id, position: item.position.map((value, index) => value + delta[index]) as Vector3, rotation: item.rotation }]);
  }

  async function groundMember(id: string) {
    const workspace = controller.current;
    if (!workspace || !editable) return;
    const initial = workspace.getSnapshot();
    const ids = selectedMemberIds.includes(id) ? selectedMemberIds : [id];
    const members = initial.document.assembly.members.filter(item => ids.includes(item.id));
    if (!initial.draftId || !members.length) return;
    const geometryState = (item: ComponentMember) => JSON.stringify([item.template, item.position, item.rotation, item.parameterValues]);
    const initialGeometry = members.map(geometryState).join(";");
    try {
      const grounded = await Promise.all(members.map(member => memberGroundPosition(member)));
      const current = workspace.getSnapshot();
      if (controller.current !== workspace || current.draftId !== initial.draftId) return;
      const currentMembers = current.document.assembly.members.filter(item => ids.includes(item.id));
      if (currentMembers.map(geometryState).join(";") !== initialGeometry) {
        setMessage("成员已变化，请重新点击贴地。");
        return;
      }
      const minimumZ = Math.min(...members.map((member, index) => member.position[2] - grounded[index][2]));
      transformMembers(currentMembers.map(member => ({ id: member.id, position: [member.position[0], member.position[1], member.position[2] - minimumZ], rotation: member.rotation })));
      setMessage("已将所选物体整体贴地，当前最低点 z = 0 mm。");
    } catch {
      if (controller.current === workspace && workspace.getSnapshot().draftId === initial.draftId) {
        setMessage("无法计算模型最低点，请检查该成员的模型或发布版本。");
      }
    }
  }

  async function enterComponent(id: string, childPath: string[]) {
    const workspace = controller.current;
    if (!workspace || !editable || componentEditLock.current) return;
    const initial = workspace.getSnapshot();
    const member = initial.document.assembly.members.find(item => item.id === id);
    if (member?.template.templateKind !== "component") return;
    componentEditLock.current = true;
    setMessage("正在读取组件中的零部件…");
    try {
      const result = await materializeComponentModel(initial.document, id, loadComponentDocument,
        { keepGroup: true, childPath, idFactory: () => crypto.randomUUID() });
      const current = workspace.getSnapshot();
      if (controller.current !== workspace || current.draftId !== initial.draftId || current.document.revision !== initial.document.revision) {
        setMessage("模型已变化，请重新双击零部件。"); return;
      }
      if (!commitModel(result.model)) return;
      setSelectedMemberIds(result.selectedMemberId ? [result.selectedMemberId] : result.memberIds);
      setMessage("已进入组件，当前仅选中内部零部件；单击组件可重新整体选择。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "组件中的零部件暂时无法读取。"); }
    finally { componentEditLock.current = false; }
  }

  async function loadComponentDocument(publicationId: string, reference: PartTemplateReference) {
    const publication = await fetchPublishedComponent(publicationId);
    if (publication.templateId !== reference.templateId || publication.version !== reference.version || publication.sha256.toLowerCase() !== reference.sha256.toLowerCase()) {
      throw new Error("组件发布版本已变化，请刷新列表后重新加入。");
    }
    return publication.document;
  }

  async function toggleGroup() {
    const workspace = controller.current;
    if (!workspace || !editable || componentEditLock.current || !selectedMemberIds.length) return;
    const initial = workspace.getSnapshot();
    const selected = initial.document.assembly.members.filter(member => selectedMemberIds.includes(member.id));
    const groups = initial.document.assembly.groups ?? [];
    const completeGroups = groups.filter(group => group.memberIds.every(id => selectedMemberIds.includes(id)));
    const selectedGroups = completeGroups.filter(group => !group.parentId || !completeGroups.some(parent => parent.id === group.parentId));
    const onlyGroupsSelected = selectedGroups.length > 0 && selected.every(member => selectedGroups.some(group => group.memberIds.includes(member.id)));
    if (onlyGroupsSelected) {
      const model = modelSnapshot(initial.document);
      model.assembly = ungroupSelection(initial.document.assembly, selectedGroups.map(group => group.id));
      if (commitModel(model)) setMessage("已解组，零部件位置和角度保持不变。");
      return;
    }
    if (selected.length === 1 && selected[0].template.templateKind === "component") {
      componentEditLock.current = true;
      setMessage("正在将组件解组为独立零部件…");
      try {
        const result = await materializeComponentModel(initial.document, selected[0].id, loadComponentDocument,
          { keepGroup: false, idFactory: () => crypto.randomUUID() });
        const current = workspace.getSnapshot();
        if (controller.current !== workspace || current.draftId !== initial.draftId || current.document.revision !== initial.document.revision) {
          setMessage("模型已变化，请重新选择组件后解组。"); return;
        }
        if (commitModel(result.model)) { setSelectedMemberIds(result.memberIds); setMessage("已解组，零部件位置和角度保持不变。"); }
      } catch (error) { setMessage(error instanceof Error ? error.message : "组件暂时无法解组。"); }
      finally { componentEditLock.current = false; }
      return;
    }
    if (selected.length < 2) { setMessage("请选择至少两个零部件或组件进行组合。"); return; }
    const model = modelSnapshot(initial.document);
    model.assembly = createSelectionGroup(initial.document.assembly, selected.map(member => member.id), `组件 ${groups.length + 1}`, crypto.randomUUID());
    if (commitModel(model)) setMessage(`已将 ${selected.length} 个成员组成组件；再次按 Ctrl+G 解组。`);
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
      rememberModel();
      if (!workspace.dispatch({ type: "mate.add", mate: { id: crypto.randomUUID(), from, to, relation, offset: 0 } })) return false;
      workspace.dispatch({ type: "member.patch", id: bMember.id, patch });
      await workspace.flush();
      setMessage("已保存连接，三维连接面中心重合、法向相反。");
      return true;
    } catch (error) {
      setMessage(`连接失败：${error instanceof Error ? error.message : "无法计算连接面"}`);
      return false;
    }
  }

  function removeConnection(id: string) {
    if (!editable || !document.assembly.mates.some(mate => mate.id === id)) return;
    rememberModel();
    if (!dispatch({ type: "mate.remove", id })) { setMessage("连接已不存在或已刷新"); return; }
    setMessage("已删除接口之间的连接；原有接口保留，零部件恢复独立移动");
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
      if (inheritedInterfaceTypes.length && !workspace.dispatch({ type: "interface.types.merge", types: inheritedInterfaceTypes })) {
        throw new Error("组件接口类型目录与当前草稿冲突，请检查自定义类型名称。");
      }
      const snapshot = workspace.getSnapshot();
      let position: Vector3 | undefined;
      if (template.templateKind !== "component") {
        // Resolve automatic parameter bindings before measuring the displayed shapes.
        const projected = componentReducer(snapshot.document, { type: "member.add", member });
        const instances = resolveComponentComposition(projected);
        const bounds = await Promise.all(instances.map(item => memberBounds(item)));
        const index = instances.findIndex(item => item.id === member.id);
        position = placeNextMember(bounds[index], bounds.filter((_, i) => i !== index));
        if (workspace !== controller.current || workspace.getSnapshot().draftId !== draftId ||
            workspace.getSnapshot().document.revision !== snapshot.document.revision) throw new Error("草稿已更新，请重新加入模板。");
      }
      rememberModel();
      if (!workspace.dispatch({ type: "member.add", member })) throw new Error("草稿暂不可编辑，请先处理同步问题。");
      // Apply placement separately so inferred component dimensions do not resize the parts again.
      if (position) workspace.dispatch({ type: "member.patch", id: member.id, patch: { position } });
      setSelectedMemberId(member.id);
      setPreviewTemplate(null);
      setStage("assembly");
      await workspace.flush();
      setMessage(`已加入 ${template.name} · V${template.version}，可在画布中选择并移动`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "加入失败，请重试。"); }
    finally { addingLock.current = false; setAdding(false); }
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
    setMessage("已下载本地副本；这些修改尚未保存到组件草稿。");
  }

  const geometryMessage = geometryStatus?.key === geometryKey ? geometryStatus.error ? `接口位置更新失败：${geometryStatus.error}` : "正在按当前参数更新接口位置和连接面…" : "";
  const syncMessage = shared.error || geometryMessage || (shared.status === "loading" ? "正在读取组件草稿…" : shared.status === "saving" ? "正在保存组件草稿…" : shared.notice || message);
  return { document, dispatch, stage, setStage, selectedMemberId, selectedMemberIds, setSelectedMemberId, selectMembers, previewTemplate, setPreviewTemplate,
    selectionResetToken, message: syncMessage, setMessage, checks, newDraft, removeMember, addMate, connectInterfaces, removeConnection, setInterfaceExposure, renamePort, setInterfaceType, setInterfaceConnectableTypes, addSlot, patchSlot, removeSlot, exportDraft,
    shared, editable, selectDraft, publishDraft, publishing, adding, addPublishedTemplate, libraryRefreshToken,
    copyMember, duplicateMembers, copySelection, startPaste, cancelPaste, pasteAt, pasteMembers, pasteAnchor,
    removeSelection: () => removeMembers(selectedMemberIds), toggleGroup, enterComponent,
    moveMember, transformMember, transformMembers, setPlanarMovement, groundMember, undoModel, undoCount, undoing,
    downloadLocalDraft, dismissAgentChanges: () => controller.current?.dismissAgentChanges(),
    retrySave: () => { void controller.current?.retry().catch(() => {}); },
    reloadDraft: () => { void controller.current?.reload(); },
  };
}
