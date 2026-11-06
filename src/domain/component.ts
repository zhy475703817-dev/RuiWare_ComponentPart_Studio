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
  declarationMode?: "staticGeometry" | "featureDerived";
  interfaceType?: string;
  locatingType?: string;
  role?: string;
  geometryRefs?: string[];
  parameterRefs?: string[];
  description?: string;
  required?: boolean;
  /** Connection gender used by compatibility analysis. Legacy templates may encode this in tags. */
  connectionGender?: "male" | "female" | "neutral";
  /** Physical connection envelope. Values are millimetres unless a unit is provided. */
  interfaceSize?: { diameter?: number; width?: number; height?: number; depth?: number; tolerance?: number; unit?: string };
  connectionMethod?: string;
  region?: { mode?: string; uStart?: number; vStart?: number; uSpan?: number; vSpan?: number; uSpanExpression?: string; vSpanExpression?: string; countExpression?: string };
};

export type MemberParameterDefinition = {
  id: string;
  label: string;
  unit: string;
  valueType: string;
  minimum: number | null;
  maximum: number | null;
  default: number | string | boolean;
};

export type ResolvedPartInterface = {
  id: string;
  sourceInterfaceId: string;
  declarationMode: string;
  interfaceType: string;
  geometryRefs: string[];
  parameterRefs: string[];
  region: { mode: "rectangle"; uStart: number; vStart: number; uSpan: number; vSpan: number } | null;
  sourceFeatureRuleId?: string | null;
  sourceFeatureId?: string | null;
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
  parameterDefinitions?: MemberParameterDefinition[];
  resolvedInterfaces?: ResolvedPartInterface[];
  interfaces: PartInterface[];
};

export type InterfaceSource = { memberId: string; interfaceId: string };
export type InterfaceSelection = InterfaceSource;
export type Mate = { id: string; from: InterfaceSource; to: InterfaceSource; relation: "贴合" | "同轴" | "重合"; offset: number };
export type ComponentPort = { id: string; name: string; source: InterfaceSource; tags: string[]; interface?: PartInterface };
export type ComponentSlot = { id: string; name: string; memberId: string | null; required: boolean };
export type ComponentParameter = { id: string; label: string; value: number | null; unit: string; min: number; max: number };
export type ParameterBinding = { id: string; parameterId: string; memberId: string; memberParameterId: string };
export type ComponentPattern = { id: string; label: string; memberIds: string[]; countParameterId: string; spacingParameterId: string; axis: "x" | "y" | "z" };
export type ComponentRule = { id: string; kind?: "constraint" | "composition"; label?: string; expression: string };
export type ConnectionMethod = { mode: "preset" | "custom"; value: string; details: string };
export type EvidenceAttachment = { id: string; name: string; mediaType: string; size: number; addedAt: string };
export type ComponentDefinition = {
  name: string;
  code: string;
  category: string;
  description: string;
  projectCode: string;
  owner: string;
  organization: string;
  searchTags: string[];
  connectionForms: { internal: ConnectionMethod; interComponent: ConnectionMethod };
  designConstraints: string;
  evidence: EvidenceAttachment[];
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
  patterns: ComponentPattern[];
  rules: ComponentRule[];
};

export function createComponentDocument(): ComponentDocument {
  return {
    schemaVersion: "component-1.0", templateKind: "component", revision: 1,
    unitSystem: "mm-kg-s", coordinateSystem: "right-handed-z-up",
    definition: {
      name: "横梁组件", code: "", category: "货架组件", description: "由多个已发布零部件组成的可复用组件模板",
      projectCode: "", owner: "", organization: "", searchTags: [],
      connectionForms: {
        internal: { mode: "preset", value: "螺栓连接", details: "" },
        interComponent: { mode: "preset", value: "插接/卡扣", details: "" },
      },
      designConstraints: "", evidence: [],
    },
    assembly: { members: [], mates: [] }, ports: [], slots: [], bindings: [], patterns: [],
    parameters: [
      { id: "componentLength", label: "组件总长", value: 2400, unit: "mm", min: 1000, max: 6000 },
      { id: "componentWidth", label: "组件总宽", value: 600, unit: "mm", min: 100, max: 3000 },
      { id: "componentHeight", label: "组件总高", value: 1800, unit: "mm", min: 300, max: 8000 },
    ],
    rules: [{ id: "rule-1", kind: "constraint", label: "工程限制", expression: "组件总长必须在 1000~6000 mm 范围内" }],
  };
}

export function hasInterface(document: ComponentDocument, source: InterfaceSource): boolean {
  return getInterface(document, source) !== undefined;
}

export function getInterface(document: ComponentDocument, source: InterfaceSource): PartInterface | undefined {
  return document.assembly.members.find(member => member.id === source.memberId)?.interfaces.find(item => item.id === source.interfaceId);
}

export function samePartInterface(a: PartInterface | undefined, b: PartInterface | undefined): boolean {
  const normalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(normalize);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, normalize(item)]));
    return value;
  };
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}
