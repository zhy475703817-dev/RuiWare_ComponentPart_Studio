"""Authoritative CAD geometry for resize jobs; never uses display markers."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import sys
from threading import RLock

import numpy as np
from scipy.spatial.transform import Rotation

from ..config import PROJECT_ROOT
from ..reconstruction import editable_parameters, resolved_interfaces
from .models import PartInterface

ENGINE = str(PROJECT_ROOT / "backend" / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)


def matrix(position, rotation):
    result = np.eye(4)
    result[:3, :3] = Rotation.from_euler("XYZ", rotation).as_matrix()
    result[:3, 3] = position
    return result


def exact_bounds(shape, transform=None):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    if transform is not None:
        trsf = gp_Trsf()
        trsf.SetValues(*[float(x) for x in np.asarray(transform)[:3, :].flat])
        shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    values = box.Get()
    return np.array([values[:3], values[3:]], dtype=float)


def transformed_shape(shape, transform):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf
    trsf = gp_Trsf()
    trsf.SetValues(*[float(x) for x in np.asarray(transform)[:3, :].flat])
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def retained_support(face, solid):
    """Trim a source-owned support against the finished CAD after cuts/features."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    operation = BRepAlgoAPI_Common(face, solid)
    if not operation.IsDone(): raise GeometryFailure("无法复核特征加工后的接触面")
    result = operation.Shape()
    if result.IsNull(): raise GeometryFailure("原接触面已被加工特征移除")
    properties = GProp_GProps(); BRepGProp.SurfaceProperties_s(result, properties)
    if properties.Mass() < 1e-8: raise GeometryFailure("原接触面已被加工特征移除")
    return result


@dataclass
class Geometry:
    shape: object
    bounds: np.ndarray
    frames: dict
    interfaces: list
    resolved: list
    diagnostics: list
    definitions: list
    hull: np.ndarray | None = None


class GeometryFailure(ValueError):
    pass


class CadProvider:
    def __init__(self, templates):
        self.templates = templates
        self.cache = {}
        self.lock = RLock()

    def probe(self, reference, parameters):
        """Linearization only. Final acceptance always rebuilds every CAD operation.

        Source-owned frames come from the body and the resolved feature expressions;
        running all repeated hole booleans for each finite difference adds no locator
        information. Never expose this temporary shape as a preview or measurement.
        """
        key = (reference["publicationId"], reference["sha256"], "probe", hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest())
        if key not in self.cache:
            draft, material = self.templates[key[:2]]
            if len(self.cache) >= 256: self.cache.pop(next(iter(self.cache)))
            self.cache[key] = self._build(draft, material, parameters, editable_parameters(draft), probe=True)
        return self.cache[key]

    def evaluate(self, reference, parameters):
        key = (reference["publicationId"], reference["sha256"], hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest())
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            draft, material = self.templates[key[:2]]
            definitions = editable_parameters(draft)
            for definition in definitions:
                value = parameters.get(definition["id"], definition["default"])
                if definition["valueType"] in ("number", "integer"):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                        raise GeometryFailure(f'{definition["label"]}不是有效数值')
                    low, high = definition["minimum"], definition["maximum"]
                    if low is not None and value < low or high is not None and value > high:
                        raise GeometryFailure(f'{definition["label"]}需要 {value:g}，允许范围为 {low} ～ {high} {definition["unit"]}')
                    if definition["valueType"] == "integer" and value != int(value):
                        raise GeometryFailure(f'{definition["label"]}必须为整数')
                if definition["allowedValues"] and value not in definition["allowedValues"]:
                    raise GeometryFailure(f'{definition["label"]}不在允许规格中')
            result = self._build(draft, material, parameters, definitions)
            if len(self.cache) >= 256:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result
            return result

    def _build(self, data, material, parameters, definitions, probe=False):
        from template_core import TemplateDraft, lower_to_plan
        from cad_worker.operators.body_ops import build_body_with_face_map, resolve_face_support, FaceSupport, _support_frame
        from cad_worker.operators.feature_ops import apply_operation, _resolved_support_frame, _mapped_point
        from cad_worker.operators.base_entities import _through_penetration_for_shape
        from cad_worker.postcheck import check_brep
        draft = TemplateDraft.model_validate(data)
        plan = lower_to_plan(draft, material, overrides=parameters)
        errors = [d.message for d in plan.diagnostics if d.severity == "error"]
        if errors:
            raise GeometryFailure("；".join(errors))
        shape, face_map = build_body_with_face_map(plan.operations[0])
        body = plan.operations[0]
        # The pinned engine records thinwall side faces but omits its extrusion
        # caps. Recover only the unique planar cap of that known source operation.
        if body.operator == 'sketch.centerline_thinwall_extrude' or body.operator == 'profile.open_profile_tube_extrude' and body.arguments.get('sketch', {}).get('profileMode') == 'centerlineThinWall':
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopAbs import TopAbs_FACE
            from OCP.TopoDS import TopoDS
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            plane = body.arguments.get('sketch', {}).get('plane', 'XY')
            axis = {'XY': 2, 'XZ': 1, 'YZ': 0}.get(plane)
            if axis is not None:
                limits = exact_bounds(shape)
                caps = {'start': [], 'end': []}
                explorer = TopExp_Explorer(shape, TopAbs_FACE)
                while explorer.More():
                    face_shape = TopoDS.Face_s(explorer.Current()); frame = _support_frame(face_shape)
                    if frame is not None and abs(frame[3][axis]) > 1-1e-8:
                        props = GProp_GProps(); BRepGProp.SurfaceProperties_s(face_shape, props); p = props.CentreOfMass()
                        center = [p.X(), p.Y(), p.Z()]
                        for index, side in enumerate(('start', 'end')):
                            if abs(center[axis]-limits[index, axis]) < 1e-6:
                                caps[side].append((face_shape, frame))
                    explorer.Next()
                for side, candidates in caps.items():
                    if len(candidates) != 1: continue
                    face_shape, (origin, u, v, normal) = candidates[0]
                    face_map['@extrusion-'+side] = [FaceSupport(supportFace=face_shape, origin=origin, uDirection=u, vDirection=v, normal=normal,
                        sourceEntityId='@extrusion-cap', operationId=body.id, profileSketchId=body.arguments.get('profileSketchId', 'body-profile'), kind='profileRegion', capSide=side)]
        for operation in ([] if probe else plan.operations[1:]):
            shape = apply_operation(shape, operation, _through_penetration_for_shape(shape, 100), face_map=face_map)
        valid, properties, solids = check_brep(shape)
        if not valid or solids != 1 or properties.Mass() <= 0:
            raise GeometryFailure("零件重建未通过实体检查")
        resolved, diagnostics = resolved_interfaces(data, material, parameters)
        if any(d.get("severity") == "error" for d in diagnostics):
            raise GeometryFailure("接口重建失败：" + "；".join(d.get("message", "") for d in diagnostics if d.get("severity") == "error"))
        declarations = {i["id"]: i for i in data.get("interfaces", [])}
        faces = {f.id: f for f in draft.geometryRecipe.semanticFaces}
        frames, interfaces = {}, []
        # Stable source-owned support faces, also useful for parts without declared end ports.
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        grouped = {}
        for supports in face_map.values():
            for support in supports:
                key = (support.operationId, support.profileSketchId, support.sourceEntityId, support.capSide or "side")
                grouped.setdefault(key, []).append(support)
        retained = {}
        def finished_support(support):
            if probe: return support.supportFace
            key = (support.operationId, support.profileSketchId, support.sourceEntityId, support.capSide)
            if key not in retained:
                retained[key] = retained_support(support.supportFace, shape)
            return retained[key]
        for key, supports in grouped.items():
            if len(supports) != 1: continue
            support = supports[0]
            try: contact = finished_support(support)
            except GeometryFailure: continue
            props = GProp_GProps(); BRepGProp.SurfaceProperties_s(support.supportFace, props)
            p = props.CentreOfMass()
            frames["@support:" + ":".join(key)] = {"center": np.array([p.X(), p.Y(), p.Z()]), "normal": np.array(support.normal), "u": np.array(support.uDirection), "source": key, "contactShape": contact}
        accepted_fields = set(PartInterface.model_fields)
        for occurrence in resolved:
            declaration = declarations.get(occurrence["sourceInterfaceId"], {})
            source = {k: deepcopy(v) for k, v in declaration.items() if k in accepted_fields and v is not None}
            source.update(id=occurrence["id"], name=declaration.get("name", occurrence["id"]), type=source.get("type", "plane"), tags=source.get("tags", []))
            for field in ("geometryRefs", "parameterRefs", "region", "declarationMode", "interfaceType"):
                if occurrence.get(field) is not None:
                    source[field] = deepcopy(occurrence[field])
            interfaces.append(PartInterface.model_validate(source).model_dump(exclude_none=True))
            refs = occurrence.get("geometryRefs", [])
            if len(refs) != 1 or refs[0] not in faces:
                continue
            face = faces[refs[0]]
            try:
                if face.locator is None:
                    # Legacy extrusion caps have a canonical semantic output and
                    # source operation. Accept only one actual source-owned cap.
                    side = {"part.endFace.start": "start", "part.endFace.end": "end"}.get(face.id)
                    candidates = [s for values in grouped.values() for s in values if side and s.operationId == face.sourceOperationId and s.capSide == side]
                    if len(candidates) != 1:
                        diagnostics.append({"severity": "warning", "code": "RESIZE_FRAME_MISSING", "message": f'{occurrence["id"]} 缺少唯一的真实几何定位来源'})
                        continue
                    support = candidates[0]
                else:
                    support = resolve_face_support(face_map, face.locator)
                finished_support(support)
                region = occurrence.get("region")
                if region:
                    frame = _resolved_support_frame(support, face.hostFrame, None, None)
                    point = _mapped_point(region["uStart"], region["vStart"], frame)
                    center = np.array([point.X(), point.Y(), point.Z()])
                else:
                    from OCP.BRepGProp import BRepGProp
                    from OCP.GProp import GProp_GProps
                    props = GProp_GProps()
                    BRepGProp.SurfaceProperties_s(support.supportFace, props)
                    p = props.CentreOfMass()
                    center = np.array([p.X(), p.Y(), p.Z()])
                normal = np.array(support.normal)
                if source.get('type') == 'axis' and not occurrence.get('sourceFeatureRuleId'):
                    axis = declaration.get('referenceFrame', {}).get('axis')
                    if axis not in ('x', 'y', 'z'):
                        diagnostics.append({'severity': 'warning', 'code': 'RESIZE_AXIS_MISSING', 'message': occurrence['id']+' 缺少明确轴方向'})
                        continue
                    normal = np.eye(3)['xyz'.index(axis)]
                frames[occurrence["id"]] = {"center": center, "normal": normal, "u": np.array(support.uDirection),
                    "featureAxis": bool(region and occurrence.get("sourceFeatureRuleId")),
                    "tolerances": declaration.get("referenceFrame", {}).get("geometryTolerances", {}),
                    "source": (occurrence["sourceInterfaceId"], occurrence.get("sourceFeatureRuleId"), occurrence.get("sourceFeatureId"), tuple(refs))}
            except (RuntimeError, ValueError) as error:
                diagnostics.append({"severity": "warning", "code": "RESIZE_FRAME_UNRESOLVED", "message": f'{occurrence["id"]}: {error}'})
        # A tight convex support cloud accelerates trial rotations. It is never
        # the acceptance measurement: the solver rechecks AddOptimal on full CAD.
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.BRep import BRep_Tool
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
        from OCP.TopLoc import TopLoc_Location
        from scipy.spatial import ConvexHull
        BRepMesh_IncrementalMesh(shape, .01, False, .1, False).Perform()
        vertices = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()), location)
            if mesh:
                for index in range(1, mesh.NbNodes()+1):
                    p = mesh.Node(index).Transformed(location.Transformation())
                    vertices.append([p.X(), p.Y(), p.Z()])
            explorer.Next()
        hull = None
        if vertices:
            points = np.unique(np.round(vertices, 8), axis=0)
            hull = points[ConvexHull(points).vertices]
        return Geometry(shape, exact_bounds(shape), frames, interfaces, resolved, diagnostics, definitions, hull)
