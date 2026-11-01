import { createComponentDocument, getInterface, hasInterface, samePartInterface } from "./component.ts";
import { inferEnvelopeDimensions } from "./parameterMetrics.ts";
import type { ComponentDefinition, ComponentDocument, ComponentMember, ComponentPattern, ComponentPort, Mate, ParameterBinding } from "./component.ts";

export type ComponentAction =
  | { type: "reset" }
  | { type: "definition.patch"; patch: Partial<ComponentDefinition> }
  | { type: "member.add"; member: ComponentMember }
  | { type: "member.patch"; id: string; patch: Partial<Pick<ComponentMember, "role" | "position" | "rotation" | "parameterValues">> }
  | { type: "member.remove"; id: string }
  | { type: "mate.add"; mate: Mate }
  | { type: "port.add"; port: ComponentPort }
  | { type: "parameter.value"; id: string; value: number | null }
  | { type: "parameter.add"; parameter: import("./component.ts").ComponentParameter }
  | { type: "parameter.remove"; id: string }
  | { type: "binding.set"; binding: ParameterBinding | null; id: string }
  | { type: "pattern.add"; pattern: ComponentPattern }
  | { type: "pattern.remove"; id: string }
  | { type: "rule.add"; rule: import("./component.ts").ComponentRule }
  | { type: "rule.remove"; id: string }
  | { type: "rule.expression"; id: string; expression: string };

export function componentReducer(document: ComponentDocument, action: ComponentAction): ComponentDocument {
  const next = (patch: Partial<ComponentDocument>): ComponentDocument => ({ ...document, ...patch, revision: document.revision + 1 });
  switch (action.type) {
    case "reset": return createComponentDocument();
    case "definition.patch":
      if (Object.entries(action.patch).every(([key, value]) => document.definition[key as keyof ComponentDefinition] === value)) return document;
      return next({ definition: { ...document.definition, ...action.patch } });
    case "member.add":
      if (document.assembly.members.some(member => member.id === action.member.id)) return document;
      {
        const member = structuredClone(action.member);
        const dimensionAliases: Record<string, string[]> = {
          length: ["length", "overallLength", "componentLength", "总长", "长度"],
          width: ["width", "overallWidth", "componentWidth", "总宽", "宽度"],
          height: ["height", "overallHeight", "componentHeight", "总高", "高度"],
        };
        const existingBindings = document.bindings;
        const autoBindings: ParameterBinding[] = [];
        const previousInferred = inferEnvelopeDimensions(document.parameters, document.assembly.members, document.patterns);
        let parameters = document.parameters.map(parameter => {
          const aliases = parameter.id.toLowerCase().includes("length") || parameter.label.includes("长") ? dimensionAliases.length
            : parameter.id.toLowerCase().includes("width") || parameter.label.includes("宽") ? dimensionAliases.width
              : parameter.id.toLowerCase().includes("height") || parameter.label.includes("高") ? dimensionAliases.height : [];
          const definition = member.parameterDefinitions?.find(item => aliases.some(alias => item.id.toLowerCase().includes(alias.toLowerCase()) || item.label.toLowerCase().includes(alias.toLowerCase())) && item.unit === parameter.unit && ["number", "integer"].includes(item.valueType));
          if (!definition) return parameter;
          if (!existingBindings.some(binding => binding.parameterId === parameter.id && binding.memberId === member.id && binding.memberParameterId === definition.id)) {
            autoBindings.push({ id: `${member.id}:${parameter.id}:${definition.id}`, parameterId: parameter.id, memberId: member.id, memberParameterId: definition.id });
          }
          if (document.assembly.members.length === 0 && parameter.value !== null && typeof definition.default === "number") {
            const value = Math.min(definition.maximum ?? parameter.max, Math.max(definition.minimum ?? parameter.min, definition.default));
            return { ...parameter, value: Math.min(parameter.max, Math.max(parameter.min, value)) };
          }
          return parameter;
        });
        const members = [...document.assembly.members, member];
        const inferred = inferEnvelopeDimensions(parameters, members, document.patterns);
        const defaults: Record<string, number> = { componentLength: 2400, componentWidth: 600, componentHeight: 1800 };
        parameters = parameters.map(parameter => {
          const axis = parameter.id.toLowerCase().includes("length") || parameter.label.includes("长") ? "length"
            : parameter.id.toLowerCase().includes("width") || parameter.label.includes("宽") ? "width"
              : parameter.id.toLowerCase().includes("height") || parameter.label.includes("高") ? "height" : null;
          const value = axis ? inferred[axis] : undefined;
          const previousValue = axis ? previousInferred[axis] : undefined;
          const untouched = defaults[parameter.id] === parameter.value || parameter.value !== null && previousValue !== undefined && Math.abs(previousValue - parameter.value) < 1e-8;
          if (value === undefined || !untouched) return parameter;
          return { ...parameter, value: Math.min(parameter.max, Math.max(parameter.min, value)) };
        });
        member.parameterValues = { ...member.parameterValues };
        for (const binding of autoBindings) {
          const parameter = parameters.find(item => item.id === binding.parameterId);
          const definition = member.parameterDefinitions?.find(item => item.id === binding.memberParameterId);
          if (!parameter || parameter.value === null || !definition) continue;
          const minimum = Math.max(parameter.min, definition.minimum ?? -Infinity);
          const maximum = Math.min(parameter.max, definition.maximum ?? Infinity);
          if (minimum <= maximum) member.parameterValues[definition.id] = Math.min(maximum, Math.max(minimum, definition.valueType === "integer" ? Math.round(parameter.value) : parameter.value));
        }
        return next({ assembly: { ...document.assembly, members }, bindings: [...existingBindings, ...autoBindings], parameters });
      }
    case "member.patch":
      if (!document.assembly.members.some(member => member.id === action.id)) return document;
      return next({ assembly: { ...document.assembly, members: document.assembly.members.map(member => member.id === action.id ? { ...member, ...structuredClone(action.patch) } : member) } });
    case "member.remove":
      if (!document.assembly.members.some(member => member.id === action.id)) return document;
      return next({
        assembly: {
          members: document.assembly.members.filter(member => member.id !== action.id),
          mates: document.assembly.mates.filter(mate => mate.from.memberId !== action.id && mate.to.memberId !== action.id),
        },
        ports: document.ports.filter(port => port.source.memberId !== action.id),
        bindings: document.bindings.filter(binding => binding.memberId !== action.id),
        patterns: document.patterns.map(pattern => ({ ...pattern, memberIds: pattern.memberIds.filter(id => id !== action.id) })).filter(pattern => pattern.memberIds.length > 0),
        slots: document.slots.map(slot => slot.memberId === action.id ? { ...slot, memberId: null } : slot),
      });
    case "mate.add": {
      const mate = action.mate;
      if (!hasInterface(document, mate.from) || !hasInterface(document, mate.to) || mate.from.memberId === mate.to.memberId ||
        document.assembly.mates.some(item => item.id === mate.id || (
          item.from.memberId === mate.from.memberId && item.from.interfaceId === mate.from.interfaceId &&
          item.to.memberId === mate.to.memberId && item.to.interfaceId === mate.to.interfaceId
        ))) return document;
      return next({ assembly: { ...document.assembly, mates: [...document.assembly.mates, structuredClone(mate)] } });
    }
    case "port.add": {
      if (!hasInterface(document, action.port.source) || document.ports.some(port => port.id === action.port.id ||
        (port.source.memberId === action.port.source.memberId && port.source.interfaceId === action.port.source.interfaceId))) return document;
      const sourceInterface = getInterface(document, action.port.source);
      if (!sourceInterface || (action.port.interface && !samePartInterface(action.port.interface, sourceInterface))) return document;
      return next({ ports: [...document.ports, { ...structuredClone(action.port), interface: structuredClone(sourceInterface) }] });
    }
    case "parameter.value":
      if (!document.parameters.some(parameter => parameter.id === action.id && parameter.value !== action.value)) return document;
      const componentParameter = document.parameters.find(parameter => parameter.id === action.id)!;
      const boundDefinitions = document.bindings.filter(binding => binding.parameterId === action.id).flatMap(binding => {
        const member = document.assembly.members.find(item => item.id === binding.memberId);
        const definition = member?.parameterDefinitions?.find(item => item.id === binding.memberParameterId && item.unit === componentParameter.unit && ["number", "integer"].includes(item.valueType));
        return definition ? [definition] : [];
      });
      const minimum = Math.max(componentParameter.min, ...boundDefinitions.map(item => item.minimum ?? -Infinity));
      const maximum = Math.min(componentParameter.max, ...boundDefinitions.map(item => item.maximum ?? Infinity));
      const integerBound = boundDefinitions.some(item => item.valueType === "integer");
      const lowerBound = integerBound ? Math.ceil(minimum) : minimum;
      const upperBound = integerBound ? Math.floor(maximum) : maximum;
      const value = action.value === null ? null : Math.min(upperBound, Math.max(lowerBound, integerBound ? Math.round(action.value) : action.value));
      const parameters = document.parameters.map(parameter => parameter.id === action.id ? { ...parameter, value } : parameter);
      const updatedMembers = document.assembly.members.map(member => {
        const values = { ...member.parameterValues };
        for (const binding of document.bindings.filter(item => item.parameterId === action.id && item.memberId === member.id)) {
          if (value === null) {
            delete values[binding.memberParameterId];
            continue;
          }
          const definition = member.parameterDefinitions?.find(item => item.id === binding.memberParameterId && item.unit === document.parameters.find(item => item.id === action.id)?.unit && ["number", "integer"].includes(item.valueType));
          const targetMin = Math.max(componentParameter.min, definition?.minimum ?? -Infinity);
          const targetMax = Math.min(componentParameter.max, definition?.maximum ?? Infinity);
          values[binding.memberParameterId] = Math.min(targetMax, Math.max(targetMin, value));
        }
        return Object.keys(values).every(key => values[key] === member.parameterValues[key]) && Object.keys(member.parameterValues).every(key => key in values)
          ? member : { ...member, parameterValues: values };
      });
      return next({ parameters, assembly: { ...document.assembly, members: updatedMembers } });
    case "parameter.add":
      if (!action.parameter.id.trim() || document.parameters.some(parameter => parameter.id === action.parameter.id) || !Number.isFinite(action.parameter.min) || !Number.isFinite(action.parameter.max) || !Number.isFinite(action.parameter.value ?? NaN) || action.parameter.min > action.parameter.max || action.parameter.value! < action.parameter.min || action.parameter.value! > action.parameter.max) return document;
      return next({ parameters: [...document.parameters, structuredClone(action.parameter)] });
    case "parameter.remove":
      if (!document.parameters.some(parameter => parameter.id === action.id)) return document;
      return next({ parameters: document.parameters.filter(parameter => parameter.id !== action.id), bindings: document.bindings.filter(binding => binding.parameterId !== action.id), patterns: document.patterns.filter(pattern => pattern.countParameterId !== action.id && pattern.spacingParameterId !== action.id) });
    case "pattern.add": {
      const pattern = action.pattern;
      const count = document.parameters.find(parameter => parameter.id === pattern.countParameterId);
      const spacing = document.parameters.find(parameter => parameter.id === pattern.spacingParameterId);
      const memberIds = [...new Set(pattern.memberIds)];
      const usedMemberIds = new Set(document.patterns.flatMap(item => item.memberIds));
      if (!pattern.id.trim() || !pattern.label.trim() || document.patterns.some(item => item.id === pattern.id) || memberIds.length === 0 ||
        memberIds.some(id => !document.assembly.members.some(member => member.id === id) || usedMemberIds.has(id)) ||
        !count || !["个", "件"].includes(count.unit) || count.value === null || !Number.isInteger(count.value) || count.min < 1 || count.value < count.min || count.value > count.max ||
        !spacing || spacing.unit !== "mm" || spacing.value === null || spacing.value < 0 || spacing.min < 0 || spacing.value < spacing.min || spacing.value > spacing.max) return document;
      return next({ patterns: [...document.patterns, { ...structuredClone(pattern), memberIds }] });
    }
    case "pattern.remove":
      if (!document.patterns.some(pattern => pattern.id === action.id)) return document;
      return next({ patterns: document.patterns.filter(pattern => pattern.id !== action.id) });
    case "rule.add":
      if (!action.rule.id.trim() || !action.rule.expression.trim() || document.rules.some(rule => rule.id === action.rule.id)) return document;
      return next({ rules: [...document.rules, { ...structuredClone(action.rule), kind: action.rule.kind || "constraint" }] });
    case "rule.remove":
      if (!document.rules.some(rule => rule.id === action.id)) return document;
      return next({ rules: document.rules.filter(rule => rule.id !== action.id) });
    case "binding.set": {
      const previous = document.bindings.find(binding => binding.id === action.id);
      if (action.binding === null) {
        if (!previous) return document;
        return next({ bindings: document.bindings.filter(binding => binding.id !== action.id) });
      }
      if (action.binding.id !== action.id || !document.parameters.some(parameter => parameter.id === action.binding?.parameterId)) return document;
      const member = document.assembly.members.find(item => item.id === action.binding?.memberId);
      const definition = member?.parameterDefinitions?.find(item => item.id === action.binding?.memberParameterId);
      if (!member || !definition || definition.unit !== document.parameters.find(parameter => parameter.id === action.binding?.parameterId)?.unit || !["number", "integer"].includes(definition.valueType)) return document;
      if (document.bindings.some(binding => binding.id !== action.id && binding.parameterId === action.binding?.parameterId && binding.memberId === action.binding?.memberId && binding.memberParameterId === action.binding?.memberParameterId)) return document;
      const bindings = [...document.bindings.filter(binding => binding.id !== action.id), structuredClone(action.binding)];
      const parameter = document.parameters.find(item => item.id === action.binding?.parameterId)!;
      const definitions = bindings.filter(binding => binding.parameterId === parameter.id).flatMap(binding => {
        const target = document.assembly.members.find(item => item.id === binding.memberId);
        const targetDefinition = target?.parameterDefinitions?.find(item => item.id === binding.memberParameterId && item.unit === parameter.unit && ["number", "integer"].includes(item.valueType));
        return targetDefinition ? [targetDefinition] : [];
      });
      const min = Math.max(parameter.min, ...definitions.map(item => item.minimum ?? -Infinity));
      const max = Math.min(parameter.max, ...definitions.map(item => item.maximum ?? Infinity));
      const integerBound = definitions.some(item => item.valueType === "integer");
      const lowerBound = integerBound ? Math.ceil(min) : min;
      const upperBound = integerBound ? Math.floor(max) : max;
      if (lowerBound > upperBound) return document;
      const value = parameter.value === null ? null : Math.min(upperBound, Math.max(lowerBound, integerBound ? Math.round(parameter.value) : parameter.value));
      const parameters = document.parameters.map(item => item.id === parameter.id ? { ...item, value } : item);
      const assembly = { ...document.assembly, members: document.assembly.members.map(item => {
        const targetBindings = bindings.filter(binding => binding.parameterId === parameter.id && binding.memberId === item.id);
        if (!targetBindings.length || value === null) return item;
        const values = { ...item.parameterValues };
        targetBindings.forEach(binding => { values[binding.memberParameterId] = value; });
        return { ...item, parameterValues: values };
      }) };
      return next({ bindings, parameters, assembly });
    }
    case "rule.expression":
      if (!document.rules.some(rule => rule.id === action.id && rule.expression !== action.expression)) return document;
      return next({ rules: document.rules.map(rule => rule.id === action.id ? { ...rule, expression: action.expression } : rule) });
  }
}
