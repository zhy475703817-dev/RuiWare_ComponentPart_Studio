"""Deterministic helpers for parameterized sweep-path geometry.

The authored sweep path remains parameterized (line/arc records).  Sampling
is retained as a compatibility and frame-calculation representation, while
``ordered_path_segments_3d`` exposes the exact ordered geometry needed by a
CAD kernel.  This module intentionally has no OpenCascade dependency so the
same direction, angle-unit, and reference-plane rules are shared by lowering,
validation, and CAD construction.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Literal, Sequence

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]
Plane = Literal["XY", "XZ", "YZ"]

TAU = 2.0 * math.pi
ARC_ANGLE_EPSILON = 1e-9
DEFAULT_MAX_ANGLE_DEGREES = 5.0
DEFAULT_MAX_CHORD_ERROR = 0.1


def _as_point(value: Any) -> Point2 | None:
    if value is None:
        return None
    try:
        values = tuple(value)
    except TypeError:
        return None
    if len(values) < 2:
        return None
    try:
        point = (float(values[0]), float(values[1]))
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(component) for component in point) else None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _angle_pair_radians(start_angle: Any, end_angle: Any) -> tuple[float, float] | None:
    """Normalize persisted angles to radians.

    The web sketch contract historically stores angles in degrees while the
    Python geometry helpers and most numerical callers use radians.  Values
    outside one turn are unambiguously degree-oriented in the persisted
    payload; values within one turn are treated as radians.  This preserves
    existing radian callers and lets browser-authored ``0..360`` arcs pass
    through without a second conversion layer.
    """

    start = _finite_float(start_angle)
    end = _finite_float(end_angle)
    if start is None or end is None:
        return None
    if max(abs(start), abs(end)) > TAU + 1e-6:
        return math.radians(start), math.radians(end)
    return start, end


def _geometry_angles(geometry: Any) -> tuple[float, float] | None:
    """Resolve angle units using explicit endpoint evidence when available."""

    raw_start = _value(geometry, "startAngle")
    raw_end = _value(geometry, "endAngle")
    start = _finite_float(raw_start)
    end = _finite_float(raw_end)
    if start is None or end is None:
        return None
    candidates = [(start, end), (math.radians(start), math.radians(end))]
    explicit_start = _as_point(_value(geometry, "start"))
    explicit_end = _as_point(_value(geometry, "end"))
    center = _value(geometry, "center")
    radius = _value(geometry, "radius")
    if explicit_start is None and explicit_end is None:
        # Without endpoint evidence retain the established Python/radian
        # convention for values within one turn and treat larger values as
        # browser-authored degrees.
        return candidates[1] if max(abs(start), abs(end)) > TAU + 1e-6 else candidates[0]
    scored: list[tuple[float, tuple[float, float]]] = []
    for candidate in candidates:
        first = arc_point(center, radius, candidate[0])
        last = arc_point(center, radius, candidate[1])
        if first is None or last is None:
            continue
        score = 0.0
        if explicit_start is not None:
            score += math.dist(first, explicit_start)
        if explicit_end is not None:
            score += math.dist(last, explicit_end)
        scored.append((score, candidate))
    return min(scored, key=lambda item: item[0])[1] if scored else candidates[0]


def _value(geometry: Any, name: str, default: Any = None) -> Any:
    if isinstance(geometry, dict):
        return geometry.get(name, default)
    return getattr(geometry, name, default)


def arc_sweep_angle(
    start_angle: Any,
    end_angle: Any,
    sweep_direction: str = "ccw",
    large_arc: bool = False,
) -> float | None:
    """Return the signed angular travel selected by an arc record.

    ``sweepDirection`` owns the traversal direction and the returned delta is
    always congruent to ``end - start`` modulo ``2*pi``.  Consequently a CW
    arc from 0 to +90 degrees travels -270 degrees, as it must to reach the
    specified endpoint without an artificial jump.  ``largeArc`` is retained
    as a compatibility hint and promotes a minor directed travel to its major
    counterpart; an explicit direction/end-angle pair remains authoritative.
    Angles may be outside the usual ``[-pi, pi]`` range.  A zero separation is
    rejected rather than silently becoming a full circle: the path model has
    no explicit full-circle flag and a coincident endpoint would otherwise be
    indistinguishable from a zero-length arc in topology.
    """

    angles = _angle_pair_radians(start_angle, end_angle)
    if angles is None:
        return None
    return _directed_sweep_radians(angles[0], angles[1], sweep_direction, large_arc)


def _directed_sweep_radians(start: float, end: float, sweep_direction: str, large_arc: bool = False) -> float | None:
    if sweep_direction not in {"cw", "ccw"}:
        return None
    ccw_delta = (end - start) % TAU
    if ccw_delta <= ARC_ANGLE_EPSILON:
        return 0.0
    if sweep_direction == "ccw":
        sweep = ccw_delta
    else:
        sweep = ccw_delta - TAU
    if bool(large_arc):
        # Select the major route while preserving the exact end angle.  A
        # minor CCW route's major counterpart is CW (and vice versa); changing
        # sign here is intentional and avoids the artificial endpoint jump
        # that a same-sign 270° travel would create.
        magnitude = abs(sweep)
        if magnitude < math.pi:
            magnitude = TAU - magnitude
            sweep = -math.copysign(magnitude, sweep)
    return sweep


def geometry_arc_sweep_angle(geometry: Any) -> float | None:
    """Return a geometry record's signed sweep after unit disambiguation."""

    angles = _geometry_angles(geometry)
    if angles is None:
        return None
    return _directed_sweep_radians(
        angles[0],
        angles[1],
        str(_value(geometry, "sweepDirection", "ccw")),
        bool(_value(geometry, "largeArc", False)),
    )


def arc_point(center: Any, radius: Any, angle: Any) -> Point2 | None:
    """Evaluate one point on a 2-D circle, returning ``None`` if invalid."""

    c = _as_point(center)
    r = _finite_float(radius)
    a = _finite_float(angle)
    if c is None or r is None or a is None or r <= 0:
        return None
    point = (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
    return point if all(math.isfinite(component) for component in point) else None


def arc_endpoints(geometry: Any) -> tuple[Point2, Point2] | None:
    """Return parameter-derived arc endpoints, preferring explicit endpoints.

    Explicit endpoints are retained when present so authored snap coordinates
    survive lowering exactly.  Validation compares them with the parameter
    evaluation and reports a malformed arc when they disagree materially.
    """

    center = _value(geometry, "center")
    radius = _value(geometry, "radius")
    angles = _geometry_angles(geometry)
    if angles is None:
        return None
    start_angle, end_angle = angles
    computed_start = arc_point(center, radius, start_angle)
    computed_end = arc_point(center, radius, end_angle)
    if computed_start is None or computed_end is None:
        return None
    explicit_start = _as_point(_value(geometry, "start"))
    explicit_end = _as_point(_value(geometry, "end"))
    return explicit_start or computed_start, explicit_end or computed_end


def computed_arc_endpoints(geometry: Any) -> tuple[Point2, Point2] | None:
    """Evaluate arc endpoints strictly from center/radius/angle parameters."""

    center = _as_point(_value(geometry, "center"))
    radius = _finite_float(_value(geometry, "radius"))
    angles = _geometry_angles(geometry)
    if center is None or radius is None or radius <= 0 or angles is None:
        return None
    first = arc_point(center, radius, angles[0])
    last = arc_point(center, radius, angles[1])
    return (first, last) if first is not None and last is not None else None


# Public descriptive aliases used by topology/lowering callers.
arc_parameter_endpoints = computed_arc_endpoints
geometry_arc_angles = _geometry_angles


def arc_tangent_2d(angle: float, sweep_direction: str = "ccw") -> Point2:
    """Return the unit tangent of a circle at a radian angle."""

    if sweep_direction == "ccw":
        return (-math.sin(angle), math.cos(angle))
    if sweep_direction == "cw":
        return (math.sin(angle), -math.cos(angle))
    raise ValueError("sweep_direction must be cw or ccw")


def geometry_arc_tangent(geometry: Any, angle: float | None = None) -> Point2:
    """Return a parameter arc tangent, resolving persisted degree angles."""

    angles = _geometry_angles(geometry)
    if angles is None:
        raise ValueError("arc requires valid start and end angles")
    selected = angles[0] if angle is None else float(angle)
    # ``largeArc`` can select the complementary directed route when the
    # persisted end angle would otherwise be missed.  Use the effective travel
    # sign for the tangent so sampled points and frame construction never
    # disagree about the direction of motion.
    sweep = geometry_arc_sweep_angle(geometry)
    if sweep is None or abs(sweep) <= ARC_ANGLE_EPSILON:
        raise ValueError("arc requires a non-zero sweep")
    direction = "ccw" if sweep > 0 else "cw"
    return arc_tangent_2d(selected, direction)


def _line_points(geometry: Any) -> list[Point2]:
    raw_points = _value(geometry, "points", None) or []
    points = [point for point in (_as_point(item) for item in raw_points) if point is not None]
    if len(points) >= 2:
        return points
    start = _as_point(_value(geometry, "start"))
    end = _as_point(_value(geometry, "end"))
    return [start, end] if start is not None and end is not None else []


def sample_arc_points(
    geometry: Any,
    *,
    forward: bool = True,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point2]:
    """Sample an arc into points with bounded angular and chord-height error.

    The first and last points are the exact authored endpoints (or the exact
    parameter evaluation when endpoint fields are omitted).  Interior points
    are evaluated on the selected directed circle.  Reversing a topology edge
    simply reverses this list, preserving endpoint identity and continuity.
    """

    center = _as_point(_value(geometry, "center"))
    radius = _finite_float(_value(geometry, "radius"))
    angles = _geometry_angles(geometry)
    start_angle, end_angle = angles if angles is not None else (None, None)
    direction = _value(geometry, "sweepDirection", "ccw")
    sweep = (
        _directed_sweep_radians(start_angle, end_angle, str(direction), bool(_value(geometry, "largeArc", False)))
        if start_angle is not None and end_angle is not None
        else None
    )
    if center is None or radius is None or radius <= 0 or sweep is None or abs(sweep) <= ARC_ANGLE_EPSILON:
        raise ValueError("arc requires a positive radius, valid angles, direction, and non-zero sweep")
    max_angle = _finite_float(max_angle_degrees)
    max_error = _finite_float(max_chord_error)
    if max_angle is None or max_angle <= 0:
        raise ValueError("max_angle_degrees must be positive")
    if max_error is None or max_error < 0:
        raise ValueError("max_chord_error must be non-negative")

    # Sagitta for a circular chord is r * (1 - cos(theta / 2)).  Combining
    # that bound with the explicit five-degree angular bound gives the largest
    # legal interval for each sample.
    if max_error == 0:
        sagitta_step = 0.0
    else:
        cosine = 1.0 - max_error / radius
        cosine = max(-1.0, min(1.0, cosine))
        sagitta_step = 2.0 * math.acos(cosine)
    angle_step = math.radians(max_angle)
    if sagitta_step > 0:
        angle_step = min(angle_step, sagitta_step)
    if angle_step <= ARC_ANGLE_EPSILON:
        # A zero error request is mathematically impossible to satisfy with a
        # finite polyline.  Use a very small deterministic interval instead of
        # looping forever; the normal contract uses the 0.1 mm default.
        angle_step = min(math.radians(0.01), math.radians(max_angle))
    count = max(1, int(math.ceil(abs(sweep) / angle_step)))

    explicit = arc_endpoints(geometry)
    first = explicit[0] if explicit else arc_point(center, radius, start_angle)
    last = explicit[1] if explicit else arc_point(center, radius, end_angle)
    if first is None or last is None:  # defensive; values were checked above
        raise ValueError("arc endpoints cannot be calculated")
    points: list[Point2] = []
    for index in range(count + 1):
        if index == 0:
            point = first
        elif index == count:
            point = last
        else:
            angle = start_angle + sweep * index / count
            point = arc_point(center, radius, angle)
        if point is not None:
            points.append(point)
    if len(points) < 2:
        raise ValueError("arc sampling produced fewer than two points")
    return points if forward else list(reversed(points))


def sample_line_points(geometry: Any, *, forward: bool = True) -> list[Point2]:
    points = _line_points(geometry)
    if len(points) < 2:
        raise ValueError("line requires at least two points")
    return points if forward else list(reversed(points))


def sample_line_tangents(geometry: Any, *, forward: bool = True) -> list[Point2]:
    points = sample_line_points(geometry, forward=forward)
    tangents: list[Point2] = []
    for first, second in zip(points, points[1:]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            raise ValueError("line contains a zero-length segment")
        tangents.append((dx / length, dy / length))
    return tangents


def sample_arc_tangents(geometry: Any, *, forward: bool = True, **kwargs: Any) -> list[Point2]:
    """Return one analytic tangent for every sampled arc chord."""

    points = sample_arc_points(geometry, forward=True, **kwargs)
    angles = _geometry_angles(geometry)
    if angles is None:
        raise ValueError("arc requires valid start and end angles")
    sweep = geometry_arc_sweep_angle(geometry)
    if sweep is None:
        raise ValueError("arc requires a valid sweep")
    count = len(points) - 1
    # Derive the effective direction from the selected signed sweep.  This is
    # normally identical to ``sweepDirection``; it differs only for legacy
    # records that request a major route while their endpoint angles imply the
    # complementary direction.  Keeping the tangent aligned with the sampled
    # path prevents a 180° frame flip at such arcs.
    direction = "ccw" if sweep >= 0 else "cw"
    if forward:
        start_angle = angles[0]
        traversal_sweep = sweep
    else:
        # Use the unwrapped parameter endpoint.  It is geometrically
        # equivalent to ``endAngle`` and preserves the exact route selected
        # by largeArc/direction when traversal is reversed.
        start_angle = angles[0] + sweep
        traversal_sweep = -sweep
        direction = "ccw" if traversal_sweep >= 0 else "cw"
    return [
        arc_tangent_2d(start_angle + traversal_sweep * index / count, direction)
        for index in range(count)
    ]


def sample_geometry_station_tangents(
    geometry: Any,
    *,
    forward: bool = True,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point2]:
    """Return an analytic outgoing tangent at every sampled station.

    The terminal entry is evaluated at the actual arc endpoint rather than at
    the start of its final approximation chord.  For a polyline, an interior
    vertex follows the established outgoing-segment rule and the last station
    retains the incoming line direction.
    """

    kind = _value(geometry, "geometryType", "line")
    if kind == "line":
        tangents = sample_line_tangents(geometry, forward=forward)
        if not tangents:
            return []
        return [*tangents, tangents[-1]]
    if kind != "arc":
        raise ValueError(f"unsupported sweep path geometry: {kind}")

    sampled = sample_arc_points(
        geometry,
        forward=True,
        max_angle_degrees=max_angle_degrees,
        max_chord_error=max_chord_error,
    )
    angles = _geometry_angles(geometry)
    sweep = geometry_arc_sweep_angle(geometry)
    if angles is None or sweep is None or abs(sweep) <= ARC_ANGLE_EPSILON:
        raise ValueError("arc requires valid angles and a non-zero sweep")
    count = len(sampled) - 1
    if forward:
        start_angle = angles[0]
        traversal_sweep = sweep
    else:
        start_angle = angles[0] + sweep
        traversal_sweep = -sweep
    direction = "ccw" if traversal_sweep > 0 else "cw"
    return [
        arc_tangent_2d(start_angle + traversal_sweep * index / count, direction)
        for index in range(count + 1)
    ]


def sample_geometry_points(
    geometry: Any,
    *,
    forward: bool = True,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point2]:
    kind = _value(geometry, "geometryType", "line")
    if kind == "line":
        return sample_line_points(geometry, forward=forward)
    if kind == "arc":
        return sample_arc_points(
            geometry,
            forward=forward,
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
    raise ValueError(f"unsupported sweep path geometry: {kind}")


def sample_geometry_tangents(
    geometry: Any,
    *,
    forward: bool = True,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point2]:
    kind = _value(geometry, "geometryType", "line")
    if kind == "line":
        return sample_line_tangents(geometry, forward=forward)
    if kind == "arc":
        return sample_arc_tangents(
            geometry,
            forward=forward,
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
    raise ValueError(f"unsupported sweep path geometry: {kind}")


def map_point_to_3d(point: Sequence[float], plane: Plane = "XY", *, xy_as_xz: bool = False) -> Point3:
    """Map a 2-D path coordinate to a right-handed 3-D reference plane.

    The default is conventional CAD naming (XY -> z=0).  The sweep editor's
    historical XY convention displays coordinates as X/Z; callers lowering a
    path for the existing profile worker can opt into that convention with
    ``xy_as_xz=True``.  XZ and YZ mappings are unambiguous in either mode.
    """

    if len(point) < 2:
        raise ValueError("2-D point requires two components")
    x, y = float(point[0]), float(point[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("path point must be finite")
    if plane == "XY":
        return (x, 0.0, y) if xy_as_xz else (x, y, 0.0)
    if plane == "XZ":
        return (x, 0.0, y)
    if plane == "YZ":
        return (0.0, x, y)
    raise ValueError(f"unsupported sweep path plane: {plane}")


def map_points_to_3d(
    points: Iterable[Sequence[float]],
    plane: Plane = "XY",
    *,
    xy_as_xz: bool = False,
) -> list[Point3]:
    return [map_point_to_3d(point, plane, xy_as_xz=xy_as_xz) for point in points]


def mapped_path_plane(plane: Plane = "XY", *, xy_as_xz: bool = False) -> Plane:
    """Return the physical 3-D plane produced by ``map_point_to_3d``."""

    if plane not in {"XY", "XZ", "YZ"}:
        raise ValueError(f"unsupported sweep path plane: {plane}")
    return "XZ" if plane == "XY" and xy_as_xz else plane


def ordered_path_segments_3d(
    path: Any,
    ordered: Sequence[dict[str, Any]] | None = None,
    *,
    xy_as_xz: bool = True,
) -> list[dict[str, Any]]:
    """Lower graph-ordered path geometry without tessellating circular arcs.

    Every returned line has explicit traversal-oriented ``start``/``end``
    points.  Every arc additionally retains its parameterized circle data and
    carries an unambiguous signed ``sweepAngle`` in radians.  ``startAngle``
    and ``endAngle`` are also radians and swap when graph traversal reverses
    the authored geometry.  A multi-point line is emitted as one exact line
    record per consecutive pair so no authored polyline corner is collapsed.

    Joins that differ only by floating-point evaluation noise are made bitwise
    identical.  Larger gaps are deliberately retained for the CAD worker to
    reject with a clear connectivity diagnostic instead of hiding malformed
    geometry behind the path sampling tolerance.
    """

    if ordered is None:
        from .sweep_path import validate_sweep_path

        ordered = validate_sweep_path(path).get("ordered", [])
    authored_plane = str(_value(path, "plane", "XY"))
    if authored_plane not in {"XY", "XZ", "YZ"}:
        raise ValueError(f"unsupported sweep path plane: {authored_plane}")
    plane: Plane = authored_plane  # type: ignore[assignment]
    physical_plane = mapped_path_plane(plane, xy_as_xz=xy_as_xz)
    by_id = {
        str(_value(item, "id", "")): item
        for item in (_value(path, "geometry", None) or [])
    }
    result: list[dict[str, Any]] = []

    def append_segment(record: dict[str, Any]) -> None:
        if result:
            previous_end = result[-1]["end"]
            current_start = record["start"]
            if math.dist(previous_end, current_start) <= 1e-9:
                # Reuse the exact tuple object/value from the preceding edge.
                # This removes sin(pi)-style noise without accepting a real
                # authored gap that topology merely clusters by tolerance.
                record["start"] = previous_end
        result.append(record)

    for order_index, order_item in enumerate(ordered):
        geometry_id = str(order_item.get("geometryId", ""))
        geometry = by_id.get(geometry_id)
        if geometry is None:
            continue
        forward = bool(order_item.get("forward", True))
        geometry_type = str(_value(geometry, "geometryType", "line"))
        common = {
            "geometryId": geometry_id,
            "geometryType": geometry_type,
            "orderIndex": order_index,
            "forward": forward,
            "plane": plane,
            "mappedPlane": physical_plane,
        }
        if geometry_type == "line":
            line_points = sample_line_points(geometry, forward=forward)
            for segment_index, (start, end) in enumerate(
                zip(line_points, line_points[1:])
            ):
                append_segment({
                    **common,
                    "segmentIndex": segment_index,
                    "start": map_point_to_3d(start, plane, xy_as_xz=xy_as_xz),
                    "end": map_point_to_3d(end, plane, xy_as_xz=xy_as_xz),
                })
            continue
        if geometry_type != "arc":
            raise ValueError(f"unsupported sweep path geometry: {geometry_type}")

        center = _as_point(_value(geometry, "center"))
        radius = _finite_float(_value(geometry, "radius"))
        angles = _geometry_angles(geometry)
        sweep = geometry_arc_sweep_angle(geometry)
        endpoints = computed_arc_endpoints(geometry)
        if (
            center is None
            or radius is None
            or radius <= 0
            or angles is None
            or sweep is None
            or abs(sweep) <= ARC_ANGLE_EPSILON
            or endpoints is None
        ):
            raise ValueError(
                f"arc {geometry_id or '<unknown>'} requires valid circle parameters"
            )
        source_start_angle, source_end_angle = angles
        authored_direction = str(_value(geometry, "sweepDirection", "ccw"))
        authored_large_arc = bool(_value(geometry, "largeArc", False))
        source_start, source_end = endpoints
        if forward:
            start, end = source_start, source_end
            start_angle, end_angle = source_start_angle, source_end_angle
            traversal_sweep = sweep
        else:
            start, end = source_end, source_start
            start_angle, end_angle = source_end_angle, source_start_angle
            traversal_sweep = -sweep
        append_segment({
            **common,
            "segmentIndex": 0,
            "start": map_point_to_3d(start, plane, xy_as_xz=xy_as_xz),
            "end": map_point_to_3d(end, plane, xy_as_xz=xy_as_xz),
            "center": map_point_to_3d(center, plane, xy_as_xz=xy_as_xz),
            "radius": radius,
            "startAngle": start_angle,
            "endAngle": end_angle,
            "largeArc": authored_large_arc,
            "sweepDirection": "ccw" if traversal_sweep > 0 else "cw",
            "sweepAngle": traversal_sweep,
            # Preserve the authored parameter values even when graph order
            # traverses the edge in reverse.  The unprefixed fields above are
            # traversal-oriented and are therefore directly consumable by CAD.
            "authoredStartAngle": source_start_angle,
            "authoredEndAngle": source_end_angle,
            "authoredLargeArc": authored_large_arc,
            "authoredSweepDirection": authored_direction,
        })

    if result and math.dist(result[-1]["end"], result[0]["start"]) <= 1e-9:
        result[-1]["end"] = result[0]["start"]
    return result


def sample_ordered_path_points(
    path: Any,
    ordered: Sequence[dict[str, Any]] | None = None,
    *,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point2]:
    """Sample graph-ordered path geometry while de-duplicating joins."""

    if ordered is None:
        # Lazy import avoids a cycle: topology uses ``sample_geometry_points``
        # for intersection checks while this convenience function consumes its
        # graph result.
        from .sweep_path import validate_sweep_path

        ordered = validate_sweep_path(path).get("ordered", [])
    by_id = {str(_value(item, "id", "")): item for item in (_value(path, "geometry", None) or [])}
    result: list[Point2] = []
    for item in ordered:
        geometry = by_id.get(str(item.get("geometryId", "")))
        if geometry is None:
            continue
        segment = sample_geometry_points(
            geometry,
            forward=bool(item.get("forward", True)),
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
        for point in segment:
            if not result or math.hypot(result[-1][0] - point[0], result[-1][1] - point[1]) > 0.05:
                result.append(point)
    return result


def sample_ordered_path_data(
    path: Any,
    ordered: Sequence[dict[str, Any]] | None = None,
    *,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> dict[str, Any]:
    """Return sampled points plus source metadata for CAD corner handling.

    ``segments`` has one entry per output chord.  ``cornerKinds`` has one
    entry per output station and identifies only a line-to-line join as a
    real RightCorner; every interior arc sample and line/arc join is smooth.
    This lets the CAD worker avoid interpreting an approximation chord as an
    authored sharp corner while still consuming ordinary ``pathPoints``.
    """

    if ordered is None:
        from .sweep_path import validate_sweep_path

        ordered = validate_sweep_path(path).get("ordered", [])
    by_id = {str(_value(item, "id", "")): item for item in (_value(path, "geometry", None) or [])}
    points: list[Point2] = []
    segments: list[dict[str, Any]] = []
    station_tangents: list[Point2 | None] = []
    for order_index, item in enumerate(ordered):
        geometry_id = str(item.get("geometryId", ""))
        geometry = by_id.get(geometry_id)
        if geometry is None:
            continue
        geometry_type = str(_value(geometry, "geometryType", "line"))
        sampled = sample_geometry_points(
            geometry,
            forward=bool(item.get("forward", True)),
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
        sampled_tangents = sample_geometry_tangents(
            geometry,
            forward=bool(item.get("forward", True)),
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
        sampled_station_tangents = sample_geometry_station_tangents(
            geometry,
            forward=bool(item.get("forward", True)),
            max_angle_degrees=max_angle_degrees,
            max_chord_error=max_chord_error,
        )
        if not sampled:
            continue
        if len(sampled_station_tangents) != len(sampled):
            raise ValueError("station tangent metadata does not match sampled path")
        if not points:
            points.append(sampled[0])
            station_tangents.append(sampled_station_tangents[0])
        elif math.hypot(points[-1][0] - sampled[0][0], points[-1][1] - sampled[0][1]) > 0.05:
            # Topology normally rejects this.  Preserve the discontinuity in
            # metadata rather than silently manufacturing a connecting chord.
            points.append(sampled[0])
            station_tangents.append(sampled_station_tangents[0])
            segments.append({
                "geometryId": geometry_id,
                "geometryType": geometry_type,
                "orderIndex": order_index,
                "sampleIndex": -1,
                "disconnectedJoin": True,
                "tangent2d": None,
            })
        else:
            # At an authored join use the outgoing analytic tangent.  A
            # line/arc continuity check belongs to CAD construction, where a
            # mismatch can be surfaced as an operation diagnostic.
            station_tangents[-1] = sampled_station_tangents[0]
        for sample_index, point in enumerate(sampled[1:]):
            if math.hypot(points[-1][0] - point[0], points[-1][1] - point[1]) <= 1e-12:
                continue
            points.append(point)
            station_tangents.append(sampled_station_tangents[sample_index + 1])
            segments.append({
                "geometryId": geometry_id,
                "geometryType": geometry_type,
                "orderIndex": order_index,
                "sampleIndex": sample_index,
                "disconnectedJoin": False,
                "tangent2d": sampled_tangents[sample_index] if sample_index < len(sampled_tangents) else None,
            })
    if len(points) > 1 and math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]) <= 1e-9:
        points[-1] = points[0]
    corner_kinds: list[str] = ["endpoint"] * len(points)
    for index in range(1, max(1, len(points) - 1)):
        if index >= len(points) - 1 or index >= len(segments):
            break
        incoming = segments[index - 1]
        outgoing = segments[index]
        corner_kinds[index] = (
            "right"
            if incoming["geometryType"] == outgoing["geometryType"] == "line"
            and incoming["geometryId"] != outgoing["geometryId"]
            else "smooth"
        )
    return {
        "points": points,
        "segments": segments,
        "cornerKinds": corner_kinds,
        "stationTangents2d": station_tangents,
    }


def sample_ordered_path_points_3d(
    path: Any,
    ordered: Sequence[dict[str, Any]] | None = None,
    *,
    xy_as_xz: bool = True,
    max_angle_degrees: float = DEFAULT_MAX_ANGLE_DEGREES,
    max_chord_error: float = DEFAULT_MAX_CHORD_ERROR,
) -> list[Point3]:
    points = sample_ordered_path_points(
        path,
        ordered,
        max_angle_degrees=max_angle_degrees,
        max_chord_error=max_chord_error,
    )
    plane = _value(path, "plane", "XY")
    return map_points_to_3d(points, plane, xy_as_xz=xy_as_xz)


# Verbose aliases make the public contract easy to discover for callers and
# retain compatibility with likely naming variants in downstream integrations.
sample_arc = sample_arc_points
sample_path_geometry = sample_geometry_points
sample_sweep_path = sample_ordered_path_points
sample_sweep_path_3d = sample_ordered_path_points_3d
sample_sweep_path_data = sample_ordered_path_data
arc_to_points = sample_arc_points
path_to_3d_points = sample_ordered_path_points_3d
compute_arc_sweep = arc_sweep_angle
compute_arc_endpoints = computed_arc_endpoints
sample_arc_to_points = sample_arc_points
sample_path_points = sample_ordered_path_points
sample_path_points_3d = sample_ordered_path_points_3d
