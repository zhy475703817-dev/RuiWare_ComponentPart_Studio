import type { ComponentDocument, ComponentMember, ComponentParameter } from "./component.ts";
import { effectiveParameterBounds } from "./parameterMetrics.ts";

export type ResolvedComponentMember = ComponentMember & {
  sourceMemberId: string;
  patternInstance: boolean;
  patternId?: string;
};

type CompositionDocument = Pick<ComponentDocument, "assembly" | "parameters" | "bindings" | "patterns">;

function resolveParameters(document: CompositionDocument, overrides: Record<string, unknown>): ComponentParameter[] {
  return document.parameters.map(parameter => {
    const override = overrides[parameter.id];
    if (typeof override !== "number" || !Number.isFinite(override)) return parameter;
    const bounds = effectiveParameterBounds(parameter, document.bindings, document.assembly.members);
    if (bounds.min > bounds.max) return parameter;
    const integerBound = document.patterns.some(pattern => pattern.countParameterId === parameter.id) || document.bindings.some(binding => binding.parameterId === parameter.id && document.assembly.members.some(member => member.id === binding.memberId && member.parameterDefinitions?.some(definition => definition.id === binding.memberParameterId && definition.unit === parameter.unit && definition.valueType === "integer")));
    const min = integerBound ? Math.ceil(bounds.min) : bounds.min;
    const max = integerBound ? Math.floor(bounds.max) : bounds.max;
    if (min > max) return parameter;
    const value = Math.min(max, Math.max(min, integerBound ? Math.round(override) : override));
    return { ...parameter, value };
  });
}

/** Resolve parameter bindings and generic repeated member groups for a component preview. */
export function resolveComponentComposition(document: CompositionDocument, overrides: Record<string, unknown> = {}): ResolvedComponentMember[] {
  const parameters = resolveParameters(document, overrides);
  const members = document.assembly.members.map(member => ({ ...structuredClone(member), parameterValues: { ...member.parameterValues } }));

  for (const binding of document.bindings) {
    const parameter = parameters.find(item => item.id === binding.parameterId);
    const member = members.find(item => item.id === binding.memberId);
    const definition = member?.parameterDefinitions?.find(item => item.id === binding.memberParameterId);
    if (!parameter || parameter.value === null || !member || !definition || definition.unit !== parameter.unit || !["number", "integer"].includes(definition.valueType)) continue;
    const min = Math.max(parameter.min, definition.minimum ?? -Infinity);
    const max = Math.min(parameter.max, definition.maximum ?? Infinity);
    if (min > max) continue;
    const value = definition.valueType === "integer" ? Math.round(parameter.value) : parameter.value;
    member.parameterValues[binding.memberParameterId] = Math.min(max, Math.max(min, value));
  }

  const patternedMemberIds = new Set(document.patterns.flatMap(pattern => pattern.memberIds));
  const result: ResolvedComponentMember[] = members
    .filter(member => !patternedMemberIds.has(member.id))
    .map(member => ({ ...member, sourceMemberId: member.id, patternInstance: false }));

  for (const pattern of document.patterns) {
    const count = parameters.find(parameter => parameter.id === pattern.countParameterId)?.value;
    const spacing = parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value;
    const repetitions = Math.max(1, Math.round(count ?? 1));
    const offset = typeof spacing === "number" && Number.isFinite(spacing) ? spacing : 0;
    for (const member of members.filter(item => pattern.memberIds.includes(item.id))) {
      for (let index = 0; index < repetitions; index += 1) {
        const position: ComponentMember["position"] = [...member.position];
        if (pattern.axis === "x") position[0] += offset * index;
        if (pattern.axis === "y") position[1] += offset * index;
        if (pattern.axis === "z") position[2] += offset * index;
        result.push({
          ...member,
          id: index === 0 ? member.id : `${member.id}#${pattern.id}-${index}`,
          position,
          sourceMemberId: member.id,
          patternInstance: index > 0,
          patternId: pattern.id,
        });
      }
    }
  }
  return result;
}
