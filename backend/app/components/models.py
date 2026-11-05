from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_serializer, model_validator


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


class ConnectorFrame(ComponentModel):
    origin: Vector3
    rotation: Vector3


class PartInterface(ComponentModel):
    id: str
    name: str
    type: Literal["plane", "axis", "point"]
    tags: list[str]
    frame: ConnectorFrame | None = Field(default=None, exclude_if=lambda value: value is None)
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
    region: dict[str, object] | None = None

    @model_serializer(mode="wrap")
    def preserve_optional_metadata(self, handler):
        data = handler(self)
        for field in ("declarationMode", "interfaceType", "locatingType", "role", "geometryRefs", "parameterRefs", "description", "required", "connectionGender", "interfaceSize", "connectionMethod", "region"):
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


class ComponentMember(ComponentModel):
    id: str
    name: str
    code: str
    role: str
    template: PartTemplateReference
    position: Vector3
    rotation: Vector3
    parameterValues: dict[str, ParameterValue]
    parameterDefinitions: list[MemberParameterDefinition] = Field(default_factory=list)
    resolvedInterfaces: list[dict[str, object]] = Field(default_factory=list)
    interfaces: list[PartInterface]

    @model_serializer(mode="wrap")
    def preserve_legacy_members(self, handler):
        data = handler(self)
        for field in ("parameterDefinitions", "resolvedInterfaces"):
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


class PortDimension(ComponentModel):
    key: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[float, Field(strict=True, ge=0)] | None = None
    unit: Literal["mm", "cm", "m"] = "mm"
    tolerance: Annotated[float, Field(strict=True, ge=0)] | None = None

    @field_validator("key")
    @classmethod
    def strip_key(cls, value):
        if not value.strip():
            raise ValueError("尺寸标识不能为空")
        return value.strip()


class PortSpecification(ComponentModel):
    category: str = ""
    role: Literal["male", "female", "symmetric", "unknown"] = "unknown"
    contact: Literal["clearance", "contact", "press-fit", "unknown"] = "unknown"
    clearanceMinMm: Number | None = None
    clearanceMaxMm: Number | None = None
    dimensions: Annotated[list[PortDimension], Field(max_length=32)] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_dimensions(self):
        keys = [item.key.strip() for item in self.dimensions]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("尺寸标识不能为空或重复")
        if self.clearanceMinMm is not None and self.clearanceMaxMm is not None and self.clearanceMinMm > self.clearanceMaxMm:
            raise ValueError("间隙下限不能大于上限")
        if self.contact in ("clearance", "contact") and self.clearanceMinMm is not None and self.clearanceMinMm < 0:
            raise ValueError("非压配合的间隙下限不能为负")
        return self


class PortConfirmation(ComponentModel):
    key: str
    revision: Revision
    createdAt: str
    actor: Literal["user"]
    note: str
    snapshot: dict = Field(default_factory=dict)


class PortTrialOptions(ComponentModel):
    targetDraftId: str
    targetPortId: str
    expectedTargetRevision: Revision
    relation: Literal["固定"] = "固定"
    offset: Number = 0
    angle: Number = 0
    flip: bool = True


class PortRuleIssue(ComponentModel):
    code: str
    message: str
    status: Literal["fail", "pending"]


class PortDimensionResult(ComponentModel):
    key: str
    movingMm: Number | None
    targetMm: Number | None
    clearanceMm: Number | None = None
    uncertaintyMm: Number | None = None
    status: Literal["pass", "fail", "pending"]


class PortRulesResult(ComponentModel):
    status: Literal["pass", "fail", "pending"]
    issues: list[PortRuleIssue]
    dimensions: list[PortDimensionResult]


class PortAlignmentResult(ComponentModel):
    status: Literal["pass", "fail", "pending"]
    positionErrorMm: Number | None = None
    angleErrorRad: Number | None = None
    beforeDistanceMm: Number | None = None
    beforeAngleRad: Number | None = None


class PortCollisionResult(ComponentModel):
    status: Literal["pending"]
    method: Literal["not-run"]
    message: str


class PortTrialReport(ComponentModel):
    status: Literal["fail", "pending"]
    rules: PortRulesResult
    alignment: PortAlignmentResult
    collision: PortCollisionResult


class PortTrialRecord(ComponentModel):
    options: PortTrialOptions
    sourceKey: str
    targetKey: str
    revision: Revision
    createdAt: str
    actor: Literal["user", "agent"]
    report: dict

    @field_validator("report")
    @classmethod
    def validate_report(cls, value):
        return PortTrialReport.model_validate(value).model_dump(exclude_unset=True)


class ComponentPort(ComponentModel):
    id: str
    name: str
    source: InterfaceSource
    tags: list[str]

    required: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    compatibleTags: list[str] | None = Field(default=None, exclude_if=lambda value: value is None)
    allowedRelations: list[Literal["贴合", "同轴", "重合", "固定"]] | None = Field(default=None, exclude_if=lambda value: value is None)
    specification: PortSpecification | None = Field(default=None, exclude_if=lambda value: value is None)
    basisConfirmation: PortConfirmation | None = Field(default=None, exclude_if=lambda value: value is None)
    reviewConfirmation: PortConfirmation | None = Field(default=None, exclude_if=lambda value: value is None)
    trial: PortTrialRecord | None = Field(default=None, exclude_if=lambda value: value is None)

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


class ComponentSlot(ComponentModel):
    id: str
    name: str
    memberId: str | None
    required: bool


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


class ComponentRule(ComponentModel):
    id: str
    kind: Literal["constraint", "composition"] = "constraint"
    label: str = ""
    expression: str


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


class ComponentDefinition(ComponentModel):
    name: str
    code: str
    category: str
    description: str
    projectCode: str = ""
    owner: str = ""
    organization: str = ""
    searchTags: list[str] = Field(default_factory=list)
    connectionForms: ComponentConnectionForms = Field(default_factory=lambda: ComponentConnectionForms(
        internal=ConnectionMethod(mode="preset", value="螺栓连接", details=""),
        interComponent=ConnectionMethod(mode="preset", value="插接/卡扣", details=""),
    ))
    designConstraints: str = ""
    evidence: list[EvidenceAttachment] = Field(default_factory=list)


class Assembly(ComponentModel):
    members: list[ComponentMember]
    mates: list[Mate]


class ComponentDocument(ComponentModel):
    schemaVersion: Literal["component-1.0"]
    templateKind: Literal["component"]
    revision: Revision
    unitSystem: Literal["mm-kg-s"]
    coordinateSystem: Literal["right-handed-z-up"]
    definition: ComponentDefinition
    assembly: Assembly
    ports: list[ComponentPort]
    slots: list[ComponentSlot]
    parameters: list[ComponentParameter]
    bindings: list[ParameterBinding]
    patterns: list[ComponentPattern] = Field(default_factory=list)
    rules: list[ComponentRule]


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
    connectionForms: ComponentConnectionForms | None = None
    designConstraints: str | None = None
    evidence: list[EvidenceAttachment] | None = None


class MemberPatch(PatchModel):
    role: str | None = None
    position: Vector3 | None = None
    rotation: Vector3 | None = None
    parameterValues: dict[str, ParameterValue] | None = None


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


class PortAddAction(ComponentModel):
    type: Literal["port.add"]
    port: ComponentPort


class PortPatch(PatchModel):
    name: str | None = None
    source: InterfaceSource | None = None
    tags: list[str] | None = None
    required: bool | None = None
    compatibleTags: list[str] | None = None
    allowedRelations: list[Literal["贴合", "同轴", "重合", "固定"]] | None = None
    specification: PortSpecification | None = None


class PortConfirmAction(ComponentModel):
    type: Literal["port.confirm"]
    id: str
    scope: Literal["basis", "review"]
    note: Annotated[str, Field(min_length=1, max_length=2000)]


class PortTrialRecordAction(ComponentModel):
    type: Literal["port.trial.record"]
    id: str
    options: PortTrialOptions


class PortTrialRequest(ComponentModel):
    expectedRevision: Revision
    portId: str
    options: PortTrialOptions


class PortPatchAction(ComponentModel):
    type: Literal["port.patch"]
    id: str
    patch: PortPatch


class PortRemoveAction(ComponentModel):
    type: Literal["port.remove"]
    id: str


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


class RuleAddAction(ComponentModel):
    type: Literal["rule.add"]
    rule: ComponentRule


class RuleRemoveAction(ComponentModel):
    type: Literal["rule.remove"]
    id: str


class InterfaceSetAction(ComponentModel):
    type: Literal["interface.set"]
    memberId: str
    interfaceId: str
    frame: ConnectorFrame


TypedAction = Annotated[
    ResetAction | DefinitionPatchAction | MemberAddAction | MemberPatchAction | MemberRemoveAction
    | PortPatchAction | PortRemoveAction | PortConfirmAction | PortTrialRecordAction | InterfaceSetAction
    | MateAddAction | PortAddAction | ParameterValueAction | ParameterAddAction | ParameterRemoveAction | BindingSetAction | PatternAddAction | PatternRemoveAction | RuleAddAction | RuleRemoveAction | RuleExpressionAction,
    Field(discriminator="type"),
]
ComponentAction = TypeAdapter(TypedAction)


class ActionRequest(ComponentModel):
    expectedRevision: Revision
    action: TypedAction
    reason: Annotated[str, Field(max_length=2000)] = ""


class CreateDraftRequest(ComponentModel):
    name: str | None = None
    document: ComponentDocument | None = None


class PublishRequest(ComponentModel):
    expectedRevision: Revision
