from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_TransitionMode,
)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset, BRepOffsetAPI_MakePipeShell, BRepOffsetAPI_ThruSections
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Line, GeomAbs_Plane
from OCP.GC import GC_MakeArcOfCircle
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Vec

from template_core.sweep_frames import path_frames, segment_start_frames

from .base_entities import (
    _fuse,
    _material_face,
    _normal_vector,
    _point_3d,
    _primitive_edge,
    _profile_frame,
    _region_wire,
    _vector_3d,
)
from .sweep_ops import _sketch_sweep
from ..postcheck import solid_count as _solid_count


Vector3: TypeAlias = tuple[float, float, float]


@dataclass(frozen=True)
class FaceSupport:
    """Runtime support geometry traced from an authored profile source.

    ``supportFace`` is an OCCT face occurrence from the completed prism.  The
    frame origin is the minimum U/V corner of the source-owned face domain;
    U and V use the same positive world-axis convention as ``hostFrame``.
    Callers combine this origin with the resolved semantic-face U/V bounds,
    so two coplanar profile edges retain different local origins.
    """

    supportFace: TopoDS_Face
    origin: Vector3
    uDirection: Vector3
    vDirection: Vector3
    normal: Vector3
    sourceEntityId: str
    operationId: str
    profileSketchId: str
    kind: Literal["profileEdge", "profileRegion"]
    capSide: Literal["start", "end"] | None = None


FaceMap: TypeAlias = dict[str, list[FaceSupport]]


def _locator_value(locator: Any, name: str) -> Any:
    if isinstance(locator, dict):
        return locator.get(name)
    return getattr(locator, name, None)


def resolve_face_support(face_map: FaceMap, locator: Any) -> FaceSupport:
    """Resolve one authored locator without consulting B-Rep face ordering."""

    source_entity_id = str(_locator_value(locator, "sourceEntityId") or "")
    kind = _locator_value(locator, "kind")
    operation_id = str(_locator_value(locator, "operationId") or "")
    profile_sketch_id = str(_locator_value(locator, "profileSketchId") or "")
    cap_side = _locator_value(locator, "capSide")
    matches = [
        support
        for support in face_map.get(source_entity_id, [])
        if support.kind == kind
        and support.operationId == operation_id
        and support.profileSketchId == profile_sketch_id
        and support.capSide == cap_side
    ]
    locator_label = (
        f"kind={kind!r}, operationId={operation_id!r}, "
        f"profileSketchId={profile_sketch_id!r}, "
        f"sourceEntityId={source_entity_id!r}, capSide={cap_side!r}"
    )
    if not matches:
        raise RuntimeError(f"Semantic face locator did not resolve to a support face: {locator_label}")
    if len(matches) != 1:
        raise RuntimeError(
            f"Semantic face locator resolved ambiguously to {len(matches)} support faces: {locator_label}"
        )
    return matches[0]


def _unique_shapes(shapes: list[TopoDS_Shape]) -> list[TopoDS_Shape]:
    unique: list[TopoDS_Shape] = []
    for shape in shapes:
        if not any(shape.IsSame(existing) for existing in unique):
            unique.append(shape)
    return unique


def _matching_subshapes(
    root: TopoDS_Shape,
    target: TopoDS_Shape,
    shape_type: Any,
) -> list[TopoDS_Shape]:
    """Return occurrences matching known topology, never an ordinal face."""

    matches: list[TopoDS_Shape] = []
    if root.ShapeType() == shape_type and root.IsSame(target):
        matches.append(root)
    explorer = TopExp_Explorer(root, shape_type)
    while explorer.More():
        occurrence = explorer.Current()
        if occurrence.IsSame(target):
            matches.append(occurrence)
        explorer.Next()
    return _unique_shapes(matches)


def _propagate_history(
    builder: Any,
    result: TopoDS_Shape,
    sources: dict[str, list[TopoDS_Shape]],
    shape_type: Any,
) -> dict[str, list[TopoDS_Shape]]:
    propagated: dict[str, list[TopoDS_Shape]] = {}
    for source_id, source_shapes in sources.items():
        occurrences: list[TopoDS_Shape] = []
        for source_shape in source_shapes:
            successors = list(builder.Modified(source_shape)) + list(builder.Generated(source_shape))
            if not builder.IsDeleted(source_shape):
                successors.append(source_shape)
            for successor in successors:
                occurrences.extend(_matching_subshapes(result, successor, shape_type))
        occurrences = _unique_shapes(occurrences)
        if occurrences:
            propagated[source_id] = occurrences
    return propagated


def _merge_source_shapes(
    current: dict[str, list[TopoDS_Shape]],
    additions: dict[str, list[TopoDS_Shape]],
) -> dict[str, list[TopoDS_Shape]]:
    merged = {source_id: list(shapes) for source_id, shapes in current.items()}
    for source_id, shapes in additions.items():
        merged[source_id] = _unique_shapes([*merged.get(source_id, []), *shapes])
    return merged


def _region_face_with_source_edges(sketch: dict[str, Any], region: dict[str, Any]):
    primitives = {
        item["id"]: item
        for item in sketch["primitives"]
        if not item.get("construction")
    }
    wire_builder = BRepBuilderAPI_MakeWire()
    source_edges: dict[str, list[TopoDS_Shape]] = {}
    for reference in region["boundaryRefs"]:
        primitive = primitives[reference]
        wire_builder.Add(_primitive_edge(primitive, sketch.get("plane", "XY")))
        if not wire_builder.IsDone():
            raise RuntimeError(f"Sketch region wire failed: {region['id']}")
        # MakeWire may replace the edge supplied to Add in order to share a
        # vertex with the preceding edge.  Edge() is the exact edge retained
        # by the wire and therefore the one accepted by MakePrism.Generated.
        source_edges.setdefault(reference, []).append(wire_builder.Edge())
    face = BRepBuilderAPI_MakeFace(wire_builder.Wire()).Face()
    return face, source_edges


def _material_face_with_sources(sketch: dict[str, Any]):
    records = []
    for region in sketch["regions"]:
        face, edges = _region_face_with_source_edges(sketch, region)
        records.append((region, face, edges))
    additive = [record for record in records if record[0]["operation"] == "add"]
    subtractive = [record for record in records if record[0]["operation"] == "subtract"]
    if not additive:
        raise RuntimeError("Sketch has no additive material region")

    first_region, material, first_edges = additive[0]
    tracked_edges = first_edges
    tracked_regions: dict[str, list[TopoDS_Shape]] = {}
    original_region_faces: dict[str, TopoDS_Face] = {}
    if first_region.get("closed", True):
        tracked_regions[first_region["id"]] = [material]
        original_region_faces[first_region["id"]] = material

    for region, face, edges in additive[1:]:
        tracked_edges = _merge_source_shapes(tracked_edges, edges)
        if region.get("closed", True):
            tracked_regions = _merge_source_shapes(tracked_regions, {region["id"]: [face]})
            original_region_faces[region["id"]] = face
        operation = BRepAlgoAPI_Fuse(material, face)
        operation.Build()
        if not operation.IsDone():
            raise RuntimeError(f"Sketch additive-region fuse failed: {region['id']}")
        material = operation.Shape()
        tracked_edges = _propagate_history(operation, material, tracked_edges, TopAbs_EDGE)
        tracked_regions = _propagate_history(operation, material, tracked_regions, TopAbs_FACE)

    for region, face, edges in subtractive:
        # A subtractive region contributes its boundary's longitudinal faces,
        # but the removed region itself cannot own a start/end material cap.
        tracked_edges = _merge_source_shapes(tracked_edges, edges)
        operation = BRepAlgoAPI_Cut(material, face)
        operation.Build()
        if not operation.IsDone():
            raise RuntimeError(f"Sketch subtractive-region cut failed: {region['id']}")
        material = operation.Shape()
        tracked_edges = _propagate_history(operation, material, tracked_edges, TopAbs_EDGE)
        tracked_regions = _propagate_history(operation, material, tracked_regions, TopAbs_FACE)

    return material, tracked_edges, tracked_regions, original_region_faces


def _unit_vector(values: Vector3) -> Vector3:
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1e-12:
        raise RuntimeError("Face support direction is degenerate")
    return tuple(value / length for value in values)  # type: ignore[return-value]


def _dot(first: Vector3, second: Vector3) -> float:
    return sum(a * b for a, b in zip(first, second))


def _canonical_face_axes(
    normal: Vector3,
    surface: BRepAdaptor_Surface,
) -> tuple[Vector3, Vector3]:
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    if abs(normal[dominant]) >= 1 - 1e-8:
        return {
            0: ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            1: ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
            2: ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        }[dominant]

    # Slanted planar edges remain traceable even though today's six
    # hostFrames cannot consume them without a later explicit-frame model.
    plane = surface.Plane()
    x_direction = plane.XAxis().Direction()
    y_direction = plane.YAxis().Direction()
    return (
        (x_direction.X(), x_direction.Y(), x_direction.Z()),
        (y_direction.X(), y_direction.Y(), y_direction.Z()),
    )


def _support_frame(face: TopoDS_Face, domain_shape: TopoDS_Shape | None = None):
    surface = BRepAdaptor_Surface(face, True)
    if surface.GetType() != GeomAbs_Plane:
        return None
    plane = surface.Plane()
    plane_normal = plane.Axis().Direction()
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    normal = _unit_vector((
        sign * plane_normal.X(),
        sign * plane_normal.Y(),
        sign * plane_normal.Z(),
    ))
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    if abs(normal[dominant]) >= 0.98:
        snapped = [0.0, 0.0, 0.0]
        snapped[dominant] = 1.0 if normal[dominant] >= 0 else -1.0
        normal = tuple(snapped)  # type: ignore[assignment]
    u_direction, v_direction = _canonical_face_axes(normal, surface)

    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(domain_shape or face, bounds, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds.Get()
    corners = [
        (x, y, z)
        for x in (xmin, xmax)
        for y in (ymin, ymax)
        for z in (zmin, zmax)
    ]
    minimum_u = min(_dot(point, u_direction) for point in corners)
    minimum_v = min(_dot(point, v_direction) for point in corners)
    location = plane.Location()
    normal_offset = _dot((location.X(), location.Y(), location.Z()), normal)
    origin = tuple(
        minimum_u * u_direction[index]
        + minimum_v * v_direction[index]
        + normal_offset * normal[index]
        for index in range(3)
    )
    return origin, u_direction, v_direction, normal


def _append_face_support(face_map: FaceMap, support: FaceSupport) -> None:
    existing = face_map.setdefault(support.sourceEntityId, [])
    if any(
        item.kind == support.kind
        and item.capSide == support.capSide
        and item.operationId == support.operationId
        and item.profileSketchId == support.profileSketchId
        and item.supportFace.IsSame(support.supportFace)
        for item in existing
    ):
        return
    existing.append(support)


def _sketch_region_extrude(arguments):
    sketch = arguments["sketch"]
    additive_regions = [region for region in sketch["regions"] if region["operation"] == "add"]
    subtractive_regions = [region for region in sketch["regions"] if region["operation"] == "subtract"]
    length = float(arguments["length"])
    # Separate coplanar regions must be fused after extrusion.  Fusing their
    # planar faces first leaves a compound, which OCC extrudes into multiple
    # solids even when the regions share an exact boundary.
    if len(additive_regions) > 1:
        solids = [
            BRepPrimAPI_MakePrism(
                BRepBuilderAPI_MakeFace(_region_wire(sketch, region)).Face(),
                _normal_vector(sketch.get("plane", "XY"), length),
            ).Shape()
            for region in additive_regions
        ]
        result = _fuse(*solids)
        for region in subtractive_regions:
            tool = BRepPrimAPI_MakePrism(
                BRepBuilderAPI_MakeFace(_region_wire(sketch, region)).Face(),
                _normal_vector(sketch.get("plane", "XY"), length),
            ).Shape()
            cut = BRepAlgoAPI_Cut(result, tool)
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(f"Sketch subtractive-region cut failed: {region['id']}")
            result = cut.Shape()
        return result
    face = _material_face(sketch)
    return BRepPrimAPI_MakePrism(face, _normal_vector(sketch.get("plane", "XY"), length)).Shape()


def _faces_in(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    if shape.ShapeType() == TopAbs_FACE:
        return [shape]
    faces: list[TopoDS_Shape] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        faces.append(explorer.Current())
        explorer.Next()
    return _unique_shapes(faces)


def _sketch_region_extrude_with_face_map(
    arguments: dict[str, Any],
    operation_id: str,
    profile_sketch_id: str,
):
    sketch = arguments["sketch"]
    material, source_edges, source_regions, original_region_faces = _material_face_with_sources(sketch)
    length = float(arguments["length"])
    prism = BRepPrimAPI_MakePrism(
        material,
        _normal_vector(sketch.get("plane", "XY"), length),
    )
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError("Sketch region extrusion failed")
    shape = prism.Shape()
    face_map: FaceMap = {}
    primitives = {item["id"]: item for item in sketch["primitives"]}

    for source_entity_id, edges in source_edges.items():
        primitive = primitives.get(source_entity_id)
        if primitive is None or primitive.get("type") != "line":
            # Arc/circle extrusion produces a curved side and is deliberately
            # outside the first planar locator implementation.
            continue
        for edge in edges:
            for generated in prism.Generated(edge):
                for occurrence in _matching_subshapes(shape, generated, TopAbs_FACE):
                    support_face = TopoDS.Face_s(occurrence)
                    frame = _support_frame(support_face)
                    if frame is None:
                        continue
                    origin, u_direction, v_direction, normal = frame
                    _append_face_support(face_map, FaceSupport(
                        supportFace=support_face,
                        origin=origin,
                        uDirection=u_direction,
                        vDirection=v_direction,
                        normal=normal,
                        sourceEntityId=source_entity_id,
                        operationId=operation_id,
                        profileSketchId=profile_sketch_id,
                        kind="profileEdge",
                    ))

    for source_entity_id, basis_faces in source_regions.items():
        original_face = original_region_faces[source_entity_id]
        for basis_face in basis_faces:
            for cap_side, cap_shape in (
                ("start", prism.FirstShape(basis_face)),
                ("end", prism.LastShape(basis_face)),
            ):
                for generated_cap in _faces_in(cap_shape):
                    for occurrence in _matching_subshapes(shape, generated_cap, TopAbs_FACE):
                        support_face = TopoDS.Face_s(occurrence)
                        frame = _support_frame(support_face, original_face)
                        if frame is None:
                            continue
                        origin, u_direction, v_direction, normal = frame
                        _append_face_support(face_map, FaceSupport(
                            supportFace=support_face,
                            origin=origin,
                            uDirection=u_direction,
                            vDirection=v_direction,
                            normal=normal,
                            sourceEntityId=source_entity_id,
                            operationId=operation_id,
                            profileSketchId=profile_sketch_id,
                            kind="profileRegion",
                            capSide=cap_side,
                        ))
    return shape, face_map


def _sketch_revolve(arguments):
    sketch = arguments["sketch"]
    face = _material_face(sketch)
    origin_u, origin_v = float(arguments.get("axisOriginU", 0)), float(arguments.get("axisOriginV", 0))
    direction_u, direction_v = float(arguments.get("axisDirectionU", 0)), float(arguments.get("axisDirectionV", 1))
    if math.hypot(direction_u, direction_v) <= 1e-9:
        raise RuntimeError("Revolve axis direction must be non-zero")
    angle = float(arguments.get("angleDegrees", 360))
    if not 0 < abs(angle) <= 360:
        raise RuntimeError("Revolve angle must be within -360..360 degrees and non-zero")
    axis = gp_Ax1(_point_3d(origin_u, origin_v, sketch.get("plane", "XY")), _vector_3d(direction_u, direction_v, sketch.get("plane", "XY")))
    operation = BRepPrimAPI_MakeRevol(face, axis, math.radians(angle), True)
    operation.Build()
    if not operation.IsDone():
        raise RuntimeError("Revolve construction failed")
    return operation.Shape()


def _parse_stations(value: str) -> list[tuple[float, float]]:
    try:
        stations = [tuple(float(component.strip()) for component in item.split(":")) for item in value.split(";") if item.strip()]
    except ValueError as error:
        raise RuntimeError("Loft stations must use offset:scale;offset:scale format") from error
    if len(stations) < 2 or any(len(item) != 2 for item in stations):
        raise RuntimeError("Loft requires at least two offset:scale stations")
    if any(scale <= 0 for _, scale in stations):
        raise RuntimeError("Loft station scale must be positive")
    if any(second[0] <= first[0] for first, second in zip(stations, stations[1:])):
        raise RuntimeError("Loft station offsets must be strictly increasing")
    return stations


def _loft_region(sketch, region, stations):
    operation = BRepOffsetAPI_ThruSections(True, bool(False), 1e-6)
    for offset, scale in stations:
        operation.AddWire(_region_wire(sketch, region, scale, offset))
    operation.CheckCompatibility(True)
    operation.Build()
    if not operation.IsDone():
        raise RuntimeError(f"Loft construction failed for region {region['id']}")
    return operation.Shape()


def _sketch_loft(arguments):
    sketch = arguments["sketch"]
    stations = _parse_stations(str(arguments.get("stations", "")))
    additive = [_loft_region(sketch, region, stations) for region in sketch["regions"] if region["operation"] == "add"]
    subtractive = [_loft_region(sketch, region, stations) for region in sketch["regions"] if region["operation"] == "subtract"]
    if not additive:
        raise RuntimeError("Loft has no additive material region")
    result = _fuse(*additive)
    for tool in subtractive:
        cut = BRepAlgoAPI_Cut(result, tool)
        cut.Build()
        if not cut.IsDone():
            raise RuntimeError("Loft inner-region subtraction failed")
        result = cut.Shape()
    return result


def _rotate_2d(vector: tuple[float, float], angle: float) -> tuple[float, float]:
    return (
        vector[0] * math.cos(angle) - vector[1] * math.sin(angle),
        vector[0] * math.sin(angle) + vector[1] * math.cos(angle),
    )


def _sheet_single_bend(arguments):
    length, width, thickness = (float(arguments[name]) for name in ("length", "width", "thickness"))
    bend = float(arguments.get("bendPosition", length / 2))
    angle = math.radians(float(arguments.get("bendAngleDegrees", 90)))
    inside_radius = float(arguments.get("insideRadius", thickness))
    k_factor = float(arguments.get("kFactor", 0.42))
    if min(length, width, thickness, inside_radius) <= 0:
        raise RuntimeError("Sheet bend dimensions and inside radius must be positive")
    if not 0 <= k_factor <= 1:
        raise RuntimeError("Sheet bend kFactor must be between 0 and 1")
    if not 0 < abs(angle) < math.pi:
        raise RuntimeError("Sheet bend angle must be between -180 and 180 degrees and non-zero")
    sign = 1.0 if angle > 0 else -1.0
    neutral_radius = inside_radius + k_factor * thickness
    bend_allowance = neutral_radius * abs(angle)
    remaining = length - bend - bend_allowance
    if bend <= thickness or remaining <= thickness:
        raise RuntimeError("Sheet bend leaves no positive straight flange after bend allowance")
    direction = (math.cos(angle), math.sin(angle))
    radial_start = (0.0, -sign)
    center = (bend, sign * neutral_radius)
    radial_mid = _rotate_2d(radial_start, angle / 2)
    radial_end = _rotate_2d(radial_start, angle)
    outer_radius, inner_radius = inside_radius + thickness, inside_radius

    def radial_point(radius: float, radial: tuple[float, float]) -> tuple[float, float]:
        return center[0] + radial[0] * radius, center[1] + radial[1] * radius

    outer_start = radial_point(outer_radius, radial_start)
    outer_mid = radial_point(outer_radius, radial_mid)
    outer_end = radial_point(outer_radius, radial_end)
    inner_start = radial_point(inner_radius, radial_start)
    inner_mid = radial_point(inner_radius, radial_mid)
    inner_end = radial_point(inner_radius, radial_end)
    end_outer = outer_end[0] + direction[0] * remaining, outer_end[1] + direction[1] * remaining
    end_inner = inner_end[0] + direction[0] * remaining, inner_end[1] + direction[1] * remaining
    start_outer = 0.0, outer_start[1]
    start_inner = 0.0, inner_start[1]

    def xz(point: tuple[float, float]) -> gp_Pnt:
        return gp_Pnt(point[0], 0, point[1])

    wire_builder = BRepBuilderAPI_MakeWire()
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(start_outer), xz(outer_start)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(xz(outer_start), xz(outer_mid), xz(outer_end)).Value()).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(outer_end), xz(end_outer)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(end_outer), xz(end_inner)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(end_inner), xz(inner_end)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(xz(inner_end), xz(inner_mid), xz(inner_start)).Value()).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(inner_start), xz(start_inner)).Edge())
    wire_builder.Add(BRepBuilderAPI_MakeEdge(xz(start_inner), xz(start_outer)).Edge())
    if not wire_builder.IsDone():
        raise RuntimeError("Sheet bend profile wire construction failed")
    face = BRepBuilderAPI_MakeFace(wire_builder.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, width, 0)).Shape()


def _line_intersection_2d(
    first_point: tuple[float, float],
    first_direction: tuple[float, float],
    second_point: tuple[float, float],
    second_direction: tuple[float, float],
) -> tuple[float, float] | None:
    determinant = first_direction[0] * second_direction[1] - first_direction[1] * second_direction[0]
    if abs(determinant) <= 1e-9:
        return None
    delta_x = second_point[0] - first_point[0]
    delta_y = second_point[1] - first_point[1]
    scale = (delta_x * second_direction[1] - delta_y * second_direction[0]) / determinant
    return first_point[0] + first_direction[0] * scale, first_point[1] + first_direction[1] * scale


def _connected_line_points(primitives) -> list[tuple[float, float]] | None:
    if not primitives or any(primitive["type"] != "line" for primitive in primitives):
        return None
    points = [(primitives[0]["start"]["x"], primitives[0]["start"]["y"])]
    current = primitives[0]["end"]
    points.append((current["x"], current["y"]))
    for primitive in primitives[1:]:
        start = primitive["start"]
        end = primitive["end"]
        if math.hypot(start["x"] - current["x"], start["y"] - current["y"]) > 1e-7:
            return None
        points.append((end["x"], end["y"]))
        current = end
    return points


def _thinwall_outline(points: list[tuple[float, float]], half_thickness: float) -> list[tuple[float, float]] | None:
    sides = _thinwall_outline_sides(points, half_thickness)
    if sides is None:
        return None
    left, right = sides
    return left + list(reversed(right))


def _thinwall_outline_sides(
    points: list[tuple[float, float]], half_thickness: float
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]] | None:
    if len(points) < 2:
        return None
    if math.dist(points[0], points[-1]) <= 1e-7:
        return None
    segments = []
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return None
        tangent = dx / length, dy / length
        normal = -tangent[1] * half_thickness, tangent[0] * half_thickness
        segments.append((start, end, tangent, normal))

    left = [(points[0][0] + segments[0][3][0], points[0][1] + segments[0][3][1])]
    right = [(points[0][0] - segments[0][3][0], points[0][1] - segments[0][3][1])]
    for index in range(1, len(points) - 1):
        previous = segments[index - 1]
        current = segments[index]
        center = points[index]
        left_join = _line_intersection_2d(
            (center[0] + previous[3][0], center[1] + previous[3][1]),
            previous[2],
            (center[0] + current[3][0], center[1] + current[3][1]),
            current[2],
        )
        right_join = _line_intersection_2d(
            (center[0] - previous[3][0], center[1] - previous[3][1]),
            previous[2],
            (center[0] - current[3][0], center[1] - current[3][1]),
            current[2],
        )
        left.append(left_join or (center[0] + current[3][0], center[1] + current[3][1]))
        right.append(right_join or (center[0] - current[3][0], center[1] - current[3][1]))

    last_segment = segments[-1]
    left.append((points[-1][0] + last_segment[3][0], points[-1][1] + last_segment[3][1]))
    right.append((points[-1][0] - last_segment[3][0], points[-1][1] - last_segment[3][1]))
    return left, right


def _wire_edges(wire: TopoDS_Shape) -> list[TopoDS_Shape]:
    explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire))
    edges: list[TopoDS_Shape] = []
    while explorer.More():
        edges.append(explorer.Current())
        explorer.Next()
    return edges


def _profile_point_2d(point: gp_Pnt, plane: str) -> tuple[float, float]:
    return {
        "XY": (point.X(), point.Y()),
        "XZ": (point.X(), point.Z()),
        "YZ": (point.Y(), point.Z()),
    }[plane]


def _match_offset_line_sources(
    primitives: list[dict[str, Any]],
    offset_edges: list[TopoDS_Shape],
    plane: str,
    half_thickness: float,
) -> dict[str, TopoDS_Shape]:
    """Match authored straight segments to their parallel offset edges."""
    line_edges = [
        edge
        for edge in offset_edges
        if BRepAdaptor_Curve(TopoDS.Edge_s(edge)).GetType() == GeomAbs_Line
    ]
    matched: dict[str, TopoDS_Shape] = {}
    for primitive in primitives:
        if primitive["type"] != "line":
            continue
        start, end = primitive["start"], primitive["end"]
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        source_length = math.hypot(dx, dy)
        if source_length <= 1e-9:
            continue
        tangent = dx / source_length, dy / source_length
        normal = -tangent[1], tangent[0]
        candidates: list[tuple[float, TopoDS_Shape]] = []
        for edge in line_edges:
            first = _profile_point_2d(_edge_first_point(edge), plane)
            last = _profile_point_2d(_edge_last_point(edge), plane)
            edge_dx, edge_dy = last[0] - first[0], last[1] - first[1]
            edge_length = math.hypot(edge_dx, edge_dy)
            if edge_length <= 1e-9:
                continue
            edge_tangent = edge_dx / edge_length, edge_dy / edge_length
            parallel_error = abs(tangent[0] * edge_tangent[1] - tangent[1] * edge_tangent[0])
            if parallel_error > 1e-6:
                continue
            relative = first[0] - start["x"], first[1] - start["y"]
            signed_offset = relative[0] * normal[0] + relative[1] * normal[1]
            if abs(abs(signed_offset) - half_thickness) > 1e-5:
                continue
            projections = [
                (point[0] - start["x"]) * tangent[0]
                + (point[1] - start["y"]) * tangent[1]
                for point in (first, last)
            ]
            overlap = max(
                0.0,
                min(source_length, max(projections)) - max(0.0, min(projections)),
            )
            if overlap <= 1e-6:
                continue
            midpoint_error = abs(sum(projections) / 2 - source_length / 2)
            candidates.append((midpoint_error + abs(edge_length - source_length), edge))
        if candidates:
            matched[primitive["id"]] = min(candidates, key=lambda item: item[0])[1]
    return matched


def _rounded_thinwall_profile(arguments: dict[str, Any]):
    """Offset a line/arc centerline into a real two-sided thin-wall profile."""
    sketch = arguments["sketch"]
    primitives = [item for item in sketch["primitives"] if not item.get("construction")]
    source_wire_builder = BRepBuilderAPI_MakeWire()
    source_edges: list[TopoDS_Shape] = []
    for primitive in primitives:
        edge = _primitive_edge(primitive, sketch.get("plane", "XY"))
        source_wire_builder.Add(edge)
        source_edges.append(edge)
    if not source_wire_builder.IsDone():
        raise RuntimeError("Rounded thin-wall centerline is disconnected")
    source_wire = source_wire_builder.Wire()
    half_thickness = float(arguments["thickness"]) / 2
    offset_wires: list[TopoDS_Shape] = []
    offset_sources: list[dict[str, TopoDS_Shape]] = []
    for distance in (half_thickness, -half_thickness):
        offset = BRepOffsetAPI_MakeOffset(source_wire, GeomAbs_Arc, True)
        offset.Perform(distance)
        offset.Build()
        if not offset.IsDone():
            raise RuntimeError("Rounded thin-wall offset construction failed")
        wire = TopoDS.Wire_s(offset.Shape())
        offset_wires.append(wire)
        wire_edges = _wire_edges(wire)
        offset_sources.append(_match_offset_line_sources(
            primitives,
            wire_edges,
            sketch.get("plane", "XY"),
            half_thickness,
        ))

    positive_edges = _wire_edges(offset_wires[0])
    negative_edges = _wire_edges(offset_wires[1])
    if not positive_edges or not negative_edges:
        raise RuntimeError("Rounded thin-wall offset produced no edges")
    closed_wire_builder = BRepBuilderAPI_MakeWire()
    for edge in positive_edges:
        closed_wire_builder.Add(edge)
    first_negative = TopoDS.Edge_s(negative_edges[0])
    last_positive = TopoDS.Edge_s(positive_edges[-1])
    last_negative = TopoDS.Edge_s(negative_edges[-1])
    closed_wire_builder.Add(BRepBuilderAPI_MakeEdge(
        _edge_last_point(last_positive), _edge_last_point(last_negative)
    ).Edge())
    for edge in reversed(negative_edges):
        closed_wire_builder.Add(TopoDS.Edge_s(TopoDS.Edge_s(edge).Reversed()))
    first_positive = TopoDS.Edge_s(positive_edges[0])
    closed_wire_builder.Add(BRepBuilderAPI_MakeEdge(
        _edge_first_point(first_negative), _edge_first_point(first_positive)
    ).Edge())
    if not closed_wire_builder.IsDone():
        raise RuntimeError("Rounded thin-wall profile wire construction failed")
    face = BRepBuilderAPI_MakeFace(closed_wire_builder.Wire(), True).Face()
    prism = BRepPrimAPI_MakePrism(face, _normal_vector(sketch.get("plane", "XY"), float(arguments["length"])))
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError("Rounded thin-wall extrusion failed")
    return prism.Shape(), offset_sources[0], positive_edges, closed_wire_builder.Wire(), prism


def _edge_first_point(edge: TopoDS_Shape) -> gp_Pnt:
    vertex = TopExp.FirstVertex_s(TopoDS.Edge_s(edge), True)
    from OCP.BRep import BRep_Tool
    return BRep_Tool.Pnt_s(vertex)


def _edge_last_point(edge: TopoDS_Shape) -> gp_Pnt:
    vertex = TopExp.LastVertex_s(TopoDS.Edge_s(edge), True)
    from OCP.BRep import BRep_Tool
    return BRep_Tool.Pnt_s(vertex)


def _prism_from_2d_outline(outline: list[tuple[float, float]], plane: str, length: float):
    points = {
        "XY": [(u, v, 0) for u, v in outline],
        "XZ": [(u, 0, v) for u, v in outline],
        "YZ": [(0, u, v) for u, v in outline],
    }[plane]
    polygon = BRepBuilderAPI_MakePolygon()
    for point in points:
        polygon.Add(gp_Pnt(*point))
    polygon.Close()
    if not polygon.IsDone():
        raise RuntimeError("Thin-wall outline wire construction failed")
    face = BRepBuilderAPI_MakeFace(polygon.Wire(), True).Face()
    axis = {"XY": gp_Vec(0, 0, length), "XZ": gp_Vec(0, length, 0), "YZ": gp_Vec(length, 0, 0)}[plane]
    return BRepPrimAPI_MakePrism(face, axis).Shape()


def _centerline_thinwall_extrude(arguments):
    sketch = arguments["sketch"]
    primitives = [item for item in sketch["primitives"] if not item.get("construction")]
    if not primitives:
        raise RuntimeError("Thin-wall centerline path is empty")
    thickness, length = float(arguments["thickness"]), float(arguments["length"])
    if thickness <= 0 or length <= 0:
        raise RuntimeError("Thin-wall thickness and extrusion length must be positive")
    plane = sketch.get("plane", "XY")
    if any(primitive["type"] == "arc" for primitive in primitives):
        return _rounded_thinwall_profile(arguments)[0]
    centerline_points = _connected_line_points(primitives)
    if centerline_points:
        outline = _thinwall_outline(centerline_points, thickness / 2)
        if outline:
            return _prism_from_2d_outline(outline, plane, length)
    if sketch.get("regions"):
        return _sketch_region_extrude(arguments)
    solids, half = [], thickness / 2
    for primitive in primitives:
        if primitive["type"] != "line":
            raise RuntimeError(f"Unsupported thin-wall centerline primitive: {primitive['type']}")
        start, end = primitive["start"], primitive["end"]
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        segment_length = math.hypot(dx, dy)
        if segment_length <= 1e-9:
            raise RuntimeError(f"Thin-wall centerline segment is degenerate: {primitive['id']}")
        nx, ny = -dy / segment_length * half, dx / segment_length * half
        corners = [(start["x"] + nx, start["y"] + ny), (end["x"] + nx, end["y"] + ny), (end["x"] - nx, end["y"] - ny), (start["x"] - nx, start["y"] - ny)]
        points = {
            "XY": [(u, v, 0) for u, v in corners],
            "XZ": [(u, 0, v) for u, v in corners],
            "YZ": [(0, u, v) for u, v in corners],
        }[plane]
        polygon = BRepBuilderAPI_MakePolygon()
        for point in points:
            polygon.Add(gp_Pnt(*point))
        polygon.Close()
        face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
        axis = {"XY": gp_Vec(0, 0, length), "XZ": gp_Vec(0, length, 0), "YZ": gp_Vec(length, 0, 0)}[plane]
        solids.append(BRepPrimAPI_MakePrism(face, axis).Shape())
    return _fuse(*solids)


def _centerline_thinwall_extrude_with_face_map(
    arguments: dict[str, Any],
    operation_id: str,
    profile_sketch_id: str,
) -> tuple[TopoDS_Shape, FaceMap]:
    sketch = arguments["sketch"]
    primitives = [item for item in sketch["primitives"] if not item.get("construction")]
    if any(primitive["type"] == "arc" for primitive in primitives):
        shape, source_edges, _positive_edges, _closed_wire, prism = _rounded_thinwall_profile(arguments)
        face_map: FaceMap = {}
        for primitive in primitives:
            source_edge = source_edges.get(primitive["id"])
            if source_edge is None or primitive["type"] != "line":
                continue
            for generated in prism.Generated(source_edge):
                for occurrence in _matching_subshapes(shape, generated, TopAbs_FACE):
                    support_face = TopoDS.Face_s(occurrence)
                    frame = _support_frame(support_face)
                    if frame is None:
                        continue
                    origin, u_direction, v_direction, normal = frame
                    _append_face_support(face_map, FaceSupport(
                        supportFace=support_face,
                        origin=origin,
                        uDirection=u_direction,
                        vDirection=v_direction,
                        normal=normal,
                        sourceEntityId=primitive["id"],
                        operationId=operation_id,
                        profileSketchId=profile_sketch_id,
                        kind="profileEdge",
                    ))
        return shape, face_map
    points = _connected_line_points(primitives)
    if not points:
        return _centerline_thinwall_extrude(arguments), {}

    thickness, length = float(arguments["thickness"]), float(arguments["length"])
    sides = _thinwall_outline_sides(points, thickness / 2)
    if sides is None:
        return _centerline_thinwall_extrude(arguments), {}
    left, right = sides
    outline = left + list(reversed(right))
    plane = sketch.get("plane", "XY")
    outline_points = {
        "XY": [(u, v, 0.0) for u, v in outline],
        "XZ": [(u, 0.0, v) for u, v in outline],
        "YZ": [(0.0, u, v) for u, v in outline],
    }[plane]
    wire_builder = BRepBuilderAPI_MakeWire()
    for start, end in zip(outline_points, [*outline_points[1:], outline_points[0]]):
        wire_builder.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*start), gp_Pnt(*end)).Edge())
    if not wire_builder.IsDone():
        raise RuntimeError("Thin-wall outline wire construction failed")
    wire = wire_builder.Wire()
    face = BRepBuilderAPI_MakeFace(wire, True).Face()
    prism = BRepPrimAPI_MakePrism(face, _normal_vector(plane, length))
    prism.Build()
    if not prism.IsDone():
        raise RuntimeError("Thin-wall outline extrusion failed")
    shape = prism.Shape()

    outline_edges: list[TopoDS_Shape] = []
    explorer = TopExp_Explorer(wire, TopAbs_EDGE)
    while explorer.More():
        outline_edges.append(explorer.Current())
        explorer.Next()

    face_map: FaceMap = {}
    for index, primitive in enumerate(primitives):
        if index >= len(outline_edges):
            break
        source_edge = outline_edges[index]
        for generated in prism.Generated(source_edge):
            for occurrence in _matching_subshapes(shape, generated, TopAbs_FACE):
                support_face = TopoDS.Face_s(occurrence)
                frame = _support_frame(support_face)
                if frame is None:
                    continue
                origin, u_direction, v_direction, normal = frame
                _append_face_support(face_map, FaceSupport(
                    supportFace=support_face,
                    origin=origin,
                    uDirection=u_direction,
                    vDirection=v_direction,
                    normal=normal,
                    sourceEntityId=primitive["id"],
                    operationId=operation_id,
                    profileSketchId=profile_sketch_id,
                    kind="profileEdge",
                ))
    return shape, face_map


def _body(operation):
    p = operation.arguments
    if operation.operator == "profile.open_profile_tube_extrude":
        sketch = p.get("sketch")
        if sketch:
            if sketch.get("profileMode") == "centerlineThinWall":
                return _centerline_thinwall_extrude(p)
            return _sketch_region_extrude(p)
        # Compatibility for callers that still provide rectangular tube
        # dimensions instead of a parameterized sketch.
        operation = type("LegacyOperation", (), {"operator": "profile.rectangular_tube_extrude", "arguments": p})()
    if operation.operator == "sketch.region_extrude":
        return _sketch_region_extrude(p)
    if operation.operator == "sketch.centerline_thinwall_extrude":
        return _centerline_thinwall_extrude(p)
    if operation.operator == "solid.revolve":
        return _sketch_revolve(p)
    if operation.operator == "solid.sweep":
        return _sketch_sweep(p)
    if operation.operator == "solid.loft":
        return _sketch_loft(p)
    if operation.operator == "sheet.bend":
        return _sheet_single_bend(p)
    length, width = p["length"], p["width"]
    thickness = p["thickness"]
    if operation.operator == "sheet.blank_extrude":
        return _box(-width / 2, -thickness / 2, 0, width, thickness, length)
    depth = p["depth"]
    if operation.operator == "profile.rectangular_tube_extrude":
        outer = _box(-width / 2, -depth / 2, 0, width, depth, length)
        inner = _box(
            -width / 2 + thickness,
            -depth / 2 + thickness,
            -1,
            width - 2 * thickness,
            depth - 2 * thickness,
            length + 2,
        )
        return BRepAlgoAPI_Cut(outer, inner).Shape()
    raise ValueError(f"Unsupported body operator: {operation.operator}")


def build_body(operation):
    return _body(operation)


def build_body_with_face_map(operation) -> tuple[TopoDS_Shape, FaceMap]:
    """Build a body and source-owned support map for supported extrusions.

    Existing callers keep using :func:`build_body`.  Operators outside the
    initial profile/region-extrusion scope still build normally and return an
    empty map, as do legacy dimension-only and centerline-offset profiles for
    which one authored edge does not identify exactly one generated face.
    """

    arguments = operation.arguments
    sketch = arguments.get("sketch")
    if (
        operation.operator in {
            "profile.open_profile_tube_extrude",
            "sketch.region_extrude",
        }
        and sketch
        and sketch.get("profileMode") != "centerlineThinWall"
    ):
        if sum(1 for region in sketch.get("regions", []) if region.get("operation") == "add") > 1:
            # Multi-region profiles use the 3-D fusion path above.  No authored
            # locator in the current profile depends on a generated support map.
            return _sketch_region_extrude(arguments), {}
        operation_id = str(getattr(operation, "id", "") or arguments.get("operationId", ""))
        profile_sketch_id = str(
            arguments.get("profileSketchId")
            or sketch.get("id")
            or getattr(operation, "profileSketchId", "")
            or "sketch.section.main"
        )
        return _sketch_region_extrude_with_face_map(
            arguments,
            operation_id,
            profile_sketch_id,
        )
    if (
        operation.operator == "profile.open_profile_tube_extrude"
        and sketch
        and sketch.get("profileMode") == "centerlineThinWall"
    ):
        operation_id = str(getattr(operation, "id", "") or arguments.get("operationId", ""))
        profile_sketch_id = str(
            arguments.get("profileSketchId")
            or sketch.get("id")
            or getattr(operation, "profileSketchId", "")
            or "sketch.section.main"
        )
        return _centerline_thinwall_extrude_with_face_map(
            arguments,
            operation_id,
            profile_sketch_id,
        )
    return build_body(operation), {}
