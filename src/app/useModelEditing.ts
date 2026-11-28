import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import type { ComponentAction } from "../domain/componentReducer";
import { componentReducer } from "../domain/componentReducer";
import { resolveComponentComposition } from "../domain/composition";
import { memberGroundPosition } from "../library/memberModels";
import type { ComponentDocument, ComponentMember, PartTemplateReference, Vector3 } from "../domain/component";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { fetchPublishedComponent } from "../library/componentPublications";
import { captureModel, cloneSelectionModel, createSelectionGroup, groupForMember, materializeComponentModel, ungroupSelection } from "../domain/assemblyGroups";
import { captureModelHistory, restoreModelHistory, canRestoreModelHistory } from "../domain/modelHistory";
import type { ModelHistoryEntry } from "../domain/modelHistory";
import type { SharedDraftController } from "./sharedDraft";

type SelectionMode = "replace" | "add" | "remove";
type MemberTransform = { id: string; position: Vector3; rotation: Vector3 };
type ModelClipboard = { document: ComponentDocument; memberIds: string[]; anchor: Vector3 };
type Props = {
  controller: RefObject<SharedDraftController | null>;
  draftId: string | null;
  document: ComponentDocument;
  enabled: boolean;
  setMessage: (message: string) => void;
  setPreviewTemplate: (template: PublishedTemplate | null) => void;
};

export function useModelEditing({ controller, draftId, document, enabled, setMessage, setPreviewTemplate }: Props) {
  const [selectedMemberIds, setSelectedMemberIds] = useState<string[]>([]);
  const selectedMemberId = selectedMemberIds[0] ?? null;
  const clipboard = useRef<ModelClipboard | null>(null);
  const pasteActive = useRef(false);
  const [pasteMembers, setPasteMembers] = useState<ComponentMember[] | null>(null);
  const [pasteAnchor, setPasteAnchor] = useState<Vector3>([0, 0, 0]);
  type ModelState = Extract<ComponentAction, { type: "model.restore" }>["model"];
  const modelHistory = useRef<ModelHistoryEntry[]>([]);
  const [undoCount, setUndoCount] = useState(0);
  const [undoing, setUndoing] = useState(false);
  const undoLock = useRef(false);
  const componentEditLock = useRef(false);
  const editable = enabled && !undoing;
  useEffect(() => {
    modelHistory.current = []; setUndoCount(0);
    clipboard.current = null; pasteActive.current = false; setSelectedMemberIds([]); setPasteMembers(null);
  }, [draftId]);
  useEffect(() => {
    const liveIds = new Set(document.assembly.members.map(member => member.id));
    setSelectedMemberIds(ids => ids.some(id => !liveIds.has(id)) ? ids.filter(id => liveIds.has(id)) : ids);
  }, [document.assembly.members]);
  function modelSnapshot(current: ComponentDocument): ModelState {
    return captureModel(current);
  }
  function rememberModel(current = controller.current?.getSnapshot().document, after?: ComponentDocument, reconciled = false) {
    if (reconciled) {
      modelHistory.current = []; setUndoCount(0);
      return;
    }
    if (!current) return;
    modelHistory.current = [...modelHistory.current, captureModelHistory(current, after)].slice(-15);
    setUndoCount(modelHistory.current.length);
  }
  function commitModel(model: ModelState): boolean {
    const workspace = controller.current;
    if (!workspace || !editable || undoLock.current) return false;
    const current = workspace.getSnapshot().document;
    if (JSON.stringify(modelSnapshot(current)) === JSON.stringify(model)) return false;
    const initial = workspace.getSnapshot();
    void workspace.commit([{ type: "model.restore", model }], saved => {
      if (controller.current === workspace && workspace.getSnapshot().draftId === initial.draftId) rememberModel(current, saved.document, saved.reconciled);
    }).catch(() => {
      if (controller.current === workspace && workspace.getSnapshot().draftId === initial.draftId) setMessage("模型操作尚未保存，请处理草稿同步状态后重试。");
    });
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
      const entry = modelHistory.current.at(-1)!;
      if (!canRestoreModelHistory(entry, workspace.getSnapshot().document)) {
        modelHistory.current = []; setUndoCount(0);
        setMessage("模型已被其他操作更新，旧撤销快照已失效，当前模型保持不变。");
        return;
      }
      const model = restoreModelHistory(entry, workspace.getSnapshot().document);
      await workspace.commit([{ type: "model.restore", model }], () => {
        if (controller.current !== workspace || workspace.getSnapshot().draftId !== draftId) return;
        modelHistory.current.pop(); setUndoCount(modelHistory.current.length);
        setSelectedMemberIds([]); cancelPaste(); setPreviewTemplate(null);
        setMessage("已撤销上一步模型操作，最多保留 15 步。");
      });
    } catch { setMessage("撤回尚未保存，请处理草稿同步状态后重试。"); }
    finally { undoLock.current = false; setUndoing(false); }
  }
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

  return { editable, selectedMemberId, selectedMemberIds, setSelectedMemberId, selectMembers, cancelPaste,
    removeMember, copyMember, duplicateMembers, copySelection, startPaste, pasteAt, pasteMembers, pasteAnchor,
    removeSelection: () => removeMembers(selectedMemberIds), toggleGroup, enterComponent,
    moveMember, transformMember, transformMembers, setPlanarMovement, groundMember, undoModel, undoCount, undoing,
    rememberModel };
}
