import { Euler, Matrix4, Quaternion, Vector3 as ThreeVector3 } from "three";
import type { ComponentDocument, ComponentGroup, ComponentMember, InterfaceSource, PartTemplateReference, Vector3 } from "./component.ts";
import { resolveComponentComposition } from "./composition.ts";

export type ModelState = Pick<ComponentDocument, "assembly" | "ports" | "bindings" | "patterns" | "slots" | "braceGenerationRules">;
type Assembly = ComponentDocument["assembly"];
const newId = () => crypto.randomUUID();
export const captureModel = (document: ComponentDocument): ModelState => structuredClone({ assembly: document.assembly, ports: document.ports, bindings: document.bindings, patterns: document.patterns, slots: document.slots, braceGenerationRules: document.braceGenerationRules ?? [] });

export function groupForMember(assembly: Assembly, memberId: string, topLevel = true): ComponentGroup | undefined {
  const groups = (assembly.groups ?? []).filter(group => group.memberIds.includes(memberId));
  return groups.find(group => topLevel ? !group.parentId : !groups.some(child => child.parentId === group.id)) ?? groups[0];
}

export function groupMemberIds(assembly: Assembly, groupId: string): string[] {
  const ids = new Set(assembly.members.map(member => member.id));
  return assembly.groups?.find(group => group.id === groupId)?.memberIds.filter(id => ids.has(id)) ?? [];
}

export function createSelectionGroup(assembly: Assembly, memberIds: string[], name = "组件", id = newId()): Assembly {
  const selected = new Set(memberIds);
  const ids = assembly.members.filter(member => selected.has(member.id)).map(member => member.id);
  if (!ids.length || assembly.members.some(member => member.id === id) || assembly.groups?.some(group => group.id === id)) return assembly;
  // Wrap whole selected groups; selected leaves move out of unrelated partial groups.
  let groups = structuredClone(assembly.groups ?? []);
  const commonParent = groups.filter(group => ids.every(memberId => group.memberIds.includes(memberId)))
    .sort((left, right) => left.memberIds.length - right.memberIds.length)[0];
  const fullGroups = groups.filter(group => group.memberIds.every(memberId => selected.has(memberId)));
  const wrapped = new Set(fullGroups.filter(group => !group.parentId || !fullGroups.some(parent => parent.id === group.parentId)).map(group => group.id));
  const fullIds = new Set(fullGroups.map(group => group.id));
  // Equal membership can occur when a publication contains just one nested group.
  // Skip every fully selected ancestor before placing the new wrapper.
  let parentId: string | undefined = commonParent?.id;
  while (parentId && fullIds.has(parentId)) parentId = groups.find(group => group.id === parentId)?.parentId;
  const ancestors = new Set<string>();
  let ancestor: string | undefined = parentId;
  while (ancestor) { ancestors.add(ancestor); ancestor = groups.find(group => group.id === ancestor)?.parentId; }
  groups = groups.map(group => ancestors.has(group.id) || fullIds.has(group.id) ? group : { ...group, memberIds: group.memberIds.filter(memberId => !selected.has(memberId)) }).filter(group => group.memberIds.length);
  const surviving = new Set(groups.map(group => group.id));
  for (const group of groups) if (group.parentId && !surviving.has(group.parentId)) delete group.parentId;
  for (const group of groups) if (wrapped.has(group.id)) group.parentId = id;
  groups.push({ id, name, memberIds: ids, ...(parentId ? { parentId } : {}) });
  return { ...assembly, groups };
}

export function ungroupSelection(assembly: Assembly, groupIds: string[]): Assembly {
  const removed = new Set(groupIds);
  if (!assembly.groups?.some(group => removed.has(group.id))) return assembly;
  const original = new Map(assembly.groups.map(group => [group.id, group]));
  return { ...assembly, groups: assembly.groups.filter(group => !removed.has(group.id)).map(group => {
    let parentId = group.parentId;
    while (parentId && removed.has(parentId)) parentId = original.get(parentId)?.parentId;
    const { parentId: _, ...rest } = group;
    return { ...rest, ...(parentId ? { parentId } : {}) };
  }) };
}

/** Copy only references internal to the copied set, keeping other current edits intact. */
export function cloneSelectionModel(source: ComponentDocument, selectedIds: string[], offset: Vector3 = [0, 0, 0], idFactory = newId, target: ComponentDocument = source): { model: ModelState; memberIds: string[] } {
  const selected = new Set(selectedIds);
  const members = source.assembly.members.filter(member => selected.has(member.id));
  const ids = new Map(members.map(member => [member.id, idFactory()]));
  const remap = (reference: InterfaceSource): InterfaceSource => ({ ...reference, memberId: ids.get(reference.memberId)! });
  const contained = (reference: InterfaceSource | null | undefined) => !!reference && ids.has(reference.memberId);
  const groups = source.assembly.groups?.filter(group => group.memberIds.every(id => ids.has(id))) ?? [];
  const groupIds = new Map(groups.map(group => [group.id, idFactory()]));
  const model = captureModel(target);
  model.assembly.members.push(...members.map(member => ({ ...structuredClone(member), id: ids.get(member.id)!, name: `${member.name} 副本`, role: `${member.role} 副本`, position: member.position.map((value, index) => value + offset[index]) as Vector3 })));
  model.assembly.mates.push(...source.assembly.mates.filter(mate => contained(mate.from) && contained(mate.to)).map(mate => ({ ...structuredClone(mate), id: idFactory(), from: remap(mate.from), to: remap(mate.to) })));
  if (groups.length) model.assembly.groups = [...(model.assembly.groups ?? []), ...groups.map(group => ({ id: groupIds.get(group.id)!, name: `${group.name} 副本`, memberIds: group.memberIds.map(id => ids.get(id)!), ...(group.parentId && groupIds.has(group.parentId) ? { parentId: groupIds.get(group.parentId)! } : {}) }))];
  model.bindings.push(...source.bindings.filter(binding => ids.has(binding.memberId) && target.parameters.some(parameter => parameter.id === binding.parameterId)).map(binding => ({ ...structuredClone(binding), id: idFactory(), memberId: ids.get(binding.memberId)! })));
  model.ports.push(...source.ports.filter(port => contained(port.source)).map(port => ({ ...structuredClone(port), id: idFactory(), source: remap(port.source) })));
  model.patterns.push(...source.patterns.filter(pattern => pattern.memberIds.some(id => ids.has(id)) && [pattern.countParameterId, pattern.spacingParameterId].every(id => target.parameters.some(parameter => parameter.id === id))).map(pattern => ({ ...structuredClone(pattern), id: idFactory(), memberIds: pattern.memberIds.filter(id => ids.has(id)).map(id => ids.get(id)!) })));
  model.slots.push(...source.slots.filter(slot => contained(slot.baseInterface) && (!slot.memberId || ids.has(slot.memberId))).map(slot => ({ ...structuredClone(slot), id: idFactory(), baseInterface: remap(slot.baseInterface!), memberId: slot.memberId ? ids.get(slot.memberId)! : null })));
  return { model, memberIds: members.map(member => ids.get(member.id)!) };
}

const pose = (member: ComponentMember) => new Matrix4().compose(new ThreeVector3(...member.position), new Quaternion().setFromEuler(new Euler(...member.rotation)), new ThreeVector3(1, 1, 1));
const transformedMember = (member: ComponentMember, matrix: Matrix4, id: string): ComponentMember => {
  const position = new ThreeVector3(), quaternion = new Quaternion(), scale = new ThreeVector3();
  matrix.clone().multiply(pose(member)).decompose(position, quaternion, scale);
  const rotation = new Euler().setFromQuaternion(quaternion);
  const { sourceMemberId: _, patternInstance: __, patternId: ___, ...plain } = member as ComponentMember & { sourceMemberId?: string; patternInstance?: boolean; patternId?: string };
  return { ...structuredClone(plain), id, position: position.toArray() as Vector3, rotation: [rotation.x, rotation.y, rotation.z] };
};

/** Materialize publication children once so a nested leaf can be edited independently. */
export async function materializeComponentModel(
  document: ComponentDocument,
  componentId: string,
  loadDocument: (publicationId: string, reference: PartTemplateReference) => Promise<ComponentDocument>,
  options: { keepGroup?: boolean; childPath?: string[]; idFactory?: () => string } = {},
): Promise<{ model: ModelState; memberIds: string[]; selectedMemberId?: string; groupId?: string }> {
  const source = document.assembly.members.find(member => member.id === componentId);
  const model = captureModel(document);
  if (!source || source.template.templateKind !== "component") return { model, memberIds: [] };
  const idFactory = options.idFactory ?? newId;
  const added: ComponentMember[] = [], groups: ComponentGroup[] = [], internalMates: Assembly["mates"] = [];
  const pathMap = new Map<string, string>();
  const cache = new Map<string, Promise<ComponentDocument>>();
  const getPublication = (reference: PartTemplateReference) => {
    const key = JSON.stringify(reference);
    if (!cache.has(key)) cache.set(key, loadDocument(reference.publicationId, reference));
    return cache.get(key)!;
  };
  const expand = async (member: ComponentMember, matrix: Matrix4, path: string[], groupId: string, parentId: string | undefined, ancestors: string[]): Promise<Map<string, InterfaceSource>> => {
    if (ancestors.includes(member.template.publicationId) || ancestors.length > 20) throw new Error("组件存在循环引用，无法展开。");
    const publication = member.instanceDocument ?? await getPublication(member.template);
    const children = resolveComponentComposition(publication, member.parameterValues);
    const before = added.length;
    const endpoints = new Map<string, InterfaceSource>();
    const childIds = new Map<string, string>();
    const nestedGroupIds = new Map<string, string>();
    const localGroupIds = new Map((publication.assembly.groups ?? []).map(group => [group.id, idFactory()]));
    for (const child of children) {
      const id = idFactory(), childPath = [...path, child.id];
      childIds.set(child.id, id);
      const localGroup = groupForMember(publication.assembly, child.sourceMemberId, false);
      const childParent = localGroup ? localGroupIds.get(localGroup.id) : groupId;
      if (child.template.templateKind === "component") {
        nestedGroupIds.set(child.id, id);
        const references = await expand(child, matrix.clone().multiply(pose(child)), childPath, id, childParent, [...ancestors, member.template.publicationId]);
        for (const [key, reference] of references) endpoints.set(`${child.id}:${key}`, reference);
      } else {
        added.push(transformedMember(child, matrix, id));
        pathMap.set(JSON.stringify(childPath), id);
        for (const item of child.interfaces) endpoints.set(`${child.id}:${item.id}`, { memberId: id, interfaceId: item.id });
      }
    }
    for (const original of publication.assembly.groups ?? []) {
      const memberIds = children.filter(child => original.memberIds.includes(child.sourceMemberId)).flatMap(child => {
        const nested = groups.find(group => group.id === nestedGroupIds.get(child.id));
        return nested ? nested.memberIds : childIds.has(child.id) ? [childIds.get(child.id)!] : [];
      });
      if (memberIds.length) groups.push({ id: localGroupIds.get(original.id)!, name: original.name, memberIds, parentId: original.parentId ? localGroupIds.get(original.parentId) : groupId });
    }
    for (const mate of publication.assembly.mates) {
      const from = endpoints.get(`${mate.from.memberId}:${mate.from.interfaceId}`), to = endpoints.get(`${mate.to.memberId}:${mate.to.interfaceId}`);
      if (from && to) internalMates.push({ ...structuredClone(mate), id: idFactory(), from, to });
    }
    const memberIds = added.slice(before).map(child => child.id);
    if (memberIds.length) groups.push({ id: groupId, name: member.name, memberIds, ...(parentId ? { parentId } : {}) });
    return new Map(publication.ports.flatMap(port => {
      const reference = endpoints.get(`${port.source.memberId}:${port.source.interfaceId}`);
      return reference ? [[port.id, reference] as const] : [];
    }));
  };
  const originalParent = groupForMember(document.assembly, componentId, false)?.id;
  const ports = await expand(source, pose(source), [], componentId, originalParent, []);
  if (!added.length) throw new Error("组件中没有可展开的零部件。");
  const remapEndpoint = (reference: InterfaceSource) => reference.memberId === componentId ? ports.get(reference.interfaceId) : reference;
  model.assembly.members = [...model.assembly.members.filter(member => member.id !== componentId), ...added];
  model.assembly.mates = [...model.assembly.mates.flatMap(mate => {
    const from = remapEndpoint(mate.from), to = remapEndpoint(mate.to);
    return from && to ? [{ ...mate, from, to }] : [];
  }), ...internalMates];
  model.assembly.groups = [...(model.assembly.groups ?? []).map(group => ({ ...group, memberIds: group.memberIds.flatMap(id => id === componentId ? added.map(child => child.id) : [id]) })), ...groups];
  model.ports = model.ports.flatMap(port => { const reference = remapEndpoint(port.source); return reference ? [{ ...port, source: reference }] : []; });
  model.bindings = model.bindings.filter(binding => binding.memberId !== componentId);
  model.patterns = model.patterns.map(pattern => ({ ...pattern, memberIds: pattern.memberIds.flatMap(id => id === componentId ? added.map(child => child.id) : [id]) }));
  model.slots = model.slots.map(slot => {
    const baseInterface = slot.baseInterface ? remapEndpoint(slot.baseInterface) ?? null : null;
    const assignment = slot.memberId && slot.interfaceId ? remapEndpoint({ memberId: slot.memberId, interfaceId: slot.interfaceId }) : null;
    return { ...slot, baseInterface, memberId: baseInterface ? assignment?.memberId ?? null : null, interfaceId: baseInterface ? assignment?.interfaceId ?? null : null };
  });
  if (options.keepGroup === false) model.assembly = ungroupSelection(model.assembly, [componentId]);
  return { model, memberIds: added.map(member => member.id), selectedMemberId: options.childPath ? pathMap.get(JSON.stringify(options.childPath)) : undefined, ...(options.keepGroup === false ? {} : { groupId: componentId }) };
}
