import type { PartInterface } from "./component.ts";

const labels: Record<string, string> = {
  "plane-contact": "面贴合", "pin-hole": "销孔", "coaxial-fit": "轴孔配合",
  surface: "贴合面", "contact-face": "贴合面", pin: "销", hole: "孔", shaft: "轴", bore: "轴孔",
  welded: "焊接", bolted: "螺栓连接", "clearance-fit": "间隙配合",
  "interference-fit": "过盈配合", "transition-fit": "过渡配合",
};

export function interfaceContractGroups(interfaces: PartInterface[]) {
  const groups = new Map<string, { key: string; label: string; interfaces: PartInterface[] }>();
  for (const item of interfaces) {
    const contract = item.mating;
    const values = contract ? [contract.family, contract.role, contract.connectionMethod || "未指定连接方式", `v${contract.version}`] : [];
    const key = JSON.stringify(values);
    let group = groups.get(key);
    if (!group) {
      group = { key, label: values.length ? values.map(value => labels[value] ? `${labels[value]}（${value}）` : value).join(" · ") : "未定义配合契约", interfaces: [] };
      groups.set(key, group);
    }
    group.interfaces.push(item);
  }
  return [...groups.values()];
}
