from __future__ import annotations

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

from .base_entities import _fuse, _host_direction, _host_point, _through_polygon
from .body_ops import FaceMap, FaceSupport, build_body, resolve_face_support


FEATURE_OPERATORS = {
    "machining.circular_through_hole",
    "machining.straight_slot_through",
    "machining.rectangular_through_cutout",
    "machining.polygonal_through_cutout",
}


_HOST_FRAME_UV_DIRECTIONS = {
    "negativeY": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "positiveY": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "negativeX": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "positiveX": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "negativeZ": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "positiveZ": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}

type _SupportFrame = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
    float,
]


def _dot(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    return sum(left * right for left, right in zip(first, second))


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = _dot(vector, vector) ** 0.5
    if magnitude <= 1e-12:
        raise RuntimeError("Semantic face support has a zero-length direction")
    return tuple(component / magnitude for component in vector)


def _resolved_support_frame(
    support: FaceSupport,
    host_frame: str,
    u_start: float | None = 0.0,
    v_start: float | None = 0.0,
) -> _SupportFrame:
    """Combine topology-selected plane placement with the authored U/V frame.

    The support face fixes which physical plane is used.  ``hostFrame`` keeps
    the existing six-direction contract for the in-plane U/V axes; it no
    longer selects a face by itself.
    """

    normal = _unit(tuple(float(component) for component in support.normal))
    u_direction = _unit(tuple(float(component) for component in support.uDirection))
    v_direction = _unit(tuple(float(component) for component in support.vDirection))
    if host_frame == "profile":
        if abs(_dot(u_direction, v_direction)) > 1e-7:
            raise RuntimeError(
                f"Semantic face source {support.sourceEntityId} has non-orthogonal "
                "profile support U/V directions"
            )
        if abs(_dot(normal, u_direction)) > 1e-7 or abs(_dot(normal, v_direction)) > 1e-7:
            raise RuntimeError(
                f"Semantic face source {support.sourceEntityId} profile frame is not planar"
            )
        support_origin = tuple(float(component) for component in support.origin)
        resolved_u_start = (
            _dot(support_origin, u_direction) if u_start is None else float(u_start)
        )
        resolved_v_start = (
            _dot(support_origin, v_direction) if v_start is None else float(v_start)
        )
        return (
            support_origin, u_direction, v_direction, normal,
            resolved_u_start, resolved_v_start,
        )
    try:
        expected_u_direction, expected_v_direction = _HOST_FRAME_UV_DIRECTIONS[host_frame]
    except KeyError as error:
        raise RuntimeError(f"Unsupported semantic host frame: {host_frame}") from error
    if (
        _dot(u_direction, expected_u_direction) < 1 - 1e-7
        or _dot(v_direction, expected_v_direction) < 1 - 1e-7
    ):
        raise RuntimeError(
            f"Semantic face source {support.sourceEntityId} support U/V directions "
            f"are not compatible with host frame {host_frame}"
        )
    if abs(_dot(u_direction, v_direction)) > 1e-7:
        raise RuntimeError(
            f"Semantic face source {support.sourceEntityId} has non-orthogonal "
            "support U/V directions"
        )
    if abs(_dot(normal, u_direction)) > 1e-7 or abs(_dot(normal, v_direction)) > 1e-7:
        raise RuntimeError(
            f"Semantic face source {support.sourceEntityId} is not compatible "
            f"with host frame {host_frame}"
        )
    support_origin = tuple(float(component) for component in support.origin)
    resolved_u_start = (
        _dot(support_origin, u_direction) if u_start is None else float(u_start)
    )
    resolved_v_start = (
        _dot(support_origin, v_direction) if v_start is None else float(v_start)
    )
    return (
        support_origin,
        u_direction,
        v_direction,
        normal,
        resolved_u_start,
        resolved_v_start,
    )


def _mapped_point(
    u: float,
    v: float,
    frame: _SupportFrame,
    normal_offset: float = 0.0,
) -> gp_Pnt:
    origin, u_direction, v_direction, normal, u_start, v_start = frame
    return gp_Pnt(*(
        origin[index]
        + (float(u) - u_start) * u_direction[index]
        + (float(v) - v_start) * v_direction[index]
        + normal_offset * normal[index]
        for index in range(3)
    ))


def _mapped_through_polygon(
    vertices: list[tuple[float, float]],
    frame: _SupportFrame,
    penetration: float,
):
    polygon = BRepBuilderAPI_MakePolygon()
    for u, v in vertices:
        polygon.Add(_mapped_point(u, v, frame, penetration / 2))
    polygon.Close()
    if not polygon.IsDone():
        raise RuntimeError("Polygonal cutout wire construction failed")
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    normal = frame[3]
    return BRepPrimAPI_MakePrism(
        face,
        gp_Vec(*(-component * penetration for component in normal)),
    ).Shape()


def _resolve_semantic_face_support(arguments, face_map: FaceMap) -> FaceSupport:
    """Resolve semanticFaceId -> locator -> authored source support."""

    semantic_face_id = str(arguments.get("semanticFaceId") or "<unknown>")
    try:
        support = resolve_face_support(face_map, arguments["locator"])
    except RuntimeError as error:
        raise RuntimeError(
            f"Semantic face {semantic_face_id} could not resolve its real support: {error}"
        ) from error
    resolved_source_entity_id = arguments.get("resolvedSourceEntityId")
    if (
        resolved_source_entity_id is not None
        and str(resolved_source_entity_id) != support.sourceEntityId
    ):
        raise RuntimeError(
            f"Semantic face {semantic_face_id} resolved source "
            f"{resolved_source_entity_id!r}, but its locator selected "
            f"{support.sourceEntityId!r}"
        )
    return support


def apply_operation(
    shape,
    operation,
    penetration: float,
    face_map: FaceMap | None = None,
):
    arguments = operation.arguments
    if operation.operator not in FEATURE_OPERATORS:
        addition = build_body(operation)
        return _fuse(shape, addition)

    # ``hostFrame`` is the current six-direction compatibility contract;
    # ``hostFace`` remains readable for canonical plans that predate it.
    host_frame = str(
        arguments.get("hostFrame", arguments.get("hostFace", "negativeY"))
    )
    host_face = host_frame
    locator = arguments.get("locator")
    support_frame = None
    if locator is not None:
        if face_map is None:
            raise RuntimeError(
                f"Semantic face {arguments.get('semanticFaceId', '<unknown>')} on "
                f"{operation.id} requires a source face map"
            )
        support = _resolve_semantic_face_support(arguments, face_map)
        support_frame = _resolved_support_frame(
            support,
            host_frame,
            arguments.get("uStart"),
            arguments.get("vStart"),
        )

    if operation.operator == "machining.circular_through_hole":
        if support_frame is None:
            axis = gp_Ax2(
                _host_point(arguments["x"], arguments["z"], host_face, penetration),
                _host_direction(host_face),
            )
        else:
            normal = support_frame[3]
            axis = gp_Ax2(
                _mapped_point(
                    arguments["x"], arguments["z"], support_frame, penetration / 2
                ),
                gp_Dir(*(-component for component in normal)),
            )
        tool = BRepPrimAPI_MakeCylinder(
            axis,
            arguments["diameter"] / 2,
            penetration,
        ).Shape()
    elif operation.operator == "machining.straight_slot_through":
        radius = arguments["width"] / 2
        straight = max(0.0, arguments["length"] - arguments["width"])
        center_a = arguments["z"] - straight / 2
        center_b = arguments["z"] + straight / 2
        if support_frame is None:
            axis_a = gp_Ax2(
                _host_point(arguments["x"], center_a, host_face, penetration),
                _host_direction(host_face),
            )
            axis_b = gp_Ax2(
                _host_point(arguments["x"], center_b, host_face, penetration),
                _host_direction(host_face),
            )
        else:
            normal = support_frame[3]
            inward = gp_Dir(*(-component for component in normal))
            axis_a = gp_Ax2(
                _mapped_point(arguments["x"], center_a, support_frame, penetration / 2),
                inward,
            )
            axis_b = gp_Ax2(
                _mapped_point(arguments["x"], center_b, support_frame, penetration / 2),
                inward,
            )
        cylinder_a = BRepPrimAPI_MakeCylinder(axis_a, radius, penetration).Shape()
        cylinder_b = BRepPrimAPI_MakeCylinder(axis_b, radius, penetration).Shape()
        vertices = [
            (arguments["x"] - radius, center_a), (arguments["x"] + radius, center_a),
            (arguments["x"] + radius, center_b), (arguments["x"] - radius, center_b),
        ]
        bridge = (
            _through_polygon(vertices, host_face, penetration)
            if support_frame is None
            else _mapped_through_polygon(vertices, support_frame, penetration)
        )
        tool = _fuse(cylinder_a, cylinder_b, bridge)
    elif operation.operator == "machining.rectangular_through_cutout":
        vertices = [
            (arguments["x"] - arguments["width"] / 2, arguments["z"] - arguments["height"] / 2),
            (arguments["x"] + arguments["width"] / 2, arguments["z"] - arguments["height"] / 2),
            (arguments["x"] + arguments["width"] / 2, arguments["z"] + arguments["height"] / 2),
            (arguments["x"] - arguments["width"] / 2, arguments["z"] + arguments["height"] / 2),
        ]
        tool = (
            _through_polygon(vertices, host_face, penetration)
            if support_frame is None
            else _mapped_through_polygon(vertices, support_frame, penetration)
        )
    elif operation.operator == "machining.polygonal_through_cutout":
        vertices = [(float(point[0]), float(point[1])) for point in arguments.get("polygonVertices", [])]
        tool = (
            _through_polygon(vertices, host_face, penetration)
            if support_frame is None
            else _mapped_through_polygon(vertices, support_frame, penetration)
        )
    else:
        raise ValueError(f"Unsupported feature operator: {operation.operator}")

    cut = BRepAlgoAPI_Cut(shape, tool)
    cut.Build()
    if not cut.IsDone():
        raise RuntimeError(f"Boolean cut failed at {operation.id}")
    return cut.Shape()
