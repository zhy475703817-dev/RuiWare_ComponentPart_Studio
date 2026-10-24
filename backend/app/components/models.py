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


class PartInterface(ComponentModel):
    id: str
    name: str
    type: Literal["plane", "axis", "point"]
    tags: list[str]


class ComponentMember(ComponentModel):
    id: str
    name: str
    code: str
    role: str
    template: PartTemplateReference
    position: Vector3
    rotation: Vector3
    parameterValues: dict[str, ParameterValue]
    interfaces: list[PartInterface]


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


class ComponentRule(ComponentModel):
    id: str
    expression: str


ConnectionType = Literal["螺栓连接", "焊接", "卡扣连接", "插接", "铆接", "胶接", "自定义"]


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

    @model_validator(mode="after")
    def validate_evidence_files(self):
        if len({item.id for item in self.evidenceFiles}) != len(self.evidenceFiles):
            raise ValueError("Evidence file IDs must be unique")
        if sum(item.size for item in self.evidenceFiles) > 15 * 1024 * 1024:
            raise ValueError("Evidence files exceed the 15 MB total limit")
        return self


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
    partConnectionType: ConnectionType | None = None
    partConnectionCustom: str | None = None
    componentConnectionType: ConnectionType | None = None
    componentConnectionCustom: str | None = None
    designConstraints: str | None = None
    evidenceFiles: list[EvidenceFile] | None = None


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


class ParameterValueAction(ComponentModel):
    type: Literal["parameter.value"]
    id: str
    value: Number | None


class RuleExpressionAction(ComponentModel):
    type: Literal["rule.expression"]
    id: str
    expression: str


TypedAction = Annotated[
    ResetAction | DefinitionPatchAction | MemberAddAction | MemberPatchAction | MemberRemoveAction
    | MateAddAction | PortAddAction | ParameterValueAction | RuleExpressionAction,
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
