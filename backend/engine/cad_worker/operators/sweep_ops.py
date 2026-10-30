from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_TransitionMode,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell, BRepOffsetAPI_ThruSections
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Circle
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

from template_core.sweep_frames import path_frames, segment_start_frames

from .base_entities import _fuse, _region_wire
from ..postcheck import solid_count as _solid_count


_PATH_TOLERANCE = 1e-7
_TANGENT_DOT_TOLERANCE = 1e-7
_TAU = 2.0 * math.pi


class SweepPathConstructionError(RuntimeError):
    """An exact sweep spine could not be built without changing its meaning."""

    def __init__(self, message: str, *, code: str = "SWEEP_PATH_EXACT_WIRE_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _ExactPathSegment:
    geometry_id: str
    kind: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    start_tangent: tuple[float, float, float]
    end_tangent: tuple[float, float, float]
    record: dict[str, Any]


@dataclass(frozen=True)
class _ExactSweepPath:
    wire: Any
    segments: tuple[_ExactPathSegment, ...]
    start_vertex: Any
    closed: bool
    plane: str

    @property
    def has_arcs(self) -> bool:
        return any(segment.kind == "arc" for segment in self.segments)

    @property
    def has_line_line_corner(self) -> bool:
        joins = list(zip(self.segments, self.segments[1:]))
        if self.closed and len(self.segments) > 1:
            joins.append((self.segments[-1], self.segments[0]))
        return any(
            incoming.kind == outgoing.kind == "line"
            and _dot(incoming.end_tangent, outgoing.start_tangent) < 1.0 - _TANGENT_DOT_TOLERANCE
            for incoming, outgoing in joins
        )


def _parse_points(value: str) -> list[tuple[float, float, float]]:
    try:
        points = [tuple(float(component.strip()) for component in item.split(":")) for item in value.split(";") if item.strip()]
    except ValueError as error:
        raise RuntimeError("Path points must use x:y:z;x:y:z format") from error
    if len(points) < 2 or any(len(point) != 3 for point in points):
        raise RuntimeError("Sweep path requires at least two 3D points")
    if any(not all(math.isfinite(component) for component in point) for point in points):
        raise RuntimeError("Sweep path points must be finite")
    if any(math.dist(a, b) <= 1e-9 for a, b in zip(points, points[1:])):
        raise RuntimeError("Sweep path contains a zero-length segment")
    return points


def _dot(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return sum(a * b for a, b in zip(first, second))


def _normalize(vector: tuple[float, float, float], label: str) -> tuple[float, float, float]:
    length = math.sqrt(_dot(vector, vector))
    if not math.isfinite(length) or length <= _PATH_TOLERANCE:
        raise SweepPathConstructionError(f"Sweep path {label} must be non-zero")
    return tuple(component / length for component in vector)  # type: ignore[return-value]


def _point3(value: Any, label: str) -> tuple[float, float, float]:
    if isinstance(value, dict):
        raw = (value.get("x"), value.get("y"), value.get("z"))
    elif isinstance(value, (list, tuple)):
        raw = tuple(value)
    else:
        raw = ()
    if len(raw) != 3:
        raise SweepPathConstructionError(f"Sweep path {label} must be a 3D point")
    try:
        result = tuple(float(component) for component in raw)
    except (TypeError, ValueError) as error:
        raise SweepPathConstructionError(f"Sweep path {label} must be a finite 3D point") from error
    if not all(math.isfinite(component) for component in result):
        raise SweepPathConstructionError(f"Sweep path {label} must be a finite 3D point")
    return result  # type: ignore[return-value]


def _physical_plane(record: dict[str, Any], arguments: dict[str, Any]) -> str:
    plane = str(record.get("mappedPlane") or record.get("plane") or arguments.get("pathPlane") or "XZ")
    if plane not in {"XY", "XZ", "YZ"}:
        raise SweepPathConstructionError(f"Unsupported exact sweep path plane: {plane}")
    return plane


def _plane_axes(plane: str) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Return the parameter U/V axes and their right-handed plane normal."""

    if plane == "XY":
        return (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)
    if plane == "XZ":
        return (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
    if plane == "YZ":
        return (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    raise SweepPathConstructionError(f"Unsupported exact sweep path plane: {plane}")


def _circle_point(
    center: tuple[float, float, float],
    radius: float,
    angle: float,
    plane: str,
) -> tuple[float, float, float]:
    u_axis, v_axis, _normal = _plane_axes(plane)
    cosine, sine = math.cos(angle), math.sin(angle)
    return tuple(
        center[axis] + radius * (cosine * u_axis[axis] + sine * v_axis[axis])
        for axis in range(3)
    )  # type: ignore[return-value]


def _arc_tangent(angle: float, sweep: float, plane: str) -> tuple[float, float, float]:
    u_axis, v_axis, _normal = _plane_axes(plane)
    sign = 1.0 if sweep > 0.0 else -1.0
    return tuple(
        sign * (-math.sin(angle) * u_axis[axis] + math.cos(angle) * v_axis[axis])
        for axis in range(3)
    )  # type: ignore[return-value]


def _fallback_arc_sweep(record: dict[str, Any], start_angle: float, end_angle: float) -> float:
    direction = str(record.get("sweepDirection", "ccw"))
    if direction not in {"cw", "ccw"}:
        raise SweepPathConstructionError(
            f"Exact arc {record.get('geometryId') or '<unknown>'} has invalid sweepDirection: {direction}",
            code="SWEEP_PATH_EXACT_ARC_FAILED",
        )
    ccw_delta = (end_angle - start_angle) % _TAU
    if ccw_delta <= 1e-12:
        return 0.0
    sweep = ccw_delta if direction == "ccw" else ccw_delta - _TAU
    if bool(record.get("largeArc", False)) and abs(sweep) < math.pi:
        sweep = -math.copysign(_TAU - abs(sweep), sweep)
    return sweep


def _parse_exact_path_segments(arguments: dict[str, Any]) -> list[_ExactPathSegment] | None:
    raw_segments = arguments.get("pathSegments")
    sampled_kinds = [str(kind) for kind in (arguments.get("pathSegmentKinds") or [])]
    sampled_contains_arc = "arc" in sampled_kinds
    if raw_segments is None:
        if sampled_contains_arc:
            raise SweepPathConstructionError(
                "Exact arc metadata is missing; sampled pathPoints cannot be treated as an exact circular path",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        return None
    if not isinstance(raw_segments, (list, tuple)):
        raise SweepPathConstructionError("Sweep pathSegments must be a list")
    if not raw_segments:
        if sampled_contains_arc:
            raise SweepPathConstructionError(
                "Exact arc metadata is empty; sampled pathPoints cannot be treated as an exact circular path",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        return None
    if not all(isinstance(record, dict) for record in raw_segments):
        raise SweepPathConstructionError("Every exact sweep path segment must be an object")
    if not all("start" in record and "end" in record for record in raw_segments):
        if sampled_contains_arc or any(str(record.get("geometryType", "line")) == "arc" for record in raw_segments):
            raise SweepPathConstructionError(
                "Exact arc metadata lacks start/end geometry; sampled chords are only a compatibility fallback",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        return None

    parsed: list[_ExactPathSegment] = []
    physical_plane: str | None = None
    for index, source in enumerate(raw_segments):
        record = dict(source)
        geometry_id = str(record.get("geometryId") or f"segment[{index}]")
        kind = str(record.get("geometryType", "line"))
        if kind not in {"line", "arc"}:
            raise SweepPathConstructionError(f"Unsupported exact sweep path segment type: {kind}")
        start = _point3(record.get("start"), f"segment {geometry_id} start")
        end = _point3(record.get("end"), f"segment {geometry_id} end")
        if math.dist(start, end) <= _PATH_TOLERANCE:
            raise SweepPathConstructionError(f"Exact sweep path segment {geometry_id} has zero length")
        segment_plane = _physical_plane(record, arguments)
        if physical_plane is None:
            physical_plane = segment_plane
        elif physical_plane != segment_plane:
            raise SweepPathConstructionError(
                f"Exact sweep path mixes physical planes {physical_plane} and {segment_plane}"
            )

        if kind == "line":
            tangent = _normalize(tuple(b - a for a, b in zip(start, end)), f"line {geometry_id} tangent")
            parsed.append(_ExactPathSegment(geometry_id, kind, start, end, tangent, tangent, record))
            continue

        try:
            center = _point3(record.get("center"), f"arc {geometry_id} center")
            radius = float(record.get("radius"))
            start_angle = float(record.get("startAngle"))
            end_angle = float(record.get("endAngle"))
        except (TypeError, ValueError) as error:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} requires finite center, radius, startAngle, and endAngle",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            ) from error
        if not all(math.isfinite(value) for value in (radius, start_angle, end_angle)) or radius <= _PATH_TOLERANCE:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} requires a positive radius and finite angles",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        raw_sweep = record.get("sweepAngle")
        try:
            sweep = float(raw_sweep) if raw_sweep is not None else _fallback_arc_sweep(record, start_angle, end_angle)
        except (TypeError, ValueError) as error:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} has an invalid sweepAngle",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            ) from error
        if not math.isfinite(sweep) or abs(sweep) <= 1e-12 or abs(sweep) >= _TAU - 1e-12:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} sweepAngle must be non-zero and less than 2*pi",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        endpoint_angle_error = abs(math.remainder(start_angle + sweep - end_angle, _TAU))
        if endpoint_angle_error > 1e-8:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} sweepAngle does not terminate at endAngle",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        expected_start = _circle_point(center, radius, start_angle, segment_plane)
        expected_end = _circle_point(center, radius, start_angle + sweep, segment_plane)
        endpoint_tolerance = max(_PATH_TOLERANCE, radius * 1e-10)
        if math.dist(start, expected_start) > endpoint_tolerance or math.dist(end, expected_end) > endpoint_tolerance:
            raise SweepPathConstructionError(
                f"Exact arc {geometry_id} endpoints do not lie on its parameterized circle",
                code="SWEEP_PATH_EXACT_ARC_FAILED",
            )
        record["_physicalPlane"] = segment_plane
        record["_center3d"] = center
        record["_radius"] = radius
        record["_startAngle"] = start_angle
        record["_sweepAngle"] = sweep
        parsed.append(
            _ExactPathSegment(
                geometry_id,
                kind,
                start,
                end,
                _arc_tangent(start_angle, sweep, segment_plane),
                _arc_tangent(start_angle + sweep, sweep, segment_plane),
                record,
            )
        )

    for index, (incoming, outgoing) in enumerate(zip(parsed, parsed[1:]), start=1):
        gap = math.dist(incoming.end, outgoing.start)
        if gap > _PATH_TOLERANCE:
            raise SweepPathConstructionError(
                f"Exact sweep path is disconnected before segment {index}: gap={gap:.9g}"
            )
        if incoming.kind != "line" or outgoing.kind != "line":
            alignment = max(-1.0, min(1.0, _dot(incoming.end_tangent, outgoing.start_tangent)))
            if alignment < 1.0 - _TANGENT_DOT_TOLERANCE:
                angle = math.degrees(math.acos(alignment))
                raise SweepPathConstructionError(
                    f"Sweep path tangent discontinuity between {incoming.geometry_id} and {outgoing.geometry_id}: {angle:.6g} degrees; "
                    "请执行“修复为相切”后重试。当前默认不允许把不相切路径当作相切路径。",
                    code="SWEEP_PATH_TANGENT_DISCONTINUITY",
                )
    if parsed and math.dist(parsed[-1].end, parsed[0].start) <= _PATH_TOLERANCE:
        incoming, outgoing = parsed[-1], parsed[0]
        if incoming.kind != "line" or outgoing.kind != "line":
            alignment = max(-1.0, min(1.0, _dot(incoming.end_tangent, outgoing.start_tangent)))
            if alignment < 1.0 - _TANGENT_DOT_TOLERANCE:
                angle = math.degrees(math.acos(alignment))
                raise SweepPathConstructionError(
                    f"Closed sweep path tangent discontinuity between {incoming.geometry_id} and {outgoing.geometry_id}: {angle:.6g} degrees; "
                    "请执行“修复为相切”后重试。当前默认不允许把不相切路径当作相切路径。",
                    code="SWEEP_PATH_TANGENT_DISCONTINUITY",
                )
    return parsed


def _make_exact_arc_edge(segment: _ExactPathSegment, start_vertex: Any, end_vertex: Any):
    record = segment.record
    plane = str(record["_physicalPlane"])
    center = record["_center3d"]
    radius = float(record["_radius"])
    start_angle = float(record["_startAngle"])
    sweep = float(record["_sweepAngle"])
    u_axis, _v_axis, normal = _plane_axes(plane)
    circle = gp_Circ(
        gp_Ax2(gp_Pnt(*center), gp_Dir(*normal), gp_Dir(*u_axis)),
        radius,
    )
    try:
        if sweep > 0.0:
            arc_builder = GC_MakeArcOfCircle(circle, start_angle, start_angle + sweep, True)
        else:
            # OCC's false sense reverses both the parameter interval and edge
            # direction, so pass the destination first to retain start -> end.
            arc_builder = GC_MakeArcOfCircle(circle, start_angle + sweep, start_angle, False)
        if not arc_builder.IsDone():
            raise RuntimeError(f"GC status={arc_builder.Status()}")
        edge_builder = BRepBuilderAPI_MakeEdge(arc_builder.Value(), start_vertex, end_vertex)
        if not edge_builder.IsDone():
            raise RuntimeError(f"edge status={edge_builder.Error()}")
        edge = edge_builder.Edge()
        if BRepAdaptor_Curve(edge).GetType() != GeomAbs_Circle:
            raise RuntimeError("OpenCascade returned a non-circular edge")
        return edge
    except Exception as error:
        raise SweepPathConstructionError(
            f"OpenCascade exact arc construction failed for {segment.geometry_id}: {error}",
            code="SWEEP_PATH_EXACT_ARC_FAILED",
        ) from error


def _build_exact_sweep_path(arguments: dict[str, Any]) -> _ExactSweepPath | None:
    segments = _parse_exact_path_segments(arguments)
    if not segments:
        return None
    closed = math.dist(segments[-1].end, segments[0].start) <= _PATH_TOLERANCE
    first_vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*segments[0].start)).Vertex()
    current_vertex = first_vertex
    wire_builder = BRepBuilderAPI_MakeWire()
    for index, segment in enumerate(segments):
        is_closing_edge = closed and index == len(segments) - 1
        end_vertex = first_vertex if is_closing_edge else BRepBuilderAPI_MakeVertex(gp_Pnt(*segment.end)).Vertex()
        try:
            if segment.kind == "arc":
                edge = _make_exact_arc_edge(segment, current_vertex, end_vertex)
            else:
                edge_builder = BRepBuilderAPI_MakeEdge(current_vertex, end_vertex)
                if not edge_builder.IsDone():
                    raise RuntimeError(f"edge status={edge_builder.Error()}")
                edge = edge_builder.Edge()
            wire_builder.Add(edge)
            if not wire_builder.IsDone():
                raise RuntimeError(f"wire status={wire_builder.Error()}")
        except SweepPathConstructionError:
            raise
        except Exception as error:
            raise SweepPathConstructionError(
                f"OpenCascade wire construction failed at segment {segment.geometry_id}: {error}"
            ) from error
        current_vertex = end_vertex
    try:
        wire = wire_builder.Wire()
    except Exception as error:
        raise SweepPathConstructionError(f"OpenCascade exact sweep wire construction failed: {error}") from error
    return _ExactSweepPath(
        wire=wire,
        segments=tuple(segments),
        start_vertex=first_vertex,
        closed=closed,
        plane=str(segments[0].record.get("_physicalPlane") or _physical_plane(segments[0].record, arguments)),
    )


def _build_polyline_wire(points: list[tuple[float, float, float]]):
    wire_builder = BRepBuilderAPI_MakeWire()
    current_vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*points[0])).Vertex()
    for end in points[1:]:
        end_vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*end)).Vertex()
        wire_builder.Add(BRepBuilderAPI_MakeEdge(current_vertex, end_vertex).Edge())
        current_vertex = end_vertex
    if not wire_builder.IsDone():
        raise RuntimeError("Sweep path wire construction failed")
    return wire_builder.Wire()


def _build_sweep_path_wire(arguments: dict[str, Any]):
    """Build the preferred exact wire, retaining pathPoints for line fallback."""

    exact_path = _build_exact_sweep_path(arguments)
    if exact_path is not None:
        return exact_path.wire
    return _build_polyline_wire(_parse_points(str(arguments.get("pathPoints", ""))))


# Public spelling for integrations that avoid importing underscored helpers.
build_sweep_path_wire = _build_sweep_path_wire


def _map_path_tangent(value, plane: str) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        values = tuple(float(component) for component in value)
    except (TypeError, ValueError, IndexError):
        return None
    if len(values) == 3:
        return values
    if len(values) != 2:
        return None
    u, v = values
    if plane in {"XY", "XZ"}:
        return (u, 0.0, v)
    if plane == "YZ":
        return (0.0, u, v)
    return None


def _profile_support_along(sketch, regions, frame, direction) -> float:
    if not frame:
        return 0.0
    _origin, e1, e2, _tangent = frame
    dx, dy, dz = direction
    c1 = e1[0] * dx + e1[1] * dy + e1[2] * dz
    c2 = e2[0] * dx + e2[1] * dy + e2[2] * dz
    primitives = {item["id"]: item for item in sketch.get("primitives", []) if not item.get("construction")}
    support = 0.0
    for region in regions:
        for reference in region.get("boundaryRefs", []):
            primitive = primitives.get(reference)
            if not primitive:
                continue
            kind = primitive.get("type")
            if kind == "line":
                for point in (primitive.get("start"), primitive.get("end")):
                    if point is None:
                        continue
                    value = float(point.get("x", 0.0)) * c1 + float(point.get("y", 0.0)) * c2
                    support = max(support, abs(value))
            elif kind == "circle":
                center = primitive.get("center") or {}
                center_value = float(center.get("x", 0.0)) * c1 + float(center.get("y", 0.0)) * c2
                radius = abs(float(primitive.get("radius") or 0.0))
                support = max(support, abs(center_value) + radius * math.hypot(c1, c2))
            elif kind == "arc":
                center = primitive.get("center") or {}
                center_value = float(center.get("x", 0.0)) * c1 + float(center.get("y", 0.0)) * c2
                radius = abs(float(primitive.get("radius") or 0.0))
                support = max(support, abs(center_value) + radius * math.hypot(c1, c2))
    return support


def _corner_extension_distances(sketch, regions, points, frames, segment_kinds=None) -> list[float]:
    result = [0.0] * len(frames)
    kinds = list(segment_kinds or ["line"] * max(0, len(points) - 1))
    if len(points) < 3 or not frames:
        return result
    corners = list(range(1, len(points) - 1))
    closed = len(points) > 2 and math.dist(points[0], points[-1]) <= 1e-9
    if closed:
        corners.append(0)
    for corner in corners:
        incoming_index = len(frames) - 1 if corner == 0 else corner - 1
        outgoing_index = 0 if corner == 0 else corner
        incoming_kind = kinds[incoming_index] if incoming_index < len(kinds) else "line"
        outgoing_kind = kinds[outgoing_index] if outgoing_index < len(kinds) else "line"
        if incoming_kind != "line" or outgoing_kind != "line":
            continue
        incoming = frames[incoming_index][3]
        outgoing = frames[outgoing_index][3]
        cross = (
            incoming[1] * outgoing[2] - incoming[2] * outgoing[1],
            incoming[2] * outgoing[0] - incoming[0] * outgoing[2],
            incoming[0] * outgoing[1] - incoming[1] * outgoing[0],
        )
        cross_length = math.sqrt(sum(value * value for value in cross))
        if cross_length <= 1e-9:
            continue
        tangent_dot = sum(incoming[axis] * outgoing[axis] for axis in range(3))
        normal_component = tuple(incoming[axis] - tangent_dot * outgoing[axis] for axis in range(3))
        normal_length = math.sqrt(sum(value * value for value in normal_component))
        if normal_length <= 1e-9:
            continue
        miter_normal = tuple(value / normal_length for value in normal_component)
        support = _profile_support_along(sketch, regions, frames[outgoing_index], miter_normal)
        if support <= 1e-9:
            continue
        distance = support / cross_length
        segment_start = len(points) - 2 if corner == 0 else corner - 1
        segment_length = math.dist(points[segment_start], points[corner])
        result[corner] = min(distance, max(0.0, segment_length * 0.49))
    return result


def _refine_sweep_shape(shape):
    try:
        refined_builder = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        refined_builder.SetLinearTolerance(1e-6)
        refined_builder.SetAngularTolerance(1e-6)
        refined_builder.Build()
        refined = refined_builder.Shape()
        if not refined.IsNull() and BRepCheck_Analyzer(refined).IsValid() and _solid_count(refined) == _solid_count(shape):
            return refined
    except Exception:
        pass
    return shape


def _exact_initial_frame(exact_path: _ExactSweepPath, orientation_mode: str):
    origin = exact_path.segments[0].start
    tangent = exact_path.segments[0].start_tangent
    next_point = tuple(origin[axis] + tangent[axis] for axis in range(3))
    try:
        return path_frames(
            [origin, next_point],
            orientation_mode,
            station_tangent_overrides=[tangent, tangent],
        )[0]
    except ValueError as error:
        raise SweepPathConstructionError(str(error), code="SWEEP_PATH_ORIENTATION_FAILED") from error


def _sweep_region_on_exact_path(
    sketch,
    region,
    exact_path: _ExactSweepPath,
    orientation_mode: str,
):
    frame = _exact_initial_frame(exact_path, orientation_mode)
    pipe = BRepOffsetAPI_MakePipeShell(exact_path.wire)
    try:
        if orientation_mode == "minimumTwist":
            pipe.SetMode(False)
        elif orientation_mode == "followPath":
            pipe.SetMode(True)
        elif orientation_mode == "fixedWorld":
            _u_axis, _v_axis, plane_normal = _plane_axes(exact_path.plane)
            # Sweep paths are planar.  A fixed binormal keeps the section's
            # world-referenced roll while OCC still evaluates the exact curve
            # tangent and keeps every section normal to the spine.
            pipe.SetMode(gp_Dir(*plane_normal))
        else:
            raise SweepPathConstructionError(
                f"Unsupported sweep orientationMode: {orientation_mode}",
                code="SWEEP_PATH_ORIENTATION_FAILED",
            )
        pipe.SetTransitionMode(
            BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner
            if exact_path.has_line_line_corner
            else BRepBuilderAPI_TransitionMode.BRepBuilderAPI_Transformed
        )
        pipe.Add(_region_wire(sketch, region, frame=frame), False, False)
        pipe.Build()
        if not pipe.IsDone() or not pipe.MakeSolid():
            raise RuntimeError(f"PipeShell status={pipe.GetStatus()}")
        candidate = pipe.Shape()
        if candidate.IsNull() or not BRepCheck_Analyzer(candidate).IsValid():
            raise RuntimeError("PipeShell produced an invalid B-Rep")
        if _solid_count(candidate) != 1:
            raise RuntimeError(f"PipeShell produced {_solid_count(candidate)} solids")
        return candidate
    except SweepPathConstructionError:
        raise
    except Exception as error:
        raise SweepPathConstructionError(
            f"Exact sweep construction failed for region {region['id']}: {error}",
            code="SWEEP_PATH_EXACT_SWEEP_FAILED",
        ) from error


def _sweep_region(
    sketch,
    region,
    points,
    frames,
    corner_extensions=None,
    segment_kinds=None,
    station_frames=None,
    exact_path: _ExactSweepPath | None = None,
    orientation_mode: str = "minimumTwist",
):
    solids = []
    corner_extensions = list(corner_extensions or [])
    kinds = list(segment_kinds or ["line"] * max(0, len(points) - 1))

    if exact_path is not None and exact_path.has_arcs:
        return _sweep_region_on_exact_path(sketch, region, exact_path, orientation_mode)

    has_real_line_corner = any(kinds[index - 1] == "line" and kinds[index] == "line" for index in range(1, min(len(kinds), len(points) - 1)))
    if "arc" in kinds and not has_real_line_corner:
        station_frames = list(station_frames or frames)
        if len(station_frames) < len(points) and station_frames:
            last = station_frames[-1]
            station_frames.append((points[-1], last[1], last[2], last[3]))
        section = BRepOffsetAPI_ThruSections(True, bool(False), 1e-6)
        try:
            for point, frame in zip(points, station_frames):
                station_frame = (point, frame[1], frame[2], frame[3])
                section.AddWire(_region_wire(sketch, region, frame=station_frame))
            section.CheckCompatibility(True)
            section.Build()
            if not section.IsDone():
                raise RuntimeError("ThruSections did not produce a solid")
            candidate = section.Shape()
            if not BRepCheck_Analyzer(candidate).IsValid():
                raise RuntimeError("ThruSections produced an invalid solid")
            return candidate
        except Exception as error:
            raise RuntimeError(f"Sweep construction failed for region {region['id']} arc path: {error}") from error

    for index, (start, end, frame) in enumerate(zip(points, points[1:], frames)):
        start_for_spine = start
        end_for_spine = end
        frame_for_profile = frame
        corner_index = index + 1
        if index == len(frames) - 1 and len(points) > 2 and math.dist(points[0], points[-1]) <= 1e-9:
            corner_index = 0
        if corner_index < len(corner_extensions):
            extension = max(0.0, float(corner_extensions[corner_index]))
            if extension > 1e-9:
                tangent = frame[3]
                end_for_spine = tuple(end[axis] + tangent[axis] * extension for axis in range(3))
                if math.dist(start, end_for_spine) <= 1e-9:
                    end_for_spine = end
        edge_builder = BRepBuilderAPI_MakeWire()
        edge_builder.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*start_for_spine), gp_Pnt(*end_for_spine)).Edge())
        spine = edge_builder.Wire()
        try:
            pipe = BRepOffsetAPI_MakePipeShell(spine)
            pipe.SetTransitionMode(BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RightCorner)
            pipe.Add(_region_wire(sketch, region, frame=frame_for_profile), False, True)
            pipe.Build()
            if not pipe.IsDone() or not pipe.MakeSolid():
                raise RuntimeError("PipeShell did not produce a solid")
            candidate = pipe.Shape()
            if not BRepCheck_Analyzer(candidate).IsValid():
                raise RuntimeError("PipeShell produced an invalid solid")
            solids.append(candidate)
        except Exception as error:
            raise RuntimeError(f"Sweep construction failed for region {region['id']} segment {index}: {error}") from error
    if not solids:
        raise RuntimeError(f"Sweep construction failed for region {region['id']}")
    return _fuse(*solids)


def _sketch_sweep(arguments):
    sketch = arguments["sketch"]
    points = _parse_points(str(arguments.get("pathPoints", "")))
    supported = {"profileAnchor": "sketch.origin", "orientationMode": "minimumTwist", "scaleMode": "constant", "twistMode": "none", "cornerMode": "right"}
    for key, expected in supported.items():
        if arguments.get(key, expected) != expected:
            if key == "orientationMode" and arguments.get(key) in {"followPath", "fixedWorld"}:
                continue
            raise RuntimeError(f"Unsupported sweep {key}: {arguments.get(key)}")
    exact_path = _build_exact_sweep_path(arguments)
    segment_tangent_overrides = arguments.get("pathSegmentTangents")
    if segment_tangent_overrides is not None:
        if not isinstance(segment_tangent_overrides, (list, tuple)) or len(segment_tangent_overrides) != len(points) - 1:
            raise RuntimeError("Sweep path tangent metadata does not match pathPoints")
        plane = str(arguments.get("pathPlane", sketch.get("plane", "XY")))
        mapped_tangents = []
        for value in segment_tangent_overrides:
            if not isinstance(value, (list, tuple)):
                raise RuntimeError("Sweep path tangent metadata is invalid")
            tangent = _map_path_tangent(value, plane)
            if tangent is None:
                raise RuntimeError(f"Unsupported sweep path plane: {plane}")
            if not all(math.isfinite(component) for component in tangent):
                raise RuntimeError("Sweep path tangent metadata must be finite")
            mapped_tangents.append(tangent)
    else:
        mapped_tangents = None
    station_tangent_overrides = arguments.get("pathStationTangents")
    if station_tangent_overrides is not None:
        if not isinstance(station_tangent_overrides, (list, tuple)) or len(station_tangent_overrides) != len(points):
            raise RuntimeError("Sweep path station tangent metadata does not match pathPoints")
        plane = str(arguments.get("pathPlane", sketch.get("plane", "XY")))
        mapped_station_tangents = []
        for value in station_tangent_overrides:
            if value is None:
                mapped_station_tangents.append(None)
                continue
            if not isinstance(value, (list, tuple)):
                raise RuntimeError("Sweep path station tangent metadata is invalid")
            tangent = _map_path_tangent(value, plane)
            if tangent is None:
                raise RuntimeError(f"Unsupported sweep path plane: {plane}")
            if not all(math.isfinite(component) for component in tangent):
                raise RuntimeError("Sweep path station tangent metadata must be finite")
            mapped_station_tangents.append(tangent)
        if any(item is None for item in mapped_station_tangents):
            mapped_station_tangents = None
    else:
        mapped_station_tangents = None
    try:
        station_frames = path_frames(points, str(arguments.get("orientationMode", "minimumTwist")), station_tangent_overrides=mapped_station_tangents)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    try:
        frames = segment_start_frames(points, str(arguments.get("orientationMode", "minimumTwist")), segment_tangent_overrides=mapped_tangents)
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    segment_kinds = arguments.get("pathSegmentKinds")
    if segment_kinds is not None and not isinstance(segment_kinds, (list, tuple)):
        raise RuntimeError("Sweep path segment metadata must be a list")
    segment_kinds = list(segment_kinds or ["line"] * max(0, len(points) - 1))
    if len(segment_kinds) != len(points) - 1:
        raise RuntimeError("Sweep path segment metadata does not match pathPoints")
    unsupported_kinds = {str(kind) for kind in segment_kinds} - {"line", "arc"}
    if unsupported_kinds:
        raise RuntimeError("Unsupported sweep path segment type")
    if "arc" in segment_kinds and (exact_path is None or not exact_path.has_arcs):
        raise SweepPathConstructionError(
            "Exact circular path construction is required; sampled pathPoints are not an exact arc",
            code="SWEEP_PATH_EXACT_ARC_FAILED",
        )
    additive_regions = [region for region in sketch["regions"] if region["operation"] == "add"]
    corner_extensions = _corner_extension_distances(sketch, additive_regions, points, frames, segment_kinds)
    orientation_mode = str(arguments.get("orientationMode", "minimumTwist"))
    additive = [
        _sweep_region(
            sketch,
            region,
            points,
            frames,
            corner_extensions,
            segment_kinds,
            station_frames,
            exact_path,
            orientation_mode,
        )
        for region in sketch["regions"]
        if region["operation"] == "add"
    ]
    if not additive:
        raise RuntimeError("Sweep requires at least one additive region")
    result = _fuse(*additive)
    for region in sketch["regions"]:
        if region["operation"] == "subtract":
            hole_extensions = _corner_extension_distances(sketch, [region], points, frames, segment_kinds)
            cut = BRepAlgoAPI_Cut(
                result,
                _sweep_region(
                    sketch,
                    region,
                    points,
                    frames,
                    hole_extensions,
                    segment_kinds,
                    station_frames,
                    exact_path,
                    orientation_mode,
                ),
            )
            cut.Build()
            if not cut.IsDone():
                raise RuntimeError(f"Sweep inner-region subtraction failed for {region['id']}")
            result = cut.Shape()
    return _refine_sweep_shape(result)
