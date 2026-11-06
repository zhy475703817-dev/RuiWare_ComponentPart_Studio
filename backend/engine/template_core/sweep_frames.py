"""Pure vector math for sweep section frames.

The sweep path is represented as a list of 3-D stations.  A frame is a
``(origin, x_axis, y_axis, tangent)`` tuple where the two axes span the
section plane and ``tangent`` points along the path.  Keeping this module
free of OpenCascade types makes orientation behaviour deterministic and
easy to test independently from CAD construction.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, TypeAlias

Vec3: TypeAlias = tuple[float, float, float]
Frame: TypeAlias = tuple[Vec3, Vec3, Vec3, Vec3]
EPSILON = 1e-9


def _v(value: Iterable[float]) -> Vec3:
    a = tuple(float(x) for x in value)
    if len(a) != 3:
        raise ValueError("3-D vectors require exactly three components")
    return a  # type: ignore[return-value]


def add(a: Vec3, b: Vec3) -> Vec3:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def sub(a: Vec3, b: Vec3) -> Vec3:
    return tuple(x - y for x, y in zip(a, b))  # type: ignore[return-value]


def scale(a: Vec3, factor: float) -> Vec3:
    return tuple(x * factor for x in a)  # type: ignore[return-value]


def dot(a: Vec3, b: Vec3) -> float:
    return sum(x * y for x, y in zip(a, b))


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalize(a: Vec3, *, epsilon: float = EPSILON) -> Vec3:
    length = norm(a)
    if length <= epsilon:
        raise ValueError("zero-length vector cannot be normalized")
    return scale(a, 1.0 / length)


def project_perpendicular(vector: Vec3, normal: Vec3) -> Vec3:
    return sub(vector, scale(normal, dot(vector, normal)))


def orthonormalize(first: Vec3, second: Vec3, normal: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    n = normalize(normal)
    x = normalize(project_perpendicular(first, n))
    y = normalize(cross(n, x))
    # Recompute x to remove accumulated floating point drift and guarantee a
    # right-handed frame even when callers pass a nearly-degenerate second axis.
    x = normalize(cross(y, n))
    return x, y, n


def rotate(vector: Vec3, axis: Vec3, angle: float) -> Vec3:
    """Rotate *vector* around a unit/non-unit axis with Rodrigues' formula."""
    k = normalize(axis)
    c, s = math.cos(angle), math.sin(angle)
    return add(add(scale(vector, c), scale(cross(k, vector), s)), scale(k, dot(k, vector) * (1.0 - c)))


def segment_tangents(points: Sequence[Vec3], *, epsilon: float = EPSILON) -> list[Vec3]:
    """Return one normalized tangent for every path segment."""
    if len(points) < 2:
        raise ValueError("sweep path requires at least two stations")
    result: list[Vec3] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        delta = sub(_v(end), _v(start))
        if norm(delta) <= epsilon:
            raise ValueError(f"sweep path contains a zero-length segment at index {index}")
        result.append(normalize(delta, epsilon=epsilon))
    return result


def corner_tangents(points: Sequence[Vec3], *, epsilon: float = EPSILON) -> list[Vec3]:
    """Return a tangent at each station, including stable 180° corners."""
    segments = segment_tangents(points, epsilon=epsilon)
    result = [segments[0]]
    for incoming, outgoing in zip(segments, segments[1:]):
        blended = add(incoming, outgoing)
        # At a 180° fold-back the bisector is undefined.  Keeping the outgoing
        # direction is deterministic and lets topology diagnostics report the
        # problematic path rather than crashing frame generation.
        result.append(normalize(outgoing if norm(blended) <= epsilon else blended, epsilon=epsilon))
    result.append(segments[-1])
    return result


def initial_frame(origin: Vec3, tangent: Vec3, *, reference: Vec3 = (1.0, 0.0, 0.0)) -> Frame:
    t = normalize(tangent)
    ref = project_perpendicular(_v(reference), t)
    if norm(ref) <= EPSILON:
        ref = project_perpendicular((0.0, 1.0, 0.0), t)
    x, y, t = orthonormalize(ref, cross(t, ref), t)
    return _v(origin), x, y, t


def _flip_if_discontinuous(x: Vec3, y: Vec3, previous: Frame | None) -> tuple[Vec3, Vec3]:
    """Choose the equivalent signed frame closest to the previous station."""

    if previous is not None and dot(x, previous[1]) + dot(y, previous[2]) < 0.0:
        return scale(x, -1.0), scale(y, -1.0)
    return x, y


def _follow_frame(origin: Vec3, tangent: Vec3, previous: Frame | None = None) -> Frame:
    """Build a world-up-following frame with a continuous singular fallback."""

    t = normalize(tangent)
    up = (0.0, 0.0, 1.0)
    # Prefer the projection of world-up as the section y axis.  At a vertical
    # tangent that projection vanishes; carrying the prior x axis through the
    # singularity prevents the otherwise unavoidable 180-degree roll flip.
    y_reference = project_perpendicular(up, t)
    if norm(y_reference) > EPSILON:
        y = normalize(y_reference)
        x = normalize(cross(y, t))
    else:
        # Keep the historical profile orientation for the first vertical
        # station; later vertical stations carry the previous axis through
        # the singularity to avoid a roll jump.
        fallback = previous[1] if previous is not None else (1.0, 0.0, 0.0)
        x = project_perpendicular(fallback, t)
        if norm(x) <= EPSILON:
            x = project_perpendicular((1.0, 0.0, 0.0), t)
        x = normalize(x)
        y = normalize(cross(t, x))
    x, y = _flip_if_discontinuous(x, y, previous)
    x, y, t = orthonormalize(x, y, t)
    return _v(origin), x, y, t


def _fixed_world_frame(origin: Vec3, tangent: Vec3, previous: Frame | None = None) -> Frame:
    """Build a frame whose x axis follows world X where geometrically possible."""

    t = normalize(tangent)
    x_reference = project_perpendicular((1.0, 0.0, 0.0), t)
    if norm(x_reference) <= EPSILON:
        # World X is parallel to the path.  Reuse the transported prior axis
        # when available so crossing this singularity does not flip the frame.
        fallback = previous[1] if previous is not None else (0.0, 1.0, 0.0)
        x_reference = project_perpendicular(fallback, t)
        if norm(x_reference) <= EPSILON:
            x_reference = project_perpendicular((0.0, 0.0, 1.0), t)
    x = normalize(x_reference)
    y = normalize(cross(t, x))
    x, y = _flip_if_discontinuous(x, y, previous)
    x, y, t = orthonormalize(x, y, t)
    return _v(origin), x, y, t


def _transport(frame: Frame, tangent: Vec3) -> Frame:
    origin, x, y, previous = frame
    target = normalize(tangent)
    rotation_axis = cross(previous, target)
    axis_length = norm(rotation_axis)
    alignment = max(-1.0, min(1.0, dot(previous, target)))
    if axis_length <= EPSILON:
        if alignment < 0.0:  # 180°: choose a stable perpendicular axis.
            axis = cross(previous, x)
            if norm(axis) <= EPSILON:
                axis = cross(previous, y)
            x2, y2, _ = orthonormalize(rotate(x, axis, math.pi), rotate(y, axis, math.pi), target)
            return origin, x2, y2, target
        x2, y2, _ = orthonormalize(x, y, target)
        return origin, x2, y2, target
    angle = math.atan2(axis_length, alignment)
    x2 = rotate(x, rotation_axis, angle)
    y2 = rotate(y, rotation_axis, angle)
    x2, y2, _ = orthonormalize(x2, y2, target)
    return origin, x2, y2, target


def _station_tangents(points: Sequence[Vec3], overrides: Sequence[Vec3] | None = None) -> list[Vec3]:
    if overrides is None:
        return corner_tangents(points)
    if len(overrides) != len(points):
        raise ValueError("station tangent metadata must match path stations")
    result: list[Vec3] = []
    for index, tangent in enumerate(overrides):
        try:
            result.append(normalize(_v(tangent)))
        except ValueError as error:
            raise ValueError(f"invalid station tangent at index {index}") from error
    return result


def minimum_twist_frames(points: Sequence[Vec3], station_tangent_overrides: Sequence[Vec3] | None = None) -> list[Frame]:
    tangents = _station_tangents(points, station_tangent_overrides)
    frames = [initial_frame(_v(points[0]), tangents[0])]
    for point, tangent in zip(points[1:], tangents[1:]):
        transported = _transport(frames[-1], tangent)
        frames.append((_v(point), transported[1], transported[2], transported[3]))
    return frames


def follow_path_frames(points: Sequence[Vec3], station_tangent_overrides: Sequence[Vec3] | None = None) -> list[Frame]:
    """Orient the section with a world-up preference while following tangent."""
    frames: list[Frame] = []
    previous: Frame | None = None
    for point, tangent in zip(points, _station_tangents(points, station_tangent_overrides)):
        frame = _follow_frame(_v(point), tangent, previous)
        frames.append(frame)
        previous = frame
    return frames


def fixed_world_frames(points: Sequence[Vec3], station_tangent_overrides: Sequence[Vec3] | None = None) -> list[Frame]:
    """Keep the profile's world-X direction wherever the tangent permits it."""
    frames: list[Frame] = []
    previous: Frame | None = None
    for point, tangent in zip(points, _station_tangents(points, station_tangent_overrides)):
        frame = _fixed_world_frame(_v(point), tangent, previous)
        frames.append(frame)
        previous = frame
    return frames


def segment_start_frames(
    points: Sequence[Vec3],
    orientation_mode: str = "minimumTwist",
    segment_tangent_overrides: Sequence[Vec3] | None = None,
) -> list[Frame]:
    """Return a frame at the start of every *straight* path segment.

    ``path_frames`` intentionally reports a station frame at a corner using
    the bisector of the incoming and outgoing tangents.  That is useful for a
    smooth visualisation, but it is not perpendicular to either edge of a
    right-corner polyline.  CAD construction therefore uses these segment
    frames instead: each profile wire is normal to the edge that it sweeps,
    and adjacent solids are joined by their natural overlap at the corner.
    """
    points = [_v(point) for point in points]
    tangents = (
        [normalize(_v(tangent)) for tangent in segment_tangent_overrides]
        if segment_tangent_overrides is not None
        else segment_tangents(points)
    )
    if len(tangents) != len(points) - 1:
        raise ValueError("segment tangent metadata must match path segments")
    if orientation_mode == "followPath":
        result: list[Frame] = []
        previous: Frame | None = None
        for point, tangent in zip(points, tangents):
            frame = _follow_frame(_v(point), tangent, previous)
            result.append(frame)
            previous = frame
        return result
    if orientation_mode == "fixedWorld":
        result = []
        previous = None
        for point, tangent in zip(points, tangents):
            frame = _fixed_world_frame(_v(point), tangent, previous)
            result.append(frame)
            previous = frame
        return result
    if orientation_mode == "minimumTwist":
        first = initial_frame(points[0], tangents[0])
        result = [first]
        current = first
        for point, tangent in zip(points[1:-1], tangents[1:]):
            current = _transport(current, tangent)
            result.append((_v(point), current[1], current[2], current[3]))
        return result
    raise ValueError(f"unsupported sweep orientation mode: {orientation_mode}")


def path_frames(
    points: Sequence[Vec3],
    orientation_mode: str = "minimumTwist",
    station_tangent_overrides: Sequence[Vec3] | None = None,
) -> list[Frame]:
    points = [_v(point) for point in points]
    if orientation_mode == "minimumTwist":
        return minimum_twist_frames(points, station_tangent_overrides)
    if orientation_mode == "followPath":
        return follow_path_frames(points, station_tangent_overrides)
    if orientation_mode == "fixedWorld":
        return fixed_world_frames(points, station_tangent_overrides)
    raise ValueError(f"unsupported sweep orientation mode: {orientation_mode}")


# Descriptive aliases keep the small math API discoverable for callers that
# prefer verb-based names while retaining the concise names used by the CAD
# worker.
compute_segment_tangents = segment_tangents
compute_corner_tangents = corner_tangents
compute_initial_frame = initial_frame
parallel_transport_frames = minimum_twist_frames
compute_follow_path_frames = follow_path_frames
compute_fixed_world_frames = fixed_world_frames
compute_path_frames = path_frames
compute_segment_start_frames = segment_start_frames
