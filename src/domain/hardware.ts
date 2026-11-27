import type { ComponentMember, GeometryDesign, HardwareItem, InterfaceHardware, Mate, PartInterface } from "./component";

import { collectHardwareNodes } from "./hardwareNodes.ts";

export type HardwareUsageLine = { category: string; name: string; specification: string; quantity: number };

export function inferHardwareCategory(name: string): string {
  if (/螺栓|bolt/i.test(name)) return "螺栓";
  if (/螺钉|screw/i.test(name)) return "螺钉";
  if (/螺柱|stud/i.test(name)) return "螺柱";
  if (/螺母|nut/i.test(name)) return "螺母";
  if (/垫圈|垫片|washer/i.test(name)) return "垫圈";
  if (/销|pin/i.test(name)) return "销";
  if (/铆钉|rivet/i.test(name)) return "铆钉";
  if (/轴承|bearing/i.test(name)) return "轴承";
  if (/轴套|bushing/i.test(name)) return "轴套";
  if (/挡圈|retaining.?ring|circlip/i.test(name)) return "挡圈";
  if (/卡扣|clip/i.test(name)) return "卡扣件";
  if (/导轨限位/i.test(name)) return "导轨附件";
  if (/^键$|平键|shaft.?key/i.test(name)) return "键";
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
  const { nodes } = collectHardwareNodes(members, mates, geometryDesign);
  const totals = new Map<string, HardwareUsageLine>();
  for (const node of nodes) {
    const interfaces = node.declarations.map(endpoint => endpoint.item);
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
