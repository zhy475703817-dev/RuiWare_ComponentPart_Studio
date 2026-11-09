from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .metamodel import (
    AIProposal,
    EvidenceInference,
    FeatureRule,
    GeometryRecipe,
    MaterialRequirement,
    ParameterDefinition,
    ParameterSource,
    PartInterface,
    SemanticFaceLocator,
    VariantDefinition,
)
from .material import effective_thickness_domain


MaterialMode = Literal["reference", "copy"]
StageName = Literal["templateInfo", "material", "baseSketch", "features", "variants", "review", "admission"]
StageState = Literal["not_started", "in_progress", "complete"]


class ManufacturingClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    originId: str = "inHouse"
    primaryProcessId: str = "coldRollForming"
    secondaryProcessIds: list[str] = Field(default_factory=list)
    reviewed: bool = False


class MaterialRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    code: str
    name: str
    type: str
    grade: str | None = None
    thickness: float | None = None
    standard: str | None = None
    surface: str | None = None
    updatedAt: str | None = None


class MaterialBinding(BaseModel):
    id: str
    mode: MaterialMode
    sourceLibrary: str
    sourceRecordId: str
    sourceUpdatedAt: str | None = None
    sourceChecksum: str
    snapshot: dict[str, Any] | None = None


class SourceAttachment(BaseModel):
    id: str
    filename: str
    mediaType: str
    kind: Literal["referenceImage", "drawing", "specification", "other"] = "other"
    description: str = Field(default="", max_length=1000)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    url: str
    createdAt: str


class StageStatus(BaseModel):
    templateInfo: StageState = "in_progress"
    material: StageState = "not_started"
    baseSketch: StageState = "not_started"
    features: StageState = "not_started"
    variants: StageState = "not_started"
    review: StageState = "not_started"
    admission: StageState = "not_started"


class BlankDefinition(BaseModel):
    form: Literal["strip", "flatBlank", "profileSegment", "tubeSegment", "barSegment", "wireBlank", "castBlank", "externalModel", "standardPart"] = "strip"
    preparationMode: Literal["sameAsSupply", "preparedBlank"] = "preparedBlank"
    preparationProcesses: list[Literal["uncoiling", "leveling", "slitting", "cutToLength", "sawing", "blanking", "preforming", "none"]] = Field(default_factory=lambda: ["uncoiling", "slitting"])
    manufacturingRoute: Literal["coldRollForming", "laserCutting", "machining", "extrusion", "bending", "casting", "purchased"] = "coldRollForming"
    lengthExpression: str = "length"
    widthExpression: str = "sectionWidth"
    thicknessExpression: str = "thickness"
    lengthAllowance: float = Field(default=0, ge=0, le=500)
    widthAllowance: float = Field(default=0, ge=0, le=500)
    grainDirection: Literal["longitudinal", "transverse", "notApplicable"] = "longitudinal"


class MaterialValidationSample(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    role: Literal["minimum", "nominal", "maximum", "special"] = "nominal"
    name: str = Field(min_length=1, max_length=80)
    bindingId: str
    bindingMode: MaterialMode
    materialCode: str
    materialName: str
    materialThickness: float | None = None
    variantId: str = "nominal"
    requiredForAdmission: bool = True
    reviewed: bool = False


class SemanticSketchEntity(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    role: str
    geometryType: Literal["point", "line", "arc", "circle"] = "line"
    parameterRefs: list[str] = Field(default_factory=list)
    construction: bool = False
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    center: tuple[float, float] | None = None
    radius: float | None = Field(default=None, gt=0)
    startAngle: float | None = None
    endAngle: float | None = None
    largeArc: bool | None = None
    points: list[tuple[float, float]] = Field(default_factory=list)


class SemanticSketchConstraint(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str = ""
    constraintType: Literal["coincident", "horizontal", "vertical", "parallel", "perpendicular", "tangent", "concentric", "symmetric", "equal", "distance", "distanceX", "distanceY", "radius", "diameter", "angle", "fixed", "pointOn", "closed"]
    entityRefs: list[str] = Field(default_factory=list)
    # For coincident pairs: which endpoint of each entity participates ("start" | "end").
    # Legacy omissions default to end→start chain semantics in the solver.
    endpointRefs: list[Literal["start", "end"]] = Field(default_factory=list)
    expression: str | None = None
    parameterId: str | None = None
    value: float | None = None
    driverMode: Literal["unset", "fixed", "parameter", "expression"] | None = None
    enabled: bool = True
    driving: bool = True


class SemanticSketchRegion(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    boundaryRefs: list[str] = Field(default_factory=list)
    closed: bool = True
    role: str = "section.materialRegion"
    operation: Literal["add", "subtract"] = "add"


class SweepPathGeometry(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    role: str = "sweep.path.segment"
    geometryType: Literal["point", "line", "arc", "circle"] = "line"
    parameterRefs: list[str] = Field(default_factory=list)
    construction: bool = False
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    center: tuple[float, float] | None = None
    radius: float | None = Field(default=None, gt=0)
    startAngle: float | None = None
    endAngle: float | None = None
    largeArc: bool | None = None
    # Explicit traversal direction for parameterized arcs.  Legacy drafts did
    # not persist this field; CCW is the canonical backwards-compatible
    # default and validation still rejects any other value supplied by a raw
    # payload before it reaches the geometry worker.
    sweepDirection: Literal["cw", "ccw"] = "ccw"
    points: list[tuple[float, float]] = Field(default_factory=list)


class SweepPathConstraint(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    label: str = ""
    constraintType: Literal["coincident", "horizontal", "vertical", "parallel", "perpendicular", "distance", "distanceX", "distanceY", "radius", "diameter", "angle", "fixed", "tangent", "concentric", "symmetric", "equal", "pointOn", "closed"]
    entityRefs: list[str] = Field(default_factory=list)
    endpointRefs: list[Literal["start", "end"]] = Field(default_factory=list)
    expression: str | None = None
    parameterId: str | None = None
    value: float | None = None
    driverMode: Literal["unset", "fixed", "parameter", "expression"] | None = None
    enabled: bool = True
    driving: bool = True


class SweepPathEndpointRef(BaseModel):
    geometryId: str
    endpoint: Literal["start", "end"]


class SweepPathSketch(BaseModel):
    id: str = "path.main"
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    geometry: list[SweepPathGeometry] = Field(default_factory=list)
    constraints: list[SweepPathConstraint] = Field(default_factory=list)
    startPointId: str | None = None
    startEndpointRef: SweepPathEndpointRef | None = None
    status: Literal["empty", "editing", "valid", "invalid", "confirmed"] = "empty"
    generationStatus: Literal["idle", "generating", "failed", "succeeded"] = "idle"
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class SketchDefinition(BaseModel):
    model: Literal["semanticProfile"] = "semanticProfile"
    acquisitionMethod: Literal["manual", "imported", "reused"] = "manual"
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    profileMode: Literal["closedRegion", "multiRegion", "centerlineThinWall"] = "closedRegion"

    @field_validator("acquisitionMethod", mode="before")
    @classmethod
    def migrate_removed_assisted_source(cls, value: object) -> object:
        """Read legacy drafts without exposing the removed assisted-creation mode."""
        return "manual" if value == "aiAssisted" else value
    drivingParameters: list[str] = Field(default_factory=lambda: ["sectionWidth", "sectionHeight"])
    entities: list[SemanticSketchEntity] = Field(default_factory=lambda: [
        SemanticSketchEntity(id="edge.bottom", role="section.edge.bottom", start=(-50, -25), end=(50, -25), parameterRefs=["sectionWidth"]),
        SemanticSketchEntity(id="edge.right", role="section.edge.right", start=(50, -25), end=(50, 25), parameterRefs=["sectionHeight"]),
        SemanticSketchEntity(id="edge.top", role="section.edge.top", start=(50, 25), end=(-50, 25), parameterRefs=["sectionWidth"]),
        SemanticSketchEntity(id="edge.left", role="section.edge.left", start=(-50, 25), end=(-50, -25), parameterRefs=["sectionHeight"]),
    ])
    constraints: list[SemanticSketchConstraint] = Field(default_factory=lambda: [
        SemanticSketchConstraint(id="constraint.loop.joint.1", constraintType="coincident", entityRefs=["edge.bottom", "edge.right"], endpointRefs=["end", "start"], label="首尾相连 1"),
        SemanticSketchConstraint(id="constraint.loop.joint.2", constraintType="coincident", entityRefs=["edge.right", "edge.top"], endpointRefs=["end", "start"], label="首尾相连 2"),
        SemanticSketchConstraint(id="constraint.loop.joint.3", constraintType="coincident", entityRefs=["edge.top", "edge.left"], endpointRefs=["end", "start"], label="首尾相连 3"),
        SemanticSketchConstraint(id="constraint.loop.joint.4", constraintType="coincident", entityRefs=["edge.left", "edge.bottom"], endpointRefs=["end", "start"], label="首尾相连 4"),
        SemanticSketchConstraint(id="constraint.horizontal", constraintType="horizontal", entityRefs=["edge.bottom", "edge.top"]),
        SemanticSketchConstraint(id="constraint.vertical", constraintType="vertical", entityRefs=["edge.left", "edge.right"]),
        SemanticSketchConstraint(id="dimension.width", constraintType="distanceX", entityRefs=["edge.bottom"], parameterId="sectionWidth", driverMode="parameter"),
        SemanticSketchConstraint(id="dimension.height", constraintType="distanceY", entityRefs=["edge.right"], parameterId="sectionHeight", driverMode="parameter"),
        SemanticSketchConstraint(id="constraint.origin", constraintType="fixed", entityRefs=["edge.bottom"]),
    ])
    regions: list[SemanticSketchRegion] = Field(default_factory=lambda: [SemanticSketchRegion(id="section.region.main", boundaryRefs=["edge.bottom", "edge.right", "edge.top", "edge.left"])])
    constraintsReviewed: bool = False
    sourceAttachmentId: str | None = None
    sourceProfileId: str | None = None
    sourceHash: str | None = None
    importUnit: Literal["mm", "cm", "m", "inch"] | None = None
    importScale: float | None = Field(default=None, gt=0)
    conversionReviewed: bool = False

class AdmissionDefinition(BaseModel):
    changeNote: str = ""
    reviewer: str = ""
    releaseChannel: Literal["development", "pilot", "production"] = "development"


def default_parameter_definitions() -> list[ParameterDefinition]:
    return [
        ParameterDefinition(id="length", label="长度", default=1000, minimum=100, maximum=6000, sourceDefinition={"type": "userInput"}),
        ParameterDefinition(id="sectionWidth", label="截面宽度", default=100, minimum=10, maximum=1000, sourceDefinition={"type": "userInput"}),
        ParameterDefinition(id="sectionHeight", label="截面高度", default=50, minimum=10, maximum=1000, sourceDefinition={"type": "userInput"}),
        ParameterDefinition(id="thickness", label="壁厚", default=2, minimum=0.8, maximum=8, source="material", sourceDefinition={"type": "materialProperty", "reference": "material.thickness", "fallback": 2}),
    ]


def default_material_requirement() -> MaterialRequirement:
    return MaterialRequirement()


def default_geometry_recipe() -> GeometryRecipe:
    return GeometryRecipe(
        constructionMode="extrude",
        semanticFaces=[
            {"id": "part.face.front", "label": "前侧面", "hostFrame": "negativeY", "locator": {"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.bottom"}, "uStartExpression": "-sectionWidth / 2", "uSpanExpression": "sectionWidth", "vStartExpression": "0", "vSpanExpression": "length"},
            {"id": "part.face.back", "label": "后侧面", "hostFrame": "positiveY", "locator": {"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.top"}, "uStartExpression": "-sectionWidth / 2", "uSpanExpression": "sectionWidth", "vStartExpression": "0", "vSpanExpression": "length"},
            {"id": "part.face.left", "label": "左侧面", "hostFrame": "negativeX", "locator": {"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.left"}, "uStartExpression": "-sectionHeight / 2", "uSpanExpression": "sectionHeight", "vStartExpression": "0", "vSpanExpression": "length"},
            {"id": "part.face.right", "label": "右侧面", "hostFrame": "positiveX", "locator": {"kind": "profileEdge", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "edge.right"}, "uStartExpression": "-sectionHeight / 2", "uSpanExpression": "sectionHeight", "vStartExpression": "0", "vSpanExpression": "length"},
            {"id": "part.endFace.start", "label": "起始端面", "hostFrame": "negativeZ", "locator": {"kind": "profileRegion", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "section.region.main", "capSide": "start"}, "uStartExpression": "-sectionWidth / 2", "uSpanExpression": "sectionWidth", "vStartExpression": "-sectionHeight / 2", "vSpanExpression": "sectionHeight"},
            {"id": "part.endFace.end", "label": "终止端面", "hostFrame": "positiveZ", "locator": {"kind": "profileRegion", "operationId": "body.main", "profileSketchId": "sketch.section.main", "sourceEntityId": "section.region.main", "capSide": "end"}, "uStartExpression": "-sectionWidth / 2", "uSpanExpression": "sectionWidth", "vStartExpression": "-sectionHeight / 2", "vSpanExpression": "sectionHeight"},
        ],
        operations=[
            {
                "id": "body.main",
                "operator": "profile.open_profile_tube_extrude",
                "sourceRefs": ["sketch.section.main"],
                "profileSketchId": "sketch.section.main",
                "argumentExpressions": {
                    "length": "length",
                },
                "semanticOutputs": ["part.body", "part.referenceFrame", "part.endFace.start", "part.endFace.end"],
            }
        ],
    )


class TemplateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal["3.0"] = "3.0"
    templateKind: Literal["monolithicPart"] = "monolithicPart"
    id: str | None = None
    code: str = ""
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    designIntent: str = ""
    manufacturingClassification: ManufacturingClassification = Field(default_factory=ManufacturingClassification)
    geometryPrototypeId: str = "prototype.customRecipe"
    tags: list[str] = Field(default_factory=list, max_length=20)
    owner: str = ""
    organization: str = ""
    unitSystem: Literal["mm-kg-s"] = "mm-kg-s"
    coordinateSystem: Literal["right-handed-z-up"] = "right-handed-z-up"
    lifecycleStatus: Literal["draft", "review", "published"] = "draft"
    stageStatus: StageStatus = Field(default_factory=StageStatus)
    attachments: list[SourceAttachment] = Field(default_factory=list, max_length=30)
    materialRequirements: list[MaterialRequirement] = Field(default_factory=lambda: [default_material_requirement()])
    materialValidationSamples: list[MaterialValidationSample] = Field(default_factory=list)
    blank: BlankDefinition = Field(default_factory=BlankDefinition)
    sketch: SketchDefinition = Field(default_factory=SketchDefinition)
    sweepPath: SweepPathSketch | None = None
    parameterDefinitions: list[ParameterDefinition] = Field(default_factory=default_parameter_definitions)
    variants: list[VariantDefinition] = Field(default_factory=lambda: [VariantDefinition(id="nominal", name="标称实例")])
    geometryRecipe: GeometryRecipe = Field(default_factory=default_geometry_recipe)
    featureRules: list[FeatureRule] = Field(default_factory=list)
    featureRulesReviewed: bool = False
    interfaces: list[PartInterface] = Field(default_factory=list)
    evidence: list[EvidenceInference] = Field(default_factory=list)
    aiProposals: list[AIProposal] = Field(default_factory=list)
    admission: AdmissionDefinition = Field(default_factory=AdmissionDefinition)
    revision: int = 1
    createdAt: str | None = None
    updatedAt: str | None = None

    @model_validator(mode="after")
    def normalize_metadata(self) -> "TemplateDraft":
        # A legacy payload may omit semanticFaces entirely.  The nested
        # GeometryRecipe default is intentionally richer for new authored
        # templates, but must not silently add source locators to an older
        # draft (especially one whose operation is not an extrusion).
        if "semanticFaces" not in self.geometryRecipe.model_fields_set:
            self.geometryRecipe.semanticFaces = [
                face.model_copy(update={"locator": None})
                for face in self.geometryRecipe.semanticFaces
            ]
        self.code = self.code.strip().upper()
        self.tags = list(dict.fromkeys(tag.strip() for tag in self.tags if tag.strip()))
        if self.materialRequirements:
            requirement = self.materialRequirements[0]
            parameter_id = requirement.thickness.parameterId
            if parameter_id:
                parameter = next((item for item in self.parameterDefinitions if item.id == parameter_id), None)
                if parameter:
                    domain = effective_thickness_domain(requirement)
                    parameter.source = "material"
                    parameter.sourceDefinition = ParameterSource(type="materialProperty", reference="material.thickness", fallback=parameter.default)
                    parameter.minimum = domain["minimum"]
                    parameter.maximum = domain["maximum"]
                    parameter.allowedValues = domain["values"]
        collections = {
            "material requirement": [item.id for item in self.materialRequirements],
            "material validation sample": [item.id for item in self.materialValidationSamples],
            "parameter": [item.id for item in self.parameterDefinitions],
            "geometry operation": [item.id for item in self.geometryRecipe.operations],
            "feature rule": [item.id for item in self.featureRules],
            "interface": [item.id for item in self.interfaces],
            "variant": [item.id for item in self.variants],
            "evidence": [item.id for item in self.evidence],
            "AI proposal": [item.id for item in self.aiProposals],
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} IDs must be unique")
        return self


class StageCheck(BaseModel):
    id: str
    label: str
    passed: bool
    severity: Literal["error", "warning"]
    path: str
    message: str


class StageValidation(BaseModel):
    stage: StageName
    complete: bool
    progress: int = Field(ge=0, le=100)
    checks: list[StageCheck]


class Diagnostic(BaseModel):
    severity: Literal["info", "warning", "error"]
    code: str
    path: str
    message: str
    suggestion: str | None = None


class StaticOperation(BaseModel):
    id: str
    operator: str
    arguments: dict[str, Any]
    semanticOutputs: list[str] = Field(default_factory=list)


class CanonicalPlan(BaseModel):
    version: Literal["3.0"] = "3.0"
    inputHash: str
    operations: list[StaticOperation]
    materialSnapshot: dict[str, Any]
    diagnostics: list[Diagnostic]


class CompileRequest(BaseModel):
    draft: TemplateDraft
    materialSnapshot: dict[str, Any]


class Artifact(BaseModel):
    kind: Literal["step", "stl", "plan", "diagnostics", "semanticMap"]
    url: str
    sha256: str
    packagePath: str | None = None
    size: int | None = Field(default=None, ge=0)


class GeometryMetrics(BaseModel):
    valid: bool
    volume: float
    solidCount: int
    operationCount: int


class CompileResult(BaseModel):
    success: bool
    inputHash: str
    diagnostics: list[Diagnostic]
    metrics: GeometryMetrics | None = None
    artifacts: list[Artifact] = Field(default_factory=list)


class PublishedVersion(BaseModel):
    id: str
    templateId: str
    version: int
    sourceRevision: int
    code: str
    name: str
    createdAt: str
    sourcePackageUrl: str
    compileResult: CompileResult
