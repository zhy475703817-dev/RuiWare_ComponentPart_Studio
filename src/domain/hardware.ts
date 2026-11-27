import type { ComponentMember, GeometryDesign, GeometryEndpoint, HardwareItem, InterfaceHardware, Mate, PartInterface } from "./component";

export type HardwareUsageLine = { category: string; name: string; specification: string; quantity: number };

export function inferHardwareCategory(name: string): string {
  if (/螺栓|bolt/i.test(name)) return "螺栓";
  if (/螺柱|stud/i.test(name)) return "螺柱";
  if (/螺母|nut/i.test(name)) return "螺母";
  if (/垫圈|垫片|washer/i.test(name)) return "垫圈";
  if (/销|pin/i.test(name)) return "销";
  return "其他";
}

export function recommendInterfaceHardware(item: PartInterface): HardwareItem[] {
  const type = item.connectionTypeId || "";
  const method = `${item.connectionMethod || ""} ${item.mating?.connectionMethod || ""}`.toLowerCase();
  const diameter = item.interfaceSize?.diameter ?? item.mating?.dimensions.diameter?.nominal;
  const spec = typeof diameter === "number" ? `M${diameter}` : "按配合尺寸";
  if (type === "bolt.male.v1" || type === "bolt.female.v1" || /螺栓|bolt/.test(method)) {
    return [
      { category: "螺栓", name: "六角螺栓", specification: `${spec} × 长度待定`, quantity: 1 },
      { category: "螺母", name: "六角螺母", specification: spec, quantity: 1 },
      { category: "垫圈", name: "平垫圈", specification: spec, quantity: 2 },
    ];
  }
  if (["threaded.stud.v1", "threaded.hole.v1"].includes(type) || /螺柱|stud|thread/.test(method)) {
    return [
      { category: "螺柱", name: "螺柱", specification: `${spec} × 长度待定`, quantity: 1 },
      { category: "螺母", name: "六角螺母", specification: spec, quantity: 1 },
      { category: "垫圈", name: "平垫圈", specification: spec, quantity: 2 },
    ];
  }
  if (type === "hole.v1" || type === "pin.v1" || /定位销|dowel pin/.test(method)) return [{ category: "销", name: "定位销", specification: spec, quantity: 1 }];
  return [];
}

function hardwareFor(item: PartInterface): HardwareUsageLine[] {
  const config: InterfaceHardware | undefined = item.hardware;
  if (config?.mode === "none") return [];
  const items = config?.mode === "manual"
    ? (config.items || []).filter(entry => entry.name.trim())
    : recommendInterfaceHardware(item);
  return items.map(entry => ({
    category: entry.category?.trim() || inferHardwareCategory(entry.name),
    name: entry.name.trim(), specification: entry.specification.trim() || "未指定规格", quantity: entry.quantity,
  }));
}

export function summarizeHardwareUsage(members: ComponentMember[], mates: Mate[], geometryDesign?: GeometryDesign | null): HardwareUsageLine[] {
  const parents = new Map<string, string>();
  const endpoints = new Map<string, Map<PartInterface, number>>();
  const root = (key: string): string => {
    const parent = parents.get(key)!;
    if (parent === key) return key;
    const result = root(parent);
    parents.set(key, result);
    return result;
  };
  // Exported component ports and their underlying part interface are one physical endpoint.
  const resolve = (scope: ComponentMember[], endpoint: GeometryEndpoint, prefix: string[]): { key: string; item: PartInterface; depth: number } | undefined => {
    const [id, ...remaining] = endpoint.path;
    const member = scope.find(value => value.id === id);
    if (!member || endpoint.interfaceId.startsWith("@")) return;
    const path = [...prefix, member.id];
    if (remaining.length) return member.instanceDocument ? resolve(member.instanceDocument.assembly.members, { ...endpoint, path: remaining }, path) : undefined;
    const item = member.interfaces.find(value => value.id === endpoint.interfaceId);
    if (!item) return;
    const nested = member.instanceDocument;
    const port = nested?.ports.find(value => value.id === endpoint.interfaceId);
    const inner = nested && port ? resolve(nested.assembly.members, { path: [port.source.memberId], interfaceId: port.source.interfaceId }, path) : undefined;
    return { key: inner?.key ?? JSON.stringify([path, endpoint.interfaceId]), item, depth: path.length };
  };
  const collect = (scope: ComponentMember[], connections: Mate[], geometry: GeometryDesign | null | undefined, prefix: string[]) => {
    const connect = (a: GeometryEndpoint, b: GeometryEndpoint) => {
      const from = resolve(scope, a, prefix), to = resolve(scope, b, prefix);
      // Geometric support faces and stale endpoints are not fastening nodes.
      if (!from || !to || from.key === to.key) return;
      for (const endpoint of [from, to]) {
        if (!parents.has(endpoint.key)) parents.set(endpoint.key, endpoint.key);
        if (!endpoints.has(endpoint.key)) endpoints.set(endpoint.key, new Map());
        endpoints.get(endpoint.key)!.set(endpoint.item, endpoint.depth);
      }
      parents.set(root(to.key), root(from.key));
    };
    for (const mate of connections) connect({ path: [mate.from.memberId], interfaceId: mate.from.interfaceId }, { path: [mate.to.memberId], interfaceId: mate.to.interfaceId });
    for (const relation of geometry?.relations ?? []) connect(relation.a, relation.b);
    for (const member of scope) {
      const nested = member.instanceDocument;
      if (nested) collect(nested.assembly.members, nested.assembly.mates, nested.geometryDesign, [...prefix, member.id]);
    }
  };
  collect(members, mates, geometryDesign, []);
  const nodes = new Map<string, PartInterface[]>();
  for (const [key, definitions] of endpoints) {
    const node = root(key);
    if (!nodes.has(node)) nodes.set(node, []);
    // An edited outer port takes precedence over its inherited inner definition.
    const configured = [...definitions].filter(([item]) => item.hardware && item.hardware.mode !== "auto");
    const shallowest = Math.min(...configured.map(([, depth]) => depth));
    nodes.get(node)!.push(...(configured.length ? configured.filter(([, depth]) => depth === shallowest).map(([item]) => item) : [...definitions.keys()]));
  }
  const totals = new Map<string, HardwareUsageLine>();
  for (const interfaces of nodes.values()) {
    // A saved kit replaces generic recommendations for the whole shared node.
    const manual = interfaces.filter(item => item.hardware?.mode === "manual");
    // Pick one complete automatic kit; a generic opposite hole must not add a second kit.
    const automatic = interfaces.map(item => ({ item, kit: hardwareFor(item) }))
      .filter(candidate => candidate.kit.length)
      .sort((a, b) => b.kit.length - a.kit.length
        || Number(Boolean(b.item.interfaceSize?.diameter ?? b.item.mating?.dimensions.diameter?.nominal)) - Number(Boolean(a.item.interfaceSize?.diameter ?? a.item.mating?.dimensions.diameter?.nominal))
        || JSON.stringify(a.kit).localeCompare(JSON.stringify(b.kit)));
    const selected = manual.length ? manual : interfaces.some(item => item.hardware?.mode === "none") ? [] : automatic.slice(0, 1).map(candidate => candidate.item);
    const pair = new Map<string, HardwareUsageLine>();
    for (const endpoint of selected) {
      const kit = new Map<string, HardwareUsageLine>();
      for (const item of hardwareFor(endpoint)) {
        if (!Number.isFinite(item.quantity) || item.quantity <= 0) continue;
        const key = JSON.stringify([item.category, item.name, item.specification]);
        const previous = kit.get(key);
        if (previous) previous.quantity += item.quantity;
        else kit.set(key, { ...item });
      }
      for (const [key, item] of kit) {
        const previous = pair.get(key);
        if (!previous) pair.set(key, { ...item });
        else previous.quantity = Math.max(previous.quantity, item.quantity);
      }
    }
    for (const [key, item] of pair) {
      const existing = totals.get(key);
      if (existing) existing.quantity += item.quantity;
      else totals.set(key, item);
    }
  }
  return [...totals.values()].sort((left, right) => left.category.localeCompare(right.category, "zh-CN") || left.name.localeCompare(right.name, "zh-CN") || left.specification.localeCompare(right.specification, "zh-CN"));
}

export function groupHardwareUsage(items: HardwareUsageLine[]) {
  const groups = new Map<string, { category: string; varieties: number; quantity: number; items: HardwareUsageLine[] }>();
  for (const item of items) {
    let group = groups.get(item.category);
    if (!group) { group = { category: item.category, varieties: 0, quantity: 0, items: [] }; groups.set(item.category, group); }
    group.items.push(item);
    group.quantity += item.quantity;
  }
  for (const group of groups.values()) group.varieties = group.items.length;
  return [...groups.values()];
}
