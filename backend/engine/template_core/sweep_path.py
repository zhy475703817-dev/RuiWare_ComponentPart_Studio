"""Topology validation and ordering for sweep paths.

The editor and the stage validator both deal in the same small contract: a
path is a graph of directed line/arc edges.  This module deliberately does
not solve sketch constraints or generate 3-D geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .sweep_path_sampling import (
    ARC_ANGLE_EPSILON,
    arc_endpoints,
    arc_point,
    computed_arc_endpoints,
    geometry_arc_angles,
    geometry_arc_sweep_angle,
    sample_geometry_points,
)

PATH_ENDPOINT_EPSILON = 0.05
_ANGLE_EPSILON = 1e-6


@dataclass(frozen=True)
class Endpoint:
    x: float
    y: float


@dataclass(frozen=True)
class Edge:
    index: int
    geometry: Any
    start: Endpoint
    end: Endpoint
    start_node: int = -1
    end_node: int = -1


def _distance(a: Endpoint, b: Endpoint) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _point(value: Any) -> Endpoint | None:
    if value is None:
        return None
    try:
        values = tuple(value)
        if len(values) < 2:
            return None
        result = Endpoint(float(values[0]), float(values[1]))
        if not (math.isfinite(result.x) and math.isfinite(result.y)):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _value(geometry: Any, name: str, default: Any = None) -> Any:
    if isinstance(geometry, dict):
        return geometry.get(name, default)
    return getattr(geometry, name, default)


def _finite_angle(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _endpoints(geometry: Any) -> tuple[Endpoint, Endpoint] | None:
    kind = _value(geometry, "geometryType")
    if kind == "arc":
        # Arc endpoints are defined by center/radius/angles.  Explicit snap
        # coordinates, when present, are retained verbatim by the sampler;
        # validation below compares them against this parameter evaluation.
        parameter_endpoints = arc_endpoints(geometry)
        if parameter_endpoints is None:
            return None
        return Endpoint(*parameter_endpoints[0]), Endpoint(*parameter_endpoints[1])
    points = _value(geometry, "points", None) or []
    start = _point(_value(geometry, "start"))
    end = _point(_value(geometry, "end"))
    if start is None and points:
        start = _point(points[0])
    if end is None and points:
        end = _point(points[-1])
    if start is None or end is None:
        return None
    return start, end


def _arc_point(geometry: Any, angle: float) -> Endpoint | None:
    value = arc_point(_value(geometry, "center"), _value(geometry, "radius"), angle)
    if value is None:
        return None
    return Endpoint(*value)


def _arc_sweep(geometry: Any) -> float | None:
    return geometry_arc_sweep_angle(geometry)


def _polyline(geometry: Any, edge: Edge) -> list[Endpoint]:
    try:
        values = sample_geometry_points(geometry)
    except (TypeError, ValueError):
        return [edge.start, edge.end]
    return [Endpoint(*point) for point in values] or [edge.start, edge.end]


def _orientation(a: Endpoint, b: Endpoint, c: Endpoint) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _on_segment(a: Endpoint, b: Endpoint, p: Endpoint) -> bool:
    return min(a.x, b.x) - PATH_ENDPOINT_EPSILON <= p.x <= max(a.x, b.x) + PATH_ENDPOINT_EPSILON and min(a.y, b.y) - PATH_ENDPOINT_EPSILON <= p.y <= max(a.y, b.y) + PATH_ENDPOINT_EPSILON


def _segments_intersect(a: Endpoint, b: Endpoint, c: Endpoint, d: Endpoint) -> bool:
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    eps = 1e-8
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)):
        return True
    return (abs(o1) <= eps and _on_segment(a, b, c)) or (abs(o2) <= eps and _on_segment(a, b, d)) or (abs(o3) <= eps and _on_segment(c, d, a)) or (abs(o4) <= eps and _on_segment(c, d, b))


def _polyline_intersects(first: list[Endpoint], second: list[Endpoint]) -> bool:
    return any(_segments_intersect(a, b, c, d) for a, b in zip(first, first[1:]) for c, d in zip(second, second[1:]))


def _same_line(a: Edge, b: Edge) -> bool:
    return (_distance(a.start, b.start) <= PATH_ENDPOINT_EPSILON and _distance(a.end, b.end) <= PATH_ENDPOINT_EPSILON) or (_distance(a.start, b.end) <= PATH_ENDPOINT_EPSILON and _distance(a.end, b.start) <= PATH_ENDPOINT_EPSILON)


def _same_arc(a: Edge, b: Edge) -> bool:
    ga, gb = a.geometry, b.geometry
    ca, cb = _point(_value(ga, "center")), _point(_value(gb, "center"))
    ra, rb = _value(ga, "radius"), _value(gb, "radius")
    try:
        radius_delta = abs(float(ra) - float(rb))
    except (TypeError, ValueError):
        return False
    if ca is None or cb is None or _distance(ca, cb) > PATH_ENDPOINT_EPSILON or radius_delta > PATH_ENDPOINT_EPSILON:
        return False
    # Direction is part of the authored arc identity.  Two arcs traversing
    # opposite ways over the same circle are intentionally not duplicates:
    # reversing one may be a legitimate return path and has different tangent
    # semantics for sweep orientation.
    if str(_value(ga, "sweepDirection", "ccw")) != str(_value(gb, "sweepDirection", "ccw")):
        return False
    sa, sb = _arc_sweep(ga), _arc_sweep(gb)
    if sa is None or sb is None or abs(sa - sb) > _ANGLE_EPSILON:
        return False
    # Compare both parameter angles and evaluated endpoints.  Modulo 2pi
    # equality handles equivalent angle encodings such as 0 and 2*pi.
    angles_a = geometry_arc_angles(ga)
    angles_b = geometry_arc_angles(gb)
    if angles_a is None or angles_b is None:
        return False

    def angle_equal(first: float, second: float) -> bool:
        return abs((first - second + math.pi) % (2 * math.pi) - math.pi) <= _ANGLE_EPSILON

    return (
        angle_equal(angles_a[0], angles_b[0])
        and angle_equal(angles_a[1], angles_b[1])
        and bool(_value(ga, "largeArc", False)) == bool(_value(gb, "largeArc", False))
        and _distance(a.start, b.start) <= PATH_ENDPOINT_EPSILON
        and _distance(a.end, b.end) <= PATH_ENDPOINT_EPSILON
    )


def _diagnostic(code: str, path: str, message: str, geometry_ids: list[str] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"severity": "error", "code": code, "path": path, "message": message}
    if geometry_ids:
        item["geometryIds"] = geometry_ids
    return item


def validate_sweep_path(path: Any) -> dict[str, Any]:
    """Return diagnostics, graph-derived order, and a compatible start ref."""
    diagnostics: list[dict[str, Any]] = []
    geometries = list(_value(path, "geometry", None) or [])
    if not geometries:
        return {"valid": False, "diagnostics": [_diagnostic("SWEEP_PATH_EMPTY", "sweepPath.geometry", "请至少绘制一条扫掠路径图元。")], "ordered": [], "startEndpointRef": None}
    edges: list[Edge] = []
    for index, geometry in enumerate(geometries):
        geometry_id = str(_value(geometry, "id", index))
        kind = _value(geometry, "geometryType", None)
        if kind not in {"line", "arc"}:
            diagnostics.append(_diagnostic("SWEEP_PATH_ILLEGAL_GEOMETRY", f"sweepPath.geometry.{geometry_id}", "扫掠路径只允许直线和圆弧图元。", [geometry_id]))
            continue
        if kind == "arc":
            center = _point(_value(geometry, "center"))
            radius_value = _value(geometry, "radius")
            try:
                radius = float(radius_value)
            except (TypeError, ValueError):
                radius = float("nan")
            start_angle = _value(geometry, "startAngle")
            end_angle = _value(geometry, "endAngle")
            direction = _value(geometry, "sweepDirection", "ccw")
            sweep = _arc_sweep(geometry)
            valid_arc = (
                center is not None
                and math.isfinite(radius)
                and radius > 0
                and _finite_angle(start_angle)
                and _finite_angle(end_angle)
                and direction in {"cw", "ccw"}
                and sweep is not None
                and abs(sweep) > ARC_ANGLE_EPSILON
            )
            if not valid_arc:
                code = "SWEEP_PATH_ZERO_LENGTH" if sweep is not None and abs(sweep) <= ARC_ANGLE_EPSILON else "SWEEP_PATH_GEOMETRY_INVALID"
                message = (
                    "圆弧路径长度不能为零。"
                    if code == "SWEEP_PATH_ZERO_LENGTH"
                    else "圆弧必须包含有效圆心、正半径、起止角和扫掠方向（cw/ccw）。"
                )
                diagnostics.append(_diagnostic(code, f"sweepPath.geometry.{geometry_id}", message, [geometry_id]))
                continue
            parameter_endpoints = computed_arc_endpoints(geometry)
            if parameter_endpoints is None:
                diagnostics.append(_diagnostic("SWEEP_PATH_GEOMETRY_INVALID", f"sweepPath.geometry.{geometry_id}", "圆弧端点无法根据参数计算。", [geometry_id]))
                continue
            # Explicit endpoints are optional, but when supplied they must
            # agree with center/radius/angle evaluation so snapped joins do
            # not hide malformed parameter data.
            expected_start = _point(_value(geometry, "start"))
            expected_end = _point(_value(geometry, "end"))
            if (
                expected_start is not None
                and _distance(expected_start, Endpoint(*parameter_endpoints[0])) > PATH_ENDPOINT_EPSILON
            ) or (
                expected_end is not None
                and _distance(expected_end, Endpoint(*parameter_endpoints[1])) > PATH_ENDPOINT_EPSILON
            ):
                diagnostics.append(_diagnostic("SWEEP_PATH_GEOMETRY_INVALID", f"sweepPath.geometry.{geometry_id}", "圆弧显式端点与圆心、半径和角度不一致。", [geometry_id]))
                continue
            endpoints = (Endpoint(*parameter_endpoints[0]), Endpoint(*parameter_endpoints[1]))
        else:
            endpoints = _endpoints(geometry)
            if endpoints is None or _distance(*endpoints) <= 1e-9:
                diagnostics.append(_diagnostic("SWEEP_PATH_ZERO_LENGTH" if endpoints else "SWEEP_PATH_GEOMETRY_INVALID", f"sweepPath.geometry.{geometry_id}", "路径图元端点无效或长度为零。", [geometry_id]))
                continue
            polyline_points = [_point(item) for item in (_value(geometry, "points", None) or [])]
            polyline_points = [item for item in polyline_points if item is not None]
            if len(polyline_points) >= 2 and any(
                _distance(first, second) <= 1e-9
                for first, second in zip(polyline_points, polyline_points[1:])
            ):
                diagnostics.append(_diagnostic("SWEEP_PATH_ZERO_LENGTH", f"sweepPath.geometry.{geometry_id}", "路径图元包含零长度折线段。", [geometry_id]))
                continue
        edges.append(Edge(index, geometry, endpoints[0], endpoints[1]))
    if not edges:
        return {"valid": False, "diagnostics": diagnostics, "ordered": [], "startEndpointRef": None}

    # Cluster endpoints into graph nodes using one shared tolerance.
    nodes: list[Endpoint] = []
    def node_for(point: Endpoint) -> int:
        for node_id, node in enumerate(nodes):
            if _distance(point, node) <= PATH_ENDPOINT_EPSILON:
                return node_id
        nodes.append(point)
        return len(nodes) - 1
    edges = [Edge(e.index, e.geometry, e.start, e.end, node_for(e.start), node_for(e.end)) for e in edges]
    incident: dict[int, list[int]] = {i: [] for i in range(len(nodes))}
    for position, edge in enumerate(edges):
        incident[edge.start_node].append(position)
        incident[edge.end_node].append(position)
    for node_id, members in incident.items():
        if len(set(members)) > 2:
            diagnostics.append(_diagnostic("SWEEP_PATH_BRANCH", "sweepPath.geometry", "路径连接点出现分叉，连接点度数不能大于 2。", [str(_value(edges[i].geometry, "id", "")) for i in set(members)]))

    requested = _value(path, "startEndpointRef")
    legacy_start_id = _value(path, "startPointId")
    if requested is None and legacy_start_id:
        requested = {"geometryId": str(legacy_start_id), "endpoint": "start"}
    start_position: int | None = None
    start_endpoint = "start"
    if requested:
        requested_geometry_id = str(requested.get("geometryId") if isinstance(requested, dict) else getattr(requested, "geometryId", ""))
        requested_endpoint = requested.get("endpoint") if isinstance(requested, dict) else getattr(requested, "endpoint", "start")
        if requested_endpoint not in {"start", "end"}:
            diagnostics.append(_diagnostic("SWEEP_PATH_START_UNDEFINED", "sweepPath.startEndpointRef.endpoint", "扫掠路径起点端点必须是 start 或 end。"))
            requested_endpoint = "start"
        for pos, edge in enumerate(edges):
            if str(_value(edge.geometry, "id", "")) == requested_geometry_id:
                start_endpoint = requested_endpoint
                start_position = pos
                break
    degree_one = [node_id for node_id, members in incident.items() if len(set(members)) == 1]
    if start_position is None:
        if degree_one:
            start_node = degree_one[0]
            start_position = incident[start_node][0]
            start_endpoint = "start" if edges[start_position].start_node == start_node else "end"
        else:
            diagnostics.append(_diagnostic("SWEEP_PATH_START_UNDEFINED", "sweepPath.startEndpointRef", "闭合扫掠路径必须明确选择起点。"))
    start_ref = None
    if start_position is not None:
        start_ref = {"geometryId": str(_value(edges[start_position].geometry, "id", "")), "endpoint": start_endpoint}

    # Traverse the graph from the chosen endpoint, deriving order and direction.
    ordered: list[dict[str, Any]] = []
    if start_position is not None:
        first = edges[start_position]
        first_forward = start_endpoint == "start"
        ordered.append({"position": start_position, "geometryId": str(_value(first.geometry, "id", "")), "forward": first_forward})
        current_node = first.end_node if first_forward else first.start_node
        previous: int | None = start_position
        while True:
            candidates = [pos for pos in incident[current_node] if pos != previous and pos not in {item["position"] for item in ordered}]
            if not candidates:
                break
            pos = candidates[0]
            edge = edges[pos]
            forward = edge.start_node == current_node
            ordered.append({"position": pos, "geometryId": str(_value(edge.geometry, "id", "")), "forward": forward})
            current_node = edge.end_node if forward else edge.start_node
            previous = pos
        if len(ordered) != len(edges):
            remaining = [str(_value(edge.geometry, "id", "")) for pos, edge in enumerate(edges) if pos not in {item["position"] for item in ordered}]
            diagnostics.append(_diagnostic("SWEEP_PATH_DISCONNECTED", "sweepPath.geometry", "扫掠路径存在未连接的图元。", remaining))

    # Duplicate and non-adjacent intersection checks.
    for left, first in enumerate(edges):
        for right in range(left + 1, len(edges)):
            second = edges[right]
            if _value(first.geometry, "geometryType") == _value(second.geometry, "geometryType") == "line" and _same_line(first, second):
                diagnostics.append(_diagnostic("SWEEP_PATH_DUPLICATE_SEGMENT", "sweepPath.geometry", "发现重复的线段（含反向重合）。", [str(_value(first.geometry, "id", "")), str(_value(second.geometry, "id", ""))]))
            elif _value(first.geometry, "geometryType") == _value(second.geometry, "geometryType") == "arc" and _same_arc(first, second):
                diagnostics.append(_diagnostic("SWEEP_PATH_DUPLICATE_SEGMENT", "sweepPath.geometry", "发现重复的圆弧。", [str(_value(first.geometry, "id", "")), str(_value(second.geometry, "id", ""))]))
            adjacent = bool({first.start_node, first.end_node} & {second.start_node, second.end_node})
            if not adjacent and _polyline_intersects(_polyline(first.geometry, first), _polyline(second.geometry, second)):
                diagnostics.append(_diagnostic("SWEEP_PATH_SELF_INTERSECTION", "sweepPath.geometry", "路径图元之间存在自相交。", [str(_value(first.geometry, "id", "")), str(_value(second.geometry, "id", ""))]))
    return {"valid": not any(item["severity"] == "error" for item in diagnostics), "diagnostics": diagnostics, "ordered": ordered, "startEndpointRef": start_ref}


def ordered_path_points(path: Any) -> list[tuple[float, float]]:
    result = validate_sweep_path(path)
    # Keep this legacy function as the single lowering entry point, but now
    # sample arcs with the same bounded-error algorithm used by CAD callers.
    from .sweep_path_sampling import sample_ordered_path_points

    try:
        return sample_ordered_path_points(path, result["ordered"])
    except (TypeError, ValueError):
        # Keep lowering best-effort for an editing/invalid draft; the caller
        # still receives the authoritative topology diagnostics.
        return []
