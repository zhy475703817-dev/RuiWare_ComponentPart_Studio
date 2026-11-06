import { getInterface, hasInterface, samePartInterface } from "./component.ts";
import type { ComponentDocument } from "./component.ts";
import { effectiveParameterBounds } from "./parameterMetrics.ts";
import { assessInterfaceCompatibility } from "./interfaceCompatibility.ts";

export type ValidationCheck = { id: string; label: string; status: "pass" | "fail" | "pending"; message: string };

export function validateComponent(document: ComponentDocument): ValidationCheck[] {
  const { members, mates } = document.assembly;
  const memberIds = new Set(members.map(member => member.id));
  const parameterIds = new Set(document.parameters.map(parameter => parameter.id));
  const occupiedInterfaces = new Set<string>();
  const slotsValid = document.slots.every(slot => {
    if (!slot.id.trim() || !slot.name.trim() || !slot.baseInterface || !hasInterface(document, slot.baseInterface)) return false;
    if (slot.memberId === null && slot.interfaceId === null) return !slot.required;
    if (!slot.memberId || !slot.interfaceId) return false;
    const assignment = { memberId: slot.memberId, interfaceId: slot.interfaceId };
    if (!hasInterface(document, assignment) || slot.memberId === slot.baseInterface.memberId) return false;
    const key = `${slot.memberId}:${slot.interfaceId}`;
    if (occupiedInterfaces.has(key)) return false;
    occupiedInterfaces.add(key);
    const baseMember = members.find(member => member.id === slot.baseInterface!.memberId);
    const targetMember = members.find(member => member.id === assignment.memberId);
    return assessInterfaceCompatibility(getInterface(document, slot.baseInterface), getInterface(document, assignment), undefined, baseMember, targetMember).status === "compatible";
  }) && new Set(document.slots.map(slot => slot.id)).size === document.slots.length;
  const check = (id: string, label: string, passed: boolean, failure: string): ValidationCheck => ({ id, label, status: passed ? "pass" : "fail", message: passed ? "基础数据检查通过" : failure });
  return [
    check("definition", "组件名称", document.definition.name.trim().length > 0, "请填写组件名称"),
    check("members", "装配成员", members.length > 0 && members.every(member => member.id.trim()) && memberIds.size === members.length, "请加入零部件，成员标识需唯一"),
    check("references", "固定版本引用", members.length > 0 && members.every(member => member.template.publicationId.trim() && member.template.templateId.trim() && Number.isSafeInteger(member.template.version) && member.template.version > 0 && /^[a-f0-9]{64}$/i.test(member.template.sha256)), "成员需引用有效发布版本及模板包校验值"),
    check("mates", "内部连接数据", new Set(mates.map(mate => mate.id)).size === mates.length && mates.every(mate => {
      if (!hasInterface(document, mate.from) || !hasInterface(document, mate.to) || mate.from.memberId === mate.to.memberId || !Number.isFinite(mate.offset)) return false;
      const source = getInterface(document, mate.from), target = getInterface(document, mate.to);
      const sourceMember = members.find(member => member.id === mate.from.memberId), targetMember = members.find(member => member.id === mate.to.memberId);
      if (assessInterfaceCompatibility(source, target, mate.relation, sourceMember, targetMember).status !== "compatible") return false;
      const endpoints = [[mate.from.memberId, mate.from.interfaceId, source?.mating?.capacity], [mate.to.memberId, mate.to.interfaceId, target?.mating?.capacity]] as const;
      return endpoints.every(([memberId, interfaceId, capacity]) => capacity !== undefined && mates.filter(item =>
        item.from.memberId === memberId && item.from.interfaceId === interfaceId || item.to.memberId === memberId && item.to.interfaceId === interfaceId).length <= capacity);
    }), "连接两端必须有明确兼容契约、允许所选关系并遵守接口容量；历史未评估连接需复核"),
    check("ports", "对外接口", document.ports.length > 0 && document.ports.every(port => port.name.trim().length > 0 && hasInterface(document, port.source) && (port.interface === undefined || samePartInterface(port.interface, getInterface(document, port.source)))), "请继承有效的零部件接口，并检查接口信息是否仍与来源一致"),
    check("parameters", "参数范围", document.parameters.length > 0 && parameterIds.size === document.parameters.length && document.parameters.every(parameter => parameter.value !== null && Number.isFinite(parameter.value) && Number.isFinite(parameter.min) && Number.isFinite(parameter.max) && parameter.min <= parameter.max && parameter.value >= parameter.min && parameter.value <= parameter.max), "参数需为范围内的有效数值"),
    check("bindings", "参数绑定来源", document.bindings.every(binding => {
      const parameter = document.parameters.find(item => item.id === binding.parameterId);
      const member = members.find(item => item.id === binding.memberId);
      const definition = member?.parameterDefinitions?.find(item => item.id === binding.memberParameterId);
      return Boolean(parameter && member && definition && Object.hasOwn(member.parameterValues, binding.memberParameterId)
        && ["number", "integer"].includes(definition.valueType)
          && definition.unit === parameter.unit
        && (definition.minimum === null || definition.minimum <= parameter.max)
        && (definition.maximum === null || definition.maximum >= parameter.min)
        && (definition.valueType !== "integer" || Math.ceil(Math.max(parameter.min, definition.minimum ?? -Infinity)) <= Math.floor(Math.min(parameter.max, definition.maximum ?? Infinity))));
    }) && document.parameters.every(parameter => {
      const bounds = effectiveParameterBounds(parameter, document.bindings, members);
      return bounds.min <= bounds.max;
    }), "参数绑定引用了不存在的成员参数，或与成员参数范围没有交集"),
    check("patterns", "组件构成规则", document.patterns.every(pattern => {
      const count = document.parameters.find(parameter => parameter.id === pattern.countParameterId);
      const spacing = document.parameters.find(parameter => parameter.id === pattern.spacingParameterId);
      return pattern.memberIds.length > 0 && new Set(pattern.memberIds).size === pattern.memberIds.length
        && pattern.memberIds.every(id => memberIds.has(id))
        && count !== undefined && ["个", "件"].includes(count.unit) && count.value !== null && Number.isInteger(count.value)
        && count.min >= 1 && count.min <= count.value && count.value <= count.max
        && spacing !== undefined && spacing.unit === "mm" && spacing.value !== null && spacing.value >= 0
        && spacing.min >= 0 && spacing.min <= spacing.value && spacing.value <= spacing.max;
    }) && new Set(document.patterns.flatMap(pattern => pattern.memberIds)).size === document.patterns.reduce((total, pattern) => total + pattern.memberIds.length, 0), "重复构成规则引用无效成员、参数或重叠成员"),
    check("slots", "槽位引用", slotsValid, "槽位缺少有效基准接口、占用接口不兼容，或必填槽位尚未分配"),
    { id: "assembly.solve", label: "装配求解", status: "pending", message: "尚未接入装配求解，连接数据不会自动改变成员位置" },
    { id: "rules.execution", label: "工程规则执行", status: "pending", message: "当前仅保存规则说明，尚未接入规则执行" },
  ];
}
