export type Vector3 = [number, number, number];
export type ParameterValue = number | string | boolean;

export type PartTemplateReference = {
  templateKind?: "part" | "component";
  publicationId: string;
  templateId: string;
  version: number;
  sha256: string;
};

export type PartInterface = {
  id: string;
  name: string;
  type: "plane" | "axis" | "point";
  tags: string[];
};

export type ComponentMember = {
  id: string;
  name: string;
  code: string;
  role: string;
  template: PartTemplateReference;
  position: Vector3;
  rotation: Vector3;
  parameterValues: Record<string, ParameterValue>;
  interfaces: PartInterface[];
};

export type InterfaceSource = { memberId: string; interfaceId: string };
export type Mate = { id: string; from: InterfaceSource; to: InterfaceSource; relation: "贴合" | "同轴" | "重合"; offset: number };
export type ComponentPort = { id: string; name: string; source: InterfaceSource; tags: string[] };
export type ComponentSlot = { id: string; name: string; memberId: string | null; required: boolean };
export type ComponentParameter = { id: string; label: string; value: number | null; unit: string; min: number; max: number };
export type ParameterBinding = { id: string; parameterId: string; memberId: string; memberParameterId: string };
export type ComponentRule = { id: string; expression: string };
export type ConnectionType = "螺栓连接" | "焊接" | "卡扣连接" | "插接" | "铆接" | "胶接" | "自定义";
export type EvidenceFile = { id: string; fileName: string; mediaType: string; size: number; sha256: string; dataUrl?: string | null };
export type ComponentDefinition = {
  name: string;
  code: string;
  category: string;
  description: string;
  projectCode: string;
  owner: string;
  organization: string;
  searchTags: string[];
  partConnectionType: ConnectionType;
  partConnectionCustom: string;
  componentConnectionType: ConnectionType;
  componentConnectionCustom: string;
  designConstraints: string;
  evidenceFiles: EvidenceFile[];
};

export type ComponentDocument = {
  schemaVersion: "component-1.0";
  templateKind: "component";
  revision: number;
  unitSystem: "mm-kg-s";
  coordinateSystem: "right-handed-z-up";
  definition: ComponentDefinition;
  assembly: { members: ComponentMember[]; mates: Mate[] };
  ports: ComponentPort[];
  slots: ComponentSlot[];
  parameters: ComponentParameter[];
  bindings: ParameterBinding[];
  rules: ComponentRule[];
};

export function createComponentDocument(): ComponentDocument {
  return {
    schemaVersion: "component-1.0", templateKind: "component", revision: 1,
    unitSystem: "mm-kg-s", coordinateSystem: "right-handed-z-up",
    definition: {
      name: "横梁组件", code: "", category: "货架组件", description: "由多个已发布零部件组成的可复用组件模板",
      projectCode: "", owner: "", organization: "", searchTags: [],
      partConnectionType: "螺栓连接", partConnectionCustom: "",
      componentConnectionType: "螺栓连接", componentConnectionCustom: "",
      designConstraints: "", evidenceFiles: [],
    },
    assembly: { members: [], mates: [] }, ports: [], slots: [], bindings: [],
    parameters: [{ id: "componentLength", label: "组件总长", value: 2400, unit: "mm", min: 1000, max: 6000 }],
    rules: [{ id: "rule-1", expression: "组件总长必须在 1000~6000 mm 范围内" }],
  };
}

export function hasInterface(document: ComponentDocument, source: InterfaceSource): boolean {
  return document.assembly.members.some(member => member.id === source.memberId && member.interfaces.some(item => item.id === source.interfaceId));
}
