import { captureModel } from "./assemblyGroups.ts";
import type { ModelState } from "./assemblyGroups.ts";
import type { ComponentDocument, ComponentInterfaceType, ComponentParameter } from "./component.ts";

type ParameterChange = { before: ComponentParameter; after: ComponentParameter };
type AncillaryState = Partial<Pick<ComponentDocument, "parameters" | "interfaceTypes">>;

export type ModelHistoryEntry = {
  model: ModelState;
  afterModel?: ModelState;
  parameterChanges?: ParameterChange[];
  introducedTypes?: ComponentInterfaceType[];
};

function sameParameter(a: ComponentParameter, b: ComponentParameter): boolean {
  return a.id === b.id && a.label === b.label && a.value === b.value &&
    a.unit === b.unit && a.min === b.min && a.max === b.max;
}

export function captureModelHistory(before: ComponentDocument, after?: ComponentDocument): ModelHistoryEntry {
  const model = captureModel(before);
  if (!after) return { model };
  const previousParameters = new Map(before.parameters.map(parameter => [parameter.id, parameter]));
  const parameterChanges = after.parameters.flatMap(parameter => {
    const previous = previousParameters.get(parameter.id);
    return previous && !sameParameter(previous, parameter) ? [{ before: previous, after: parameter }] : [];
  });
  const previousTypes = new Set((before.interfaceTypes ?? []).map(type => type.id));
  const introducedTypes = (after.interfaceTypes ?? []).filter(type => !previousTypes.has(type.id));
  return { model, afterModel: captureModel(after), ...structuredClone({ parameterChanges, introducedTypes }) };
}

function usedInterfaceTypes(model: ModelState): Set<string> {
  const used = new Set<string>();
  const collect = ({ assembly, ports }: Pick<ModelState, "assembly" | "ports">) => {
    const interfaces = assembly.members.flatMap(member => member.interfaces);
    for (const port of ports) {
      if (port.interface) interfaces.push(port.interface);
    }
    for (const item of interfaces) {
      if (item.connectionTypeId) used.add(item.connectionTypeId);
      for (const id of item.connectableTypeIds ?? []) used.add(id);
    }
    for (const member of assembly.members) {
      if (member.instanceDocument) collect(member.instanceDocument);
    }
  };
  collect(model);
  return used;
}

export function restoreModelHistory(entry: ModelHistoryEntry, current: ComponentDocument): ModelState & AncillaryState {
  const model: ModelState & AncillaryState = { ...entry.model };
  if (entry.parameterChanges?.length) {
    const changes = new Map(entry.parameterChanges.map(change => [change.after.id, change]));
    const parameters = current.parameters.map(parameter => {
      const change = changes.get(parameter.id);
      return change && sameParameter(parameter, change.after) ? change.before : parameter;
    });
    if (parameters.some((parameter, index) => parameter !== current.parameters[index])) model.parameters = parameters;
  }
  if (entry.introducedTypes?.length) {
    const labels = new Map(entry.introducedTypes.map(type => [type.id, type.label]));
    const used = usedInterfaceTypes(entry.model);
    const currentTypes = current.interfaceTypes ?? [];
    const interfaceTypes = currentTypes.filter(type => labels.get(type.id) !== type.label || used.has(type.id));
    if (interfaceTypes.length !== currentTypes.length) model.interfaceTypes = interfaceTypes;
  }
  return model;
}


export function canRestoreModelHistory(entry: ModelHistoryEntry, current: ComponentDocument): boolean {
  const key = (value: unknown) => JSON.stringify(value, (_name, item: unknown) =>
    item !== null && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
  return !entry.afterModel || key(entry.afterModel) === key(captureModel(current));
}
