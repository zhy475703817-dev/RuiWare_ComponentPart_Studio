from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_serializer, model_validator


Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Revision = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
Vector3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
ParameterValue = Number | str | bool


class ComponentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, str_max_length=65536)


class PartTemplateReference(ComponentModel):
    templateKind: Literal["part", "component"] = "part"
    publicationId: str
    templateId: str
    version: int
    sha256: str

    @model_serializer(mode="wrap")
    def keep_legacy_reference(self, handler):
        data = handler(self)
        if "templateKind" not in self.model_fields_set:
            data.pop("templateKind", None)
        return data


class MatingDimension(ComponentModel):
    nominal: Number = Field(gt=0)
    lowerTolerance: Number = 0
    upperTolerance: Number = 0
    unit: Literal["mm", "cm", "m", "in"] = "mm"
    parameterRef: str | None = None

    @model_validator(mode="after")
    def validate_tolerances(self):
        if self.lowerTolerance > self.upperTolerance:
            raise ValueError("Invalid mating dimension tolerance")
        if self.parameterRef is not None and not self.parameterRef.strip():
            raise ValueError("Invalid mating dimension parameter reference")
        return self


class MatingContract(ComponentModel):
    version: Annotated[int, Field(strict=True, ge=1)]
    family: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    standardId: str | None = Field(default=None, max_length=120)
    connectionMethod: str | None = Field(default=None, max_length=80)
    dimensions: dict[str, MatingDimension] = Field(default_factory=dict, max_length=16)
    allowedRelations: list[Literal["贴合", "同轴", "重合"]] = Field(min_length=1, max_length=3)
    capacity: Annotated[int, Field(strict=True, ge=1, le=100)] = 1

    @model_validator(mode="after")
    def validate_contract(self):
        if len(set(self.allowedRelations)) != len(self.allowedRelations):
            raise ValueError("Duplicate allowed mating relation")
        return self


    @model_serializer(mode="wrap")
    def preserve_optional_metadata(self, handler):
        data = handler(self)
        for field in ("standardId", "connectionMethod"):
            if field not in self.model_fields_set:
                data.pop(field, None)
        return data


class HardwareItem(ComponentModel):
    category: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    specification: str = Field(max_length=120)
    quantity: Annotated[int, Field(strict=True, ge=1, le=10000)]


class InterfaceHardware(ComponentModel):
    mode: Literal["auto", "manual", "none"]
    items: list[HardwareItem] = Field(default_factory=list, max_length=50)
    scenarioId: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)

    @model_serializer(mode="wrap")
    def preserve_selection_metadata(self, handler):
        data = handler(self)
        for field in ("scenarioId", "note"):
            if field not in self.model_fields_set:
                data.pop(field, None)
        return data



class PartInterface(ComponentModel):
    id: str
    name: str
    type: Literal["plane", "axis", "point"]
    tags: list[str]
    connectionTypeId: str | None = Field(default=None, max_length=120)
    connectableTypeIds: list[str] | None = Field(default=None, max_length=50)
    declarationMode: Literal["staticGeometry", "featureDerived"] | None = None
    interfaceType: Literal["locating", "connecting", "supporting", "adjustable", "processDatum", "other"] | None = None
    locatingType: Literal["planeContact", "axisCoincident", "pinHole", "edgeStop", "slotAdjustable", "keyedAntiError"] | None = None
    role: Literal["primary", "secondary", "tertiary"] | None = None
    geometryRefs: list[str] | None = None
    parameterRefs: list[str] | None = None
    description: str | None = None
    required: bool | None = None
    connectionGender: Literal["male", "female", "neutral"] | None = None
    interfaceSize: dict[str, object] | None = None
    connectionMethod: str | None = None
    referenceFrame: dict[str, object] | None = None
    mating: MatingContract | None = None
    hardware: InterfaceHardware | None = None
    region: dict[str, object] | None = None

    @model_validator(mode="after")
    def repair_legacy_face_carrier(self):
        # Earlier imports incorrectly inferred every connecting interface as an axis.
        face_refs = {"part.face.front", "part.face.back", "part.face.left", "part.face.right", "part.endFace.start", "part.endFace.end"}
        if (self.type == "axis" and self.interfaceType == "connecting"
                and self.locatingType is None and self.mating is not None
                and self.mating.family == "plane-contact" and self.geometryRefs
                and all(ref in face_refs for ref in self.geometryRefs)):
            self.type = "plane"
        return self

    @model_serializer(mode="wrap")
    def preserve_optional_metadata(self, handler):
        data = handler(self)
        for field in ("declarationMode", "interfaceType", "locatingType", "role", "geometryRefs", "parameterRefs", "description", "required", "connectionGender", "interfaceSize", "connectionMethod", "connectionTypeId", "connectableTypeIds", "referenceFrame", "mating", "hardware", "region"):
            if field not in self.model_fields_set:
                data.pop(field, None)
        return data


class MemberParameterDefinition(ComponentModel):
    id: str
    label: str
    unit: str
    valueType: Literal["number", "integer", "string", "enum", "boolean"]
    minimum: Number | None
    maximum: Number | None
    default: ParameterValue
    allowedValues: list[ParameterValue] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def preserve_legacy_allowed(self, handler):
        data = handler(self)
        if "allowedValues" not in self.model_fields_set:
            data.pop("allowedValues", None)
        return data


class ComponentMember(ComponentModel):
    id: str
    name: str
    code: str
    role: str
    template: PartTemplateReference
    position: Vector3
    rotation: Vector3
    planarMovement: bool = False
    parameterValues: dict[str, ParameterValue]
    parameterDefinitions: list[MemberParameterDefinition] = Field(default_factory=list)
    resolvedInterfaces: list[dict[str, object]] = Field(default_factory=list)
    interfaces: list[PartInterface]
    instanceDocument: "ComponentDocument | None" = None
    generatedBy: str | None = None
    generatedIndex: int | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_members(self, handler):
        data = handler(self)
        for field in ("parameterDefinitions", "resolvedInterfaces", "planarMovement", "instanceDocument", "generatedBy", "generatedIndex"):
            if field not in self.model_fields_set:
                data.pop(field, None)
        return data


class InterfaceSource(ComponentModel):
    memberId: str
    interfaceId: str


class Mate(ComponentModel):
    id: str
    from_: InterfaceSource = Field(alias="from")
    to: InterfaceSource
    relation: Literal["贴合", "同轴", "重合"]
    offset: Number


class ComponentPort(ComponentModel):
    id: str
    name: str
    source: InterfaceSource
    tags: list[str]

    # Keep the resolved source declaration with the inherited component port.
    # Older drafts omit this snapshot and remain valid through the custom
    # serializer below.
    interface: PartInterface | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_port(self, handler):
        data = handler(self)
        if "interface" not in self.model_fields_set:
            data.pop("interface", None)
        return data


class ComponentInterfaceType(ComponentModel):
    id: str = Field(min_length=8, max_length=120, pattern=r"^custom:.+$")
    label: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_custom_type(self):
        if self.label != self.label.strip() or not self.label.strip():
            raise ValueError("Invalid custom interface type label")
        if self.id != f"custom:{self.label}":
            raise ValueError("Custom interface type ID must match its label")
        return self


class ComponentSlot(ComponentModel):
    id: str
    name: str
    memberId: str | None
    required: bool
    baseInterface: InterfaceSource | None = None
    interfaceId: str | None = None


class ComponentParameter(ComponentModel):
    id: str
    label: str
    value: Number | None
    unit: str
    min: Number
    max: Number


class ParameterBinding(ComponentModel):
    id: str
    parameterId: str
    memberId: str
    memberParameterId: str


class ComponentPattern(ComponentModel):
    id: str
    label: str
    memberIds: list[str]
    countParameterId: str
    spacingParameterId: str
    axis: Literal["x", "y", "z"]


class BraceGenerationRule(ComponentModel):
    """Generate fixed-size braces from a column hole grid during resize."""
    id: str
    label: str
    enabled: bool = True
    heightParameterId: str
    pitchParameterId: str
    bottomOffsetParameterId: str
    topClearanceParameterId: str
    leftColumnMemberId: str
    rightColumnMemberId: str
    horizontalMemberId: str
    diagonalMemberId: str
    horizontalLeftInterfaceId: str
    horizontalRightInterfaceId: str
    diagonalLeftInterfaceId: str
    diagonalRightInterfaceId: str
    # Geometry relations position real ports without claiming mating-contract
    # compatibility. Keep existing rules' assembly-mate semantics by default.
    connectionMode: Literal["geometry", "mates"] = "mates"
    horizontalRelation: Literal["贴合", "同轴", "重合"] = "贴合"
    diagonalRelation: Literal["贴合", "同轴", "重合"] = "贴合"
    leftColumnInterfacePattern: str = "hole.{index}"
    rightColumnInterfacePattern: str = "hole.{index}"
    diagonalLeftColumnInterfacePattern: str | None = None
    diagonalRightColumnInterfacePattern: str | None = None
    columnInterfaceIndexStart: int = Field(default=0, ge=0, le=10000)
    columnInterfaceIndexStep: int = Field(default=1, ge=1, le=1000)
    # Horizontal braces normally use adjacent holes while diagonals can span
    # several hole pitches. Keep that topology explicit instead of forcing
    # every brace type through one index step.
    diagonalColumnInterfaceIndexStep: int = Field(default=1, ge=1, le=1000)
    diagonalColumnInterfaceIndexStart: int | None = Field(default=None, ge=0, le=10000)
    diagonalPitchParameterId: str | None = None
    diagonalAlternate: bool = True
    remainderPolicy: Literal["reject", "top-clearance"] = "reject"
    generatedMemberPrefix: str = "generated-brace"


class ComponentRule(ComponentModel):
    id: str
    kind: Literal["constraint", "composition"] = "constraint"
    label: str = ""
    expression: str


ConnectionType = Literal["螺栓连接", "焊接", "卡扣连接", "插接", "铆接", "胶接", "型材槽连接", "销钉连接", "插接/卡扣", "自定义"]


class ConnectionMethod(ComponentModel):
    mode: Literal["preset", "custom"]
    value: str
    details: str


class EvidenceAttachment(ComponentModel):
    id: str
    name: str
    mediaType: str
    size: Annotated[int, Field(strict=True, ge=0)]
    addedAt: str


class ComponentConnectionForms(ComponentModel):
    internal: ConnectionMethod
    interComponent: ConnectionMethod


class EvidenceFile(ComponentModel):
    id: str
    fileName: str = Field(max_length=255)
    mediaType: str = Field(max_length=255)
    size: Annotated[int, Field(strict=True, ge=0, le=5 * 1024 * 1024)]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataUrl: str | None = Field(default=None, max_length=7_000_000)


class ComponentDefinition(ComponentModel):
    name: str
    code: str
    category: str
    description: str
    projectCode: str = ""
    owner: str = ""
    organization: str = ""
    searchTags: list[str] = Field(default_factory=list, max_length=100)
    partConnectionType: ConnectionType = "螺栓连接"
    partConnectionCustom: str = ""
    componentConnectionType: ConnectionType = "螺栓连接"
    componentConnectionCustom: str = ""
    designConstraints: str = ""
    evidenceFiles: list[EvidenceFile] = Field(default_factory=list, max_length=10)
    connectionForms: ComponentConnectionForms = Field(default_factory=lambda: ComponentConnectionForms(
        internal=ConnectionMethod(mode="preset", value="螺栓连接", details=""),
        interComponent=ConnectionMethod(mode="preset", value="插接/卡扣", details=""),
    ))
    evidence: list[EvidenceAttachment] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_evidence_files(self):
        if len({item.id for item in self.evidenceFiles}) != len(self.evidenceFiles):
            raise ValueError("Evidence file IDs must be unique")
        if sum(item.size for item in self.evidenceFiles) > 15 * 1024 * 1024:
            raise ValueError("Evidence files exceed the 15 MB total limit")
        return self
class ComponentGroup(ComponentModel):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    memberIds: list[str] = Field(min_length=1, max_length=10000)
    parentId: str | None = None

    @model_serializer(mode="wrap")
    def preserve_optional_parent(self, handler):
        data = handler(self)
        if self.parentId is None:
            data.pop("parentId", None)
        return data


class Assembly(ComponentModel):
    members: list[ComponentMember]
    mates: list[Mate]
    groups: list[ComponentGroup] = Field(default_factory=list, max_length=10000)

    @model_serializer(mode="wrap")
    def preserve_legacy_assembly(self, handler):
        data = handler(self)
        if "groups" not in self.model_fields_set:
            data.pop("groups", None)
        return data

    @model_validator(mode="after")
    def validate_groups(self):
        member_ids = {item.id for item in self.members}
        groups = {group.id: group for group in self.groups}
        if len(groups) != len(self.groups) or member_ids.intersection(groups):
            raise ValueError("Selection group IDs must be unique")
        for group in self.groups:
            if len(set(group.memberIds)) != len(group.memberIds) or not set(group.memberIds).issubset(member_ids):
                raise ValueError("Invalid selection group member reference")
            visited = {group.id}
            current = group
            while current.parentId is not None:
                parent = groups.get(current.parentId)
                if parent is None or parent.id in visited or not set(current.memberIds).issubset(parent.memberIds):
                    raise ValueError("Invalid selection group hierarchy")
                visited.add(parent.id)
                current = parent
        return self


class GeometryEndpoint(ComponentModel):
    path: list[str] = Field(min_length=1, max_length=16)
    interfaceId: str


class GeometryRelation(ComponentModel):
    id: str
    a: GeometryEndpoint
    b: GeometryEndpoint
    mode: Literal["coincident", "preserve-offset", "plane", "axis"] = "preserve-offset"
    origin: Literal["existing", "inferred", "user"] = "user"
    offset: Vector3 = [0, 0, 0]
    normalDot: Number | None = Field(default=None, ge=-1, le=1)


class GeometryLock(ComponentModel):
    path: list[str] = Field(min_length=1, max_length=16)
    parameterId: str | None = None


class GeometryDesign(ComponentModel):
    version: Literal[1] = 1
    axes: Literal["xyz"] = "xyz"
    anchor: Literal["bottom-center"] = "bottom-center"
    locks: list[GeometryLock] = Field(default_factory=list, max_length=1000)
    relations: list[GeometryRelation] = Field(default_factory=list, max_length=2000)
    dimensions: Vector3
    solverVersion: str = "geometry-resize-1"
    inputHash: str = ""
    verified: bool = False
    interfaceSnapshots: list[dict[str, object]] = Field(default_factory=list, max_length=30000)
    verification: dict[str, object] = Field(default_factory=dict)


class ComponentDocument(ComponentModel):
    schemaVersion: Literal["component-1.0", "component-1.1"]
    templateKind: Literal["component"]
    revision: Revision
    unitSystem: Literal["mm-kg-s"]
    coordinateSystem: Literal["right-handed-z-up"]
    definition: ComponentDefinition
    assembly: Assembly
    ports: list[ComponentPort]
    interfaceTypes: list[ComponentInterfaceType] = Field(default_factory=list, max_length=500)
    slots: list[ComponentSlot]
    parameters: list[ComponentParameter]
    bindings: list[ParameterBinding]
    patterns: list[ComponentPattern] = Field(default_factory=list)
    braceGenerationRules: list[BraceGenerationRule] = Field(default_factory=list, max_length=100)
    rules: list[ComponentRule]
    geometryDesign: GeometryDesign | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_geometry(self, handler):
        data = handler(self)
        if "geometryDesign" not in self.model_fields_set:
            data.pop("geometryDesign", None)
        return data

    @model_validator(mode="after")
    def validate_interface_types(self):
        identifiers = [item.id for item in self.interfaceTypes]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate custom interface type ID")
        return self


class PatchModel(ComponentModel):
    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("Patch fields cannot be null")
        return value


class DefinitionPatch(PatchModel):
    name: str | None = None
    code: str | None = None
    category: str | None = None
    description: str | None = None
    projectCode: str | None = None
    owner: str | None = None
    organization: str | None = None
    searchTags: list[str] | None = None
    partConnectionType: ConnectionType | None = None
    partConnectionCustom: str | None = None
    componentConnectionType: ConnectionType | None = None
    componentConnectionCustom: str | None = None
    designConstraints: str | None = None
    evidenceFiles: list[EvidenceFile] | None = None
    connectionForms: ComponentConnectionForms | None = None
    evidence: list[EvidenceAttachment] | None = None


class MemberPatch(PatchModel):
    role: str | None = None
    position: Vector3 | None = None
    rotation: Vector3 | None = None
    planarMovement: bool | None = None
    parameterValues: dict[str, ParameterValue] | None = None
    interfaces: list[PartInterface] | None = None
    resolvedInterfaces: list[dict[str, object]] | None = None


class ResetAction(ComponentModel):
    type: Literal["reset"]


class DefinitionPatchAction(ComponentModel):
    type: Literal["definition.patch"]
    patch: DefinitionPatch


class MemberAddAction(ComponentModel):
    type: Literal["member.add"]
    member: ComponentMember


class MemberPatchAction(ComponentModel):
    type: Literal["member.patch"]
    id: str
    patch: MemberPatch


class MemberRemoveAction(ComponentModel):
    type: Literal["member.remove"]
    id: str


class MateAddAction(ComponentModel):
    type: Literal["mate.add"]
    mate: Mate


class MateRemoveAction(ComponentModel):
    type: Literal["mate.remove"]
    id: str


class SlotAddAction(ComponentModel):
    type: Literal["slot.add"]
    slot: ComponentSlot


class SlotPatch(ComponentModel):
    name: str | None = None
    required: bool | None = None
    baseInterface: InterfaceSource | None = None
    memberId: str | None = None
    interfaceId: str | None = None


class SlotPatchAction(ComponentModel):
    type: Literal["slot.patch"]
    id: str
    patch: SlotPatch


class SlotRemoveAction(ComponentModel):
    type: Literal["slot.remove"]
    id: str


class PortAddAction(ComponentModel):
    type: Literal["port.add"]
    port: ComponentPort


class PortRemoveAction(ComponentModel):
    type: Literal["port.remove"]
    id: str


class PortRenameAction(ComponentModel):
    type: Literal["port.rename"]
    id: str
    name: str


class InterfaceTypeSetAction(ComponentModel):
    type: Literal["interface.type.set"]
    memberId: str = Field(min_length=1, max_length=120)
    interfaceId: str = Field(min_length=1, max_length=120)
    connectionTypeId: str | None = Field(max_length=120)
    customTypeLabel: str | None = Field(default=None, max_length=80)
    connectableTypeIds: list[str] | None = Field(default=None, max_length=50)


class InterfaceConnectableSetAction(ComponentModel):
    type: Literal["interface.connectable.set"]
    memberId: str = Field(min_length=1, max_length=120)
    interfaceId: str = Field(min_length=1, max_length=120)
    connectableTypeIds: list[str] = Field(max_length=50)


class InterfaceHardwareSetAction(ComponentModel):
    type: Literal["interface.hardware.set"]
    memberId: str = Field(min_length=1, max_length=120)
    interfaceId: str = Field(min_length=1, max_length=120)
    hardware: InterfaceHardware


class InterfaceTypesMergeAction(ComponentModel):
    type: Literal["interface.types.merge"]
    types: list[ComponentInterfaceType] = Field(max_length=500)


class ParameterValueAction(ComponentModel):
    type: Literal["parameter.value"]
    id: str
    value: Number | None


class ParameterAddAction(ComponentModel):
    type: Literal["parameter.add"]
    parameter: ComponentParameter


class ParameterRemoveAction(ComponentModel):
    type: Literal["parameter.remove"]
    id: str


class BindingSetAction(ComponentModel):
    type: Literal["binding.set"]
    id: str
    binding: ParameterBinding | None


class PatternAddAction(ComponentModel):
    type: Literal["pattern.add"]
    pattern: ComponentPattern


class PatternRemoveAction(ComponentModel):
    type: Literal["pattern.remove"]
    id: str


class RuleExpressionAction(ComponentModel):
    type: Literal["rule.expression"]
    id: str
    expression: str


class BraceRuleSetAction(ComponentModel):
    type: Literal["brace.rule.set"]
    rule: BraceGenerationRule


class RuleAddAction(ComponentModel):
    type: Literal["rule.add"]
    rule: ComponentRule


class RuleRemoveAction(ComponentModel):
    type: Literal["rule.remove"]
    id: str


class ModelRestoreState(ComponentModel):
    assembly: Assembly
    ports: list[ComponentPort]
    bindings: list[ParameterBinding]
    patterns: list[ComponentPattern]
    braceGenerationRules: list[BraceGenerationRule] = Field(default_factory=list, max_length=100)
    slots: list[ComponentSlot]


class ModelRestoreAction(ComponentModel):
    type: Literal["model.restore"]
    model: ModelRestoreState


TypedAction = Annotated[
    ModelRestoreAction | ResetAction | DefinitionPatchAction | MemberAddAction | MemberPatchAction | MemberRemoveAction
    | MateAddAction | MateRemoveAction | SlotAddAction | SlotPatchAction | SlotRemoveAction | PortAddAction | PortRemoveAction | PortRenameAction | InterfaceTypeSetAction | InterfaceConnectableSetAction | InterfaceHardwareSetAction | InterfaceTypesMergeAction | ParameterValueAction | ParameterAddAction | ParameterRemoveAction | BindingSetAction | PatternAddAction | PatternRemoveAction | BraceRuleSetAction | RuleAddAction | RuleRemoveAction | RuleExpressionAction,
    Field(discriminator="type"),
]
ComponentAction = TypeAdapter(TypedAction)


class ActionRequest(ComponentModel):
    expectedRevision: Revision
    action: TypedAction


class CreateDraftRequest(ComponentModel):
    name: str | None = None
    document: ComponentDocument | None = None


class PublishRequest(ComponentModel):
    expectedRevision: Revision
