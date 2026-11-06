import type { PartInterface } from "./component";

export const PRESET_INTERFACE_TYPES = [
  { id: "bolt.male.v1", label: "螺栓连接公口", defaultConnectableTypeIds: ["bolt.female.v1"] },
  { id: "bolt.female.v1", label: "螺栓连接母口", defaultConnectableTypeIds: ["bolt.male.v1"] },
  { id: "threaded.stud.v1", label: "螺纹公端 / 螺柱", defaultConnectableTypeIds: ["threaded.hole.v1"] },
  { id: "threaded.hole.v1", label: "螺纹母端 / 螺纹孔", defaultConnectableTypeIds: ["threaded.stud.v1"] },
  { id: "pin.v1", label: "定位销", defaultConnectableTypeIds: ["hole.v1"] },
  { id: "hole.v1", label: "定位孔", defaultConnectableTypeIds: ["pin.v1"] },
  { id: "weld.surface.v1", label: "焊接面", defaultConnectableTypeIds: ["weld.surface.v1"] },
  { id: "snap.hook.v1", label: "卡扣公口 / 卡钩", defaultConnectableTypeIds: ["snap.slot.v1"] },
  { id: "snap.slot.v1", label: "卡扣母口 / 卡槽", defaultConnectableTypeIds: ["snap.hook.v1"] },
  { id: "rail.v1", label: "导轨", defaultConnectableTypeIds: ["rail.slot.v1"] },
  { id: "rail.slot.v1", label: "导轨槽", defaultConnectableTypeIds: ["rail.v1"] },
  { id: "plane.contact.v1", label: "平面贴合面", defaultConnectableTypeIds: ["plane.contact.v1"] },
  { id: "shaft.v1", label: "轴 / 轴颈", defaultConnectableTypeIds: ["bore.v1"] },
  { id: "bore.v1", label: "轴孔 / 轴承孔", defaultConnectableTypeIds: ["shaft.v1"] },
] as const;

export function defaultConnectableTypeIds(typeId: string | null | undefined): string[] {
  return [...(PRESET_INTERFACE_TYPES.find(item => item.id === typeId)?.defaultConnectableTypeIds || [])];
}

export function interfaceTypeLabel(typeId: string | null | undefined, interfaces: PartInterface[] = [], customTypes: { id: string; label: string }[] = []): string {
  if (!typeId) return "未分类";
  return PRESET_INTERFACE_TYPES.find(item => item.id === typeId)?.label ||
    customTypes.find(item => item.id === typeId)?.label ||
    (interfaces.some(item => item.connectionTypeId === typeId) ? typeId.replace(/^custom:/, "") : typeId);
}
