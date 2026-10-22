import type { ComponentDocument } from "./component.ts";
import { validateComponent } from "./validation.ts";

export function serializeComponent(document: ComponentDocument): string {
  const { definition, assembly, parameters, bindings, ports, slots, rules } = document;
  return JSON.stringify({
    schemaVersion: document.schemaVersion, templateKind: document.templateKind,
    revision: document.revision, name: definition.name,
    unitSystem: document.unitSystem, coordinateSystem: document.coordinateSystem,
    stages: {
      definition: { ...definition },
      assembly: {
        members: assembly.members.map(member => ({
          memberId: member.id, partTemplateVersion: member.template.publicationId,
          partTemplateRef: { publicationId: member.template.publicationId, templateId: member.template.templateId, version: member.template.version, sha256: member.template.sha256 },
          name: member.name, code: member.code, role: member.role,
          position: member.position, rotation: member.rotation, parameterValues: member.parameterValues,
          bindings: { ...member.parameterValues, ...Object.fromEntries(bindings.filter(binding => binding.memberId === member.id).map(binding => [binding.memberParameterId, binding.parameterId])) },
          interfaces: member.interfaces,
        })),
        mates: assembly.mates.map(mate => ({
          id: mate.id, from: `${mate.from.memberId}.${mate.from.interfaceId}`, to: `${mate.to.memberId}.${mate.to.interfaceId}`,
          fromRef: mate.from, toRef: mate.to, relation: mate.relation, offset: mate.offset,
        })),
      },
      ports: ports.map(port => ({ id: port.id, name: port.name, sourceMemberId: port.source.memberId, sourceInterfaceId: port.source.interfaceId, source: port.source, tags: port.tags })),
      slots, parameters: Object.fromEntries(parameters.map(parameter => [parameter.id, parameter.value])),
      parameterDefinitions: parameters, bindings, rules,
      validation: validateComponent(document).map(check => ({ name: check.label, passed: check.status === "pass", ...check })),
    },
  }, null, 2);
}
