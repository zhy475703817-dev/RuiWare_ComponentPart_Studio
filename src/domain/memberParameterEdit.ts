import type { ComponentMember, ParameterBinding, ParameterValue } from "./component.ts";

export function memberParameterPatch(member: ComponentMember, bindings: ParameterBinding[], id: string, value: ParameterValue) {
  const definition = member.parameterDefinitions?.find(item => item.id === id);
  if (!definition || bindings.some(binding => binding.memberId === member.id && binding.memberParameterId === id)) return null;
  if (["number", "integer"].includes(definition.valueType)) {
    if (typeof value !== "number" || !Number.isFinite(value) || definition.valueType === "integer" && !Number.isInteger(value) ||
      definition.minimum != null && value < definition.minimum || definition.maximum != null && value > definition.maximum) return null;
  } else if (definition.valueType === "boolean" ? typeof value !== "boolean" : typeof value !== "string") return null;
  return { parameterValues: { ...member.parameterValues, [id]: value } };
}
