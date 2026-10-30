from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


Scalar = int | float | bool | str
ParameterValueType = Literal["number", "integer", "boolean", "enum", "string"]
ParameterSourceType = Literal[
    "userInput", "materialProperty", "formula", "lookup", "productConfig",
    "componentConfig", "projectZone", "standard", "geometricMeasurement",
    "externalApi", "constant",
]


def _repair_mojibake(value: str) -> str:
    """Recover legacy labels saved after UTF-8 was decoded as a single-byte codec."""

    candidate = value
    for _ in range(2):
        repaired = None
        for encoding in ("latin1", "gbk"):
            try:
                decoded = candidate.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            original_has_cjk = any("\u4e00" <= char <= "\u9fff" for char in candidate)
            decoded_has_cjk = any("\u4e00" <= char <= "\u9fff" for char in decoded)
            mojibake_marker = any(char in candidate for char in "ÃÂæåèéçäï")
            if decoded != candidate and (mojibake_marker or (original_has_cjk and decoded_has_cjk)):
                repaired = decoded
                break
        if repaired is None:
            break
        candidate = repaired
    return candidate


def _coerce_parameter_value(value: Scalar, value_type: ParameterValueType) -> Scalar:
    if value_type == "number":
        if isinstance(value, bool):
            raise ValueError("numeric parameter default cannot be boolean")
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("numeric parameter default must be a number") from error
    if value_type == "integer":
        if isinstance(value, bool):
            raise ValueError("integer parameter default cannot be boolean")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("integer parameter default must be an integer") from error
        if not numeric.is_integer():
            raise ValueError("integer parameter default must be an integer")
        return int(numeric)
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        if value in {0, "0", "false", "False"}:
            return False
        if value in {1, "1", "true", "True"}:
            return True
        raise ValueError("boolean parameter default must be true or false")
    return str(value)


class EvidenceInference(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    kind: Literal["observation", "inference", "assumption", "userConfirmation"]
    statement: str
    sourceAttachmentIds: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    confirmed: bool = False


class MaterialThicknessConstraint(BaseModel):
    parameterId: str | None = "thickness"
    allowedValues: list[float] = Field(default_factory=list)
    minimum: float | None = Field(default=None, gt=0)
    maximum: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> "MaterialThicknessConstraint":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("material thickness minimum cannot exceed maximum")
        return self


class MaterialRequirement(BaseModel):
    id: str = "material.main"
    selectionMode: Literal["category", "family", "specificRecord"] = "category"
    supplyForm: Literal[
        "coil", "sheet", "openProfile", "closedProfile", "tube", "bar", "wire",
        "engineeringPlastic", "externalModel", "standardPart", "other",
    ] = "coil"
    familyTags: list[str] = Field(default_factory=list)
    allowedGrades: list[str] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=list)
    thickness: MaterialThicknessConstraint = Field(default_factory=MaterialThicknessConstraint)
    requiredProperties: dict[str, Scalar] = Field(default_factory=dict)
    allowInstanceSubstitution: bool = True
    specificBindingId: str | None = None
    reviewed: bool = False


class ParameterSource(BaseModel):
    type: ParameterSourceType = "userInput"
    reference: str | None = None
    expression: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    lookupTable: dict[str, Scalar] = Field(default_factory=dict)
    fallback: Scalar | None = None


class ParameterDefinition(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str
    displayName: str | None = None
    aliases: list[str] = Field(default_factory=list)
    symbol: str | None = None
    valueType: ParameterValueType = "number"
    unit: str = "mm"
    default: Scalar
    minimum: float | None = None
    maximum: float | None = None
    allowedValues: list[Scalar] = Field(default_factory=list)
    exposed: bool = True
    source: Literal["user", "formula", "material", "lookup"] = "user"
    sourceDefinition: ParameterSource | None = None
    scope: Literal["template", "partInstance", "component", "product", "projectZone"] = "partInstance"
    declaredInRuleStage: bool = False
    contractReady: bool = True
    ruleDefaultFor: str | None = None
    description: str = ""

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "ParameterDefinition":
        self.label = _repair_mojibake(self.label)
        self.displayName = _repair_mojibake(self.displayName) if self.displayName else None
        self.description = _repair_mojibake(self.description)
        self.aliases = [_repair_mojibake(alias) for alias in self.aliases]
        self.displayName = self.displayName or self.label
        self.label = self.displayName
        self.default = _coerce_parameter_value(self.default, self.valueType)
        self.allowedValues = [_coerce_parameter_value(value, self.valueType) for value in self.allowedValues]
        if self.valueType not in {"number", "integer"}:
            self.minimum = None
            self.maximum = None
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("parameter minimum cannot exceed maximum")
        if isinstance(self.default, (int, float)) and not isinstance(self.default, bool):
            if self.minimum is not None and self.default < self.minimum:
                raise ValueError("parameter default must be greater than or equal to minimum")
            if self.maximum is not None and self.default > self.maximum:
                raise ValueError("parameter default must be less than or equal to maximum")
        if self.allowedValues and self.default not in self.allowedValues:
            raise ValueError("parameter default must be one of allowedValues")
        if self.sourceDefinition is None:
            source_type: ParameterSourceType = {
                "user": "userInput", "formula": "formula", "material": "materialProperty", "lookup": "lookup"
            }[self.source]
            self.sourceDefinition = ParameterSource(type=source_type)
        if self.sourceDefinition.fallback is not None:
            self.sourceDefinition.fallback = _coerce_parameter_value(self.sourceDefinition.fallback, self.valueType)
        return self


class GeometryOperationDefinition(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    operator: str = Field(min_length=3)
    sourceRefs: list[str] = Field(default_factory=list)
    arguments: dict[str, Scalar] = Field(default_factory=dict)
    argumentExpressions: dict[str, str] = Field(default_factory=dict)
    conditionExpression: str = "True"
    semanticOutputs: list[str] = Field(default_factory=list)
    pathSketchId: str | None = None
    profileSketchId: str | None = None
    profileAnchor: str = "sketch.origin"
    orientationMode: Literal["followPath", "fixedWorld", "minimumTwist"] = "minimumTwist"
    scaleMode: Literal["constant"] = "constant"
    twistMode: Literal["none"] = "none"
    cornerMode: Literal["right"] = "right"


class SemanticFaceLocator(BaseModel):
    """Stable authored source for an extruded semantic face."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["profileEdge", "profileRegion"]
    coordinateFrame: Literal["global", "profile"] | None = None
    operationId: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    profileSketchId: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    sourceEntityId: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    capSide: Literal["start", "end"] | None = None

    @model_serializer(mode="wrap")
    def serialize_legacy_compatible(self, handler: Any) -> Any:
        payload = handler(self)
        # Keep older package JSON stable when no coordinate frame was authored.
        if payload.get("coordinateFrame") is None:
            payload.pop("coordinateFrame", None)
        return payload

    @model_validator(mode="after")
    def validate_locator_shape(self) -> "SemanticFaceLocator":
        if self.kind == "profileRegion" and self.capSide is None:
            raise ValueError("profileRegion locator requires capSide")
        if self.kind == "profileEdge" and self.capSide is not None:
            raise ValueError("profileEdge locator cannot define capSide")
        return self


class SemanticFaceDefinition(BaseModel):
    """A stable local U/V contract authored with geometry, never inferred from face index."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str
    hostFrame: Literal["negativeY", "positiveY", "negativeX", "positiveX", "negativeZ", "positiveZ", "profile"]
    sourceOperationId: str = "body.main"
    locator: SemanticFaceLocator | None = None
    uStartExpression: str = "-sectionWidth / 2"
    uSpanExpression: str = "sectionWidth"
    vStartExpression: str = "0"
    vSpanExpression: str = "length"


class GeometryRecipe(BaseModel):
    id: str = "geometry.main"
    constructionMode: Literal[
        "extrude", "revolve", "sweep", "loft", "sheetMetal", "coldRollForming",
        "machinedStock", "externalDerived", "standardParametric",
    ] = "extrude"
    sketches: list[str] = Field(default_factory=lambda: ["sketch.section.main"])
    paths: list[str] = Field(default_factory=list)
    operations: list[GeometryOperationDefinition] = Field(default_factory=list)
    semanticFaces: list[SemanticFaceDefinition] = Field(default_factory=lambda: [
        SemanticFaceDefinition(id="part.face.front", label="前侧面", hostFrame="negativeY", locator={"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.bottom"}, uStartExpression="-sectionWidth / 2", uSpanExpression="sectionWidth", vStartExpression="0", vSpanExpression="length"),
        SemanticFaceDefinition(id="part.face.back", label="后侧面", hostFrame="positiveY", locator={"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.top"}, uStartExpression="-sectionWidth / 2", uSpanExpression="sectionWidth", vStartExpression="0", vSpanExpression="length"),
        SemanticFaceDefinition(id="part.face.left", label="左侧面", hostFrame="negativeX", locator={"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.left"}, uStartExpression="-sectionHeight / 2", uSpanExpression="sectionHeight", vStartExpression="0", vSpanExpression="length"),
        SemanticFaceDefinition(id="part.face.right", label="右侧面", hostFrame="positiveX", locator={"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.right"}, uStartExpression="-sectionHeight / 2", uSpanExpression="sectionHeight", vStartExpression="0", vSpanExpression="length"),
        SemanticFaceDefinition(id="part.endFace.start", label="起始端面", hostFrame="negativeZ", locator={"kind": "profileRegion", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "section.region.main", "capSide": "start"}, uStartExpression="-sectionWidth / 2", uSpanExpression="sectionWidth", vStartExpression="-sectionHeight / 2", vSpanExpression="sectionHeight"),
        SemanticFaceDefinition(id="part.endFace.end", label="终止端面", hostFrame="positiveZ", locator={"kind": "profileRegion", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "section.region.main", "capSide": "end"}, uStartExpression="-sectionWidth / 2", uSpanExpression="sectionWidth", vStartExpression="-sectionHeight / 2", vSpanExpression="sectionHeight"),
    ])
    reviewed: bool = False


class PolygonVertex(BaseModel):
    """A straight-edge profile vertex, expressed in the host face's U/V frame."""

    uExpression: str
    vExpression: str


class FaceBinding(BaseModel):
    semanticFaceId: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")


class FeatureDimensionBinding(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    label: str
    parameterId: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")


class FeaturePlacement(BaseModel):
    """Executable local U/V placement for every instance produced by a feature rule."""

    mode: Literal["single", "linearArray", "equalSpan", "maxPitch", "symmetric"] = "single"
    axis: Literal["u", "v"] = "v"
    pitchExpression: str = "100"
    startMarginExpression: str = "0"
    endMarginExpression: str = "0"
    maximumPitchExpression: str = "300"


class FeatureRule(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str
    featureType: Literal["circularHole", "straightSlot", "rectangularCutout", "polygonalCutout"]
    enabled: bool = True
    conditionExpression: str = "True"
    countExpression: str = "0"
    indexVariable: str = "i"
    arguments: dict[str, Scalar] = Field(default_factory=dict)
    argumentExpressions: dict[str, str] = Field(default_factory=dict)
    faceBindings: list[FaceBinding] = Field(default_factory=lambda: [FaceBinding(semanticFaceId="part.face.front")])
    profileDimensions: list[FeatureDimensionBinding] = Field(default_factory=list)
    placement: FeaturePlacement = Field(default_factory=FeaturePlacement)
    polygonVertices: list[PolygonVertex] = Field(default_factory=list)
    maximumCount: int = Field(default=2_000, ge=0, le=20_000)
    semanticGroup: str | None = None
    description: str = ""


class InterfaceReferenceFrame(BaseModel):
    originRef: str | None = None
    axis: Literal["x", "y", "z", "-x", "-y", "-z"] = "z"


class InterfaceRegion(BaseModel):
    mode: Literal["fullFace", "rectangle"] = "fullFace"
    uStartExpression: str = "0"
    vStartExpression: str = "0"
    uSpanExpression: str = "100"
    vSpanExpression: str = "100"
    countExpression: str = "1"
    indexVariable: str = "i"
    placement: FeaturePlacement = Field(default_factory=FeaturePlacement)
    maximumCount: int = Field(default=2_000, ge=1, le=20_000)

    @model_validator(mode="before")
    @classmethod
    def migrate_numeric_coordinates(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for old_name, expression_name in (
            ("uStart", "uStartExpression"),
            ("vStart", "vStartExpression"),
            ("uSpan", "uSpanExpression"),
            ("vSpan", "vSpanExpression"),
        ):
            old_value = migrated.pop(old_name, None)
            if expression_name not in migrated and old_value is not None:
                migrated[expression_name] = str(old_value)
        return migrated

    @model_validator(mode="after")
    def validate_rectangle(self) -> "InterfaceRegion":
        if self.mode == "rectangle":
            expressions = (
                self.uStartExpression, self.vStartExpression,
                self.uSpanExpression, self.vSpanExpression,
            )
            if any(not expression.strip() for expression in expressions):
                raise ValueError("矩形接口区域必须填写 U/V 起点和尺寸表达式")
        return self


class ResolvedInterfaceRegion(BaseModel):
    mode: Literal["rectangle"] = "rectangle"
    uStart: float
    vStart: float
    uSpan: float = Field(gt=0)
    vSpan: float = Field(gt=0)


class PartInterface(BaseModel):
    """A single-part declaration of stable geometry available to a future assembly."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    name: str
    declarationMode: Literal["staticGeometry", "featureDerived"] = "staticGeometry"
    sourceFeatureRuleId: str | None = None
    interfaceType: Literal["locating", "connecting", "supporting", "adjustable", "processDatum", "other"] = "locating"
    locatingType: Literal["planeContact", "axisCoincident", "pinHole", "edgeStop", "slotAdjustable", "keyedAntiError"] | None = None
    role: Literal["primary", "secondary", "tertiary"] | None = None
    geometryRefs: list[str] = Field(default_factory=list)
    referenceFrame: InterfaceReferenceFrame = Field(default_factory=InterfaceReferenceFrame)
    region: InterfaceRegion | None = None
    parameterRefs: list[str] = Field(default_factory=list)
    compatibilityTags: list[str] = Field(default_factory=list)
    connectionGender: Literal["male", "female", "neutral"] | None = None
    interfaceSize: dict[str, Any] | None = None
    connectionMethod: str | None = None
    description: str = ""
    required: bool = False
    reviewed: bool = False

    @model_validator(mode="after")
    def normalize_locating_fields(self) -> "PartInterface":
        if self.interfaceType == "locating":
            self.locatingType = self.locatingType or "planeContact"
            self.role = self.role or "primary"
        else:
            self.locatingType = None
            self.role = None
        if self.declarationMode == "staticGeometry":
            self.sourceFeatureRuleId = None
        return self


class AIProposalOperation(BaseModel):
    action: Literal["add", "replace", "remove", "renameDisplay", "migrateId"]
    path: str
    value: Any | None = None
    previousValue: Any | None = None


class AIProposal(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    stage: str
    summary: str
    evidenceIds: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    operations: list[AIProposalOperation] = Field(default_factory=list)
    affectedObjects: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    requiredConfirmations: list[str] = Field(default_factory=list)
    status: Literal["proposed", "partiallyAccepted", "accepted", "rejected"] = "proposed"


class VariantDefinition(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    name: str
    kind: Literal["nominal", "minimum", "maximum", "standard", "thresholdBefore", "thresholdAfter", "regression", "expectedFailure"] = "standard"
    overrides: dict[str, Scalar] = Field(default_factory=dict)
    expected: Literal["valid", "invalid"] = "valid"
    requiredForAdmission: bool = False
    purpose: str = ""


class ResolvedFeature(BaseModel):
    id: str
    featureType: Literal["circularHole", "straightSlot", "rectangularCutout", "polygonalCutout"]
    arguments: dict[str, Scalar]
    semanticFaceId: str
    hostFace: Literal["negativeY", "positiveY", "negativeX", "positiveX", "negativeZ", "positiveZ", "profile"] = "negativeY"
    locator: SemanticFaceLocator | None = None
    resolvedSourceEntityId: str | None = None
    resolvedUStart: float | None = None
    resolvedUSpan: float | None = None
    resolvedVStart: float | None = None
    resolvedVSpan: float | None = None
    polygonVertices: list[tuple[float, float]] = Field(default_factory=list)
    sourceRuleId: str
    index: int


class ResolvedInterface(BaseModel):
    """An interface occurrence available to a future assembly after rule evaluation."""

    id: str
    sourceInterfaceId: str
    declarationMode: Literal["staticGeometry", "featureDerived"]
    interfaceType: Literal["locating", "connecting", "supporting", "adjustable", "processDatum", "other"]
    geometryRefs: list[str] = Field(default_factory=list)
    parameterRefs: list[str] = Field(default_factory=list)
    region: ResolvedInterfaceRegion | None = None
    sourceFeatureRuleId: str | None = None
    sourceFeatureId: str | None = None


class EvaluationDiagnostic(BaseModel):
    severity: Literal["warning", "error"]
    code: str
    path: str
    message: str


class TemplateEvaluation(BaseModel):
    values: dict[str, Scalar] = Field(default_factory=dict)
    evaluationOrder: list[str] = Field(default_factory=list)
    features: list[ResolvedFeature] = Field(default_factory=list)
    resolvedInterfaces: list[ResolvedInterface] = Field(default_factory=list)
    diagnostics: list[EvaluationDiagnostic] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)
