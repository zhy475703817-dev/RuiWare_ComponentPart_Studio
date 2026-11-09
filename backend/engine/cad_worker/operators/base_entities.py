from __future__ import annotations

import math

from OCP.Bnd import Bnd_Box
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec


def _through_penetration_for_shape(shape, minimum: float) -> float:
    """Return a through-tool length that encloses the current bounded shape.

    Locator-based tools start half this distance outside their resolved support
    face, so twice the bounding-box diagonal reaches beyond every possible
    opposite point.  The absolute-coordinate bound also keeps the legacy
    fixed ``+/- penetration / 2`` start planes outside translated geometry.
    """

    lower_bound = max(0.0, float(minimum))
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bounds, False, False)
    if bounds.IsVoid():
        return lower_bound

    xmin, ymin, zmin, xmax, ymax, zmax = bounds.Get()
    values = (xmin, ymin, zmin, xmax, ymax, zmax)
    if not all(math.isfinite(value) for value in values):
        return lower_bound

    diagonal = math.sqrt(
        (xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2
    )
    absolute_extent = max(abs(value) for value in values)
    half_span = max(diagonal, absolute_extent)
    clearance = max(1.0, half_span * 1e-6)
    return max(lower_bound, 2.0 * (half_span + clearance))


def _box(x: float, y: float, z: float, dx: float, dy: float, dz: float):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def _host_point(u: float, v: float, host_face: str, penetration: float) -> gp_Pnt:
    """Map a feature's stable host-face U/V coordinates into the global CAD frame."""
    half = penetration / 2
    return {
        "negativeY": gp_Pnt(u, -half, v),
        "positiveY": gp_Pnt(u, half, v),
        "negativeX": gp_Pnt(-half, u, v),
        "positiveX": gp_Pnt(half, u, v),
        "negativeZ": gp_Pnt(u, v, -half),
        "positiveZ": gp_Pnt(u, v, half),
    }[host_face]


def _host_direction(host_face: str) -> gp_Dir:
    return {
        "negativeY": gp_Dir(0, 1, 0), "positiveY": gp_Dir(0, -1, 0),
        "negativeX": gp_Dir(1, 0, 0), "positiveX": gp_Dir(-1, 0, 0),
        "negativeZ": gp_Dir(0, 0, 1), "positiveZ": gp_Dir(0, 0, -1),
    }[host_face]


def _host_vector(host_face: str, penetration: float) -> gp_Vec:
    direction = _host_direction(host_face)
    return gp_Vec(direction.X() * penetration, direction.Y() * penetration, direction.Z() * penetration)


def _through_polygon(vertices: list[tuple[float, float]], host_face: str, penetration: float):
    polygon = BRepBuilderAPI_MakePolygon()
    for u, v in vertices:
        polygon.Add(_host_point(u, v, host_face, penetration))
    polygon.Close()
    if not polygon.IsDone():
        raise RuntimeError("Polygonal cutout wire construction failed")
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, _host_vector(host_face, penetration)).Shape()


def _fuse(*shapes):
    result = shapes[0]
    for shape in shapes[1:]:
        operation = BRepAlgoAPI_Fuse(result, shape)
        operation.SetFuzzyValue(1e-7)
        operation.Build()
        if not operation.IsDone():
            raise RuntimeError("Boolean fuse failed")
        result = operation.Shape()
    return result


def _point_3d(u: float, v: float, plane: str = "XY", offset: float = 0.0) -> gp_Pnt:
    return {
        "XY": gp_Pnt(u, v, offset),
        "XZ": gp_Pnt(u, offset, v),
        "YZ": gp_Pnt(offset, u, v),
    }[plane]


def _vector_3d(u: float, v: float, plane: str = "XY") -> gp_Dir:
    return {
        "XY": gp_Dir(u, v, 0),
        "XZ": gp_Dir(u, 0, v),
        "YZ": gp_Dir(0, u, v),
    }[plane]


def _profile_frame(origin: tuple[float, float, float], tangent: tuple[float, float, float]):
    """Build a stable, minimum-twist initial frame perpendicular to the spine."""
    ox, oy, oz = origin
    tx, ty, tz = tangent
    length = math.sqrt(tx * tx + ty * ty + tz * tz)
    if length <= 1e-9:
        raise RuntimeError("Sweep path tangent must be non-zero")
    tx, ty, tz = tx / length, ty / length, tz / length
    # Keep X as the reference direction where possible; fall back to Y when
    # the tangent is parallel to X. This is parallel transport with no twist
    # parameter: the frame is chosen once at the path start and OCCT carries it.
    rx, ry, rz = (1.0, 0.0, 0.0) if abs(tx) < 0.9 else (0.0, 1.0, 0.0)
    dot = rx * tx + ry * ty + rz * tz
    ex, ey, ez = rx - dot * tx, ry - dot * ty, rz - dot * tz
    norm = math.sqrt(ex * ex + ey * ey + ez * ez)
    ex, ey, ez = ex / norm, ey / norm, ez / norm
    # e2 = tangent × e1
    fx, fy, fz = ty * ez - tz * ey, tz * ex - tx * ez, tx * ey - ty * ex
    return (ox, oy, oz), (ex, ey, ez), (fx, fy, fz), (tx, ty, tz)


def _map_profile_point(u: float, v: float, plane: str, frame, offset: float = 0.0):
    if frame is None:
        return _point_3d(u, v, plane, offset)
    origin, e1, e2, _tangent = frame
    return gp_Pnt(origin[0] + u * e1[0] + v * e2[0], origin[1] + u * e1[1] + v * e2[1], origin[2] + u * e1[2] + v * e2[2])


def _normal_vector(plane: str, length: float) -> gp_Vec:
    return {"XY": gp_Vec(0, 0, length), "XZ": gp_Vec(0, length, 0), "YZ": gp_Vec(length, 0, 0)}[plane]


def _primitive_edge(primitive, plane: str = "XY", scale: float = 1.0, offset: float = 0.0, frame=None):
    kind = primitive["type"]
    if kind == "line":
        a, b = primitive["start"], primitive["end"]
        return BRepBuilderAPI_MakeEdge(_map_profile_point(a["x"] * scale, a["y"] * scale, plane, frame, offset), _map_profile_point(b["x"] * scale, b["y"] * scale, plane, frame, offset)).Edge()
    if kind == "arc":
        center, radius = primitive["center"], primitive["radius"]
        start_value = primitive.get("startAngle")
        end_value = primitive.get("endAngle")
        start_angle = math.radians(0 if start_value is None else start_value)
        end_angle = math.radians(90 if end_value is None else end_value)
        ccw = (end_angle - start_angle) % (2 * math.pi)
        if ccw == 0:
            ccw = 2 * math.pi
        large_arc = primitive.get("largeArc")
        if large_arc is None:
            large_arc = ccw > math.pi
        # Midpoint of the selected arc (minor/major), traveling CCW when that path matches.
        travels_ccw = (ccw > math.pi) if large_arc else (ccw < math.pi or abs(ccw - math.pi) < 1e-9)
        if travels_ccw:
            middle_angle = start_angle + ccw / 2
        else:
            middle_angle = start_angle - (2 * math.pi - ccw) / 2
        points = [
            _map_profile_point((center["x"] + radius * math.cos(angle)) * scale, (center["y"] + radius * math.sin(angle)) * scale, plane, frame, offset)
            for angle in (start_angle, middle_angle, end_angle)
        ]
        return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(*points).Value()).Edge()
    if kind == "circle":
        center = primitive["center"]
        axis = {"XY": gp_Dir(0, 0, 1), "XZ": gp_Dir(0, -1, 0), "YZ": gp_Dir(1, 0, 0)}[plane]
        circle_origin = _map_profile_point(center["x"] * scale, center["y"] * scale, plane, frame, offset)
        circle_axis = gp_Dir(*(frame[3] if frame is not None else (axis.X(), axis.Y(), axis.Z())))
        return BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(circle_origin, circle_axis), primitive["radius"] * scale)).Edge()
    raise ValueError(f"Unsupported sketch primitive: {kind}")


def _region_wire(sketch, region, scale: float = 1.0, offset: float = 0.0, frame=None):
    primitives = {item["id"]: item for item in sketch["primitives"] if not item.get("construction")}
    wire_builder = BRepBuilderAPI_MakeWire()
    for reference in region["boundaryRefs"]:
        wire_builder.Add(_primitive_edge(primitives[reference], sketch.get("plane", "XY"), scale, offset, frame))
    if not wire_builder.IsDone():
        raise RuntimeError(f"Sketch region wire failed: {region['id']}")
    return wire_builder.Wire()


def _region_faces(sketch):
    additive_faces, subtractive_faces = [], []
    for region in sketch["regions"]:
        face = BRepBuilderAPI_MakeFace(_region_wire(sketch, region)).Face()
        (subtractive_faces if region["operation"] == "subtract" else additive_faces).append(face)
    return additive_faces, subtractive_faces


def _material_face(sketch):
    additive_faces, subtractive_faces = _region_faces(sketch)
    if not additive_faces:
        raise RuntimeError("Sketch has no additive material region")
    face = additive_faces[0]
    for other in additive_faces[1:]:
        face = BRepAlgoAPI_Fuse(face, other).Shape()
    for hole in subtractive_faces:
        face = BRepAlgoAPI_Cut(face, hole).Shape()
    return face

