import type { ComponentMember, ComponentPort, GeometryDesign, GeometryEndpoint, Mate, PartInterface } from "./component.ts";

export type HardwareEndpoint = { key: string; path: string[]; interfaceId: string; label: string; item: PartInterface; depth: number };
export type HardwareNode = { id: string; endpoints: HardwareEndpoint[]; declarations: HardwareEndpoint[]; connected: boolean };
export type HardwareNodeProblem = { id: string; label: string; code: string; connected: boolean };

/** Same physical node identity for BOM totals and selection completeness. */
export function collectHardwareNodes(members: ComponentMember[], mates: Mate[], geometry?: GeometryDesign | null,
  ports: ComponentPort[] = [], includeRequired = false) {
  const parents = new Map<string, string>();
  const definitions = new Map<string, Map<PartInterface, HardwareEndpoint>>();
  const connected = new Set<string>();
  const invalid = new Map<string, HardwareNodeProblem>();
  const root = (key: string): string => {
    const parent = parents.get(key)!;
    if (parent === key) return key;
    const result = root(parent); parents.set(key, result); return result;
  };
  const problem = (id: string, label: string, code = "INVALID_ENDPOINT", isConnected = true) => invalid.set(id, { id, label, code, connected: isConnected });
  const resolve = (scope: ComponentMember[], endpoint: GeometryEndpoint, prefix: string[], depth = 0): HardwareEndpoint | undefined => {
    if (depth > 40 || !endpoint.path.length || endpoint.interfaceId.startsWith("@")) return;
    const [id, ...rest] = endpoint.path, member = scope.find(value => value.id === id);
    if (!member) return;
    const path = [...prefix, id];
    if (rest.length) return member.instanceDocument
      ? resolve(member.instanceDocument.assembly.members, { ...endpoint, path: rest }, path, depth + 1) : undefined;
    const item = member.interfaces.find(value => value.id === endpoint.interfaceId);
    if (!item) return;
    const nested = member.instanceDocument, port = nested?.ports.find(value => value.id === endpoint.interfaceId);
    const inner = nested && port ? resolve(nested.assembly.members,
      { path: [port.source.memberId], interfaceId: port.source.interfaceId }, path, depth + 1) : undefined;
    // A broken exported port is not a new independent fastening node.
    if (nested && port && !inner) return;
    return { key: inner?.key ?? JSON.stringify([path, endpoint.interfaceId]), path, interfaceId: item.id,
      label: `${path.join(" / ")} · ${member.name} · ${item.name}`, item, depth: path.length };
  };
  const ensure = (endpoint: HardwareEndpoint) => {
    if (!parents.has(endpoint.key)) parents.set(endpoint.key, endpoint.key);
    if (!definitions.has(endpoint.key)) definitions.set(endpoint.key, new Map());
    definitions.get(endpoint.key)!.set(endpoint.item, endpoint);
  };
  const visit = (scope: ComponentMember[], connections: Mate[], design: GeometryDesign | null | undefined,
    exposed: ComponentPort[], prefix: string[], ancestors: Set<ComponentMember[]>) => {
    if (prefix.length > 40 || ancestors.has(scope)) { problem(JSON.stringify(prefix), prefix.join(" / "), "NESTING_INVALID"); return; }
    const nextAncestors = new Set(ancestors); nextAncestors.add(scope);
    const connect = (a: GeometryEndpoint, b: GeometryEndpoint) => {
      if (a.interfaceId.startsWith("@") || b.interfaceId.startsWith("@")) return;
      const left = resolve(scope, a, prefix), right = resolve(scope, b, prefix);
      if (!left || !right) {
        problem(JSON.stringify([prefix, a, b]), `${[...prefix, ...a.path].join(" / ")}:${a.interfaceId} ↔ ${[...prefix, ...b.path].join(" / ")}:${b.interfaceId}`); return;
      }
      if (left.key === right.key) return;
      ensure(left); ensure(right); connected.add(left.key); connected.add(right.key);
      parents.set(root(right.key), root(left.key));
    };
    for (const mate of connections) connect({ path: [mate.from.memberId], interfaceId: mate.from.interfaceId }, { path: [mate.to.memberId], interfaceId: mate.to.interfaceId });
    for (const relation of design?.relations ?? []) connect(relation.a, relation.b);
    if (includeRequired) {
      const required = [...exposed.map(port => ({ path: [port.source.memberId], interfaceId: port.source.interfaceId })),
        ...scope.flatMap(member => member.interfaces.filter(item => item.required).map(item => ({ path: [member.id], interfaceId: item.id })))];
      for (const source of required) {
        if (source.interfaceId.startsWith("@")) continue;
        const endpoint = resolve(scope, source, prefix);
        if (endpoint) ensure(endpoint);
        else problem(JSON.stringify([prefix, source]), `${[...prefix, ...source.path].join(" / ")}:${source.interfaceId}`, "INVALID_ENDPOINT", false);
      }
    }
    for (const member of scope) if (member.instanceDocument) {
      const nested = member.instanceDocument;
      visit(nested.assembly.members, nested.assembly.mates, nested.geometryDesign, nested.ports, [...prefix, member.id], nextAncestors);
    }
  };
  visit(members, mates, geometry, ports, [], new Set());
  const groups = new Map<string, HardwareNode>();
  for (const [key, items] of definitions) {
    const id = root(key);
    if (!groups.has(id)) groups.set(id, { id, endpoints: [], declarations: [], connected: false });
    const node = groups.get(id)!, all = [...items.values()];
    const configured = all.filter(endpoint => endpoint.item.hardware && endpoint.item.hardware.mode !== "auto");
    const depth = Math.min(...configured.map(endpoint => endpoint.depth));
    node.endpoints.push(...all);
    node.declarations.push(...(configured.length ? configured.filter(endpoint => endpoint.depth === depth) : all));
    node.connected ||= connected.has(key);
  }
  return { nodes: [...groups.values()], invalid: [...invalid.values()] };
}
