"""Bounded, instance-scoped geometric resize with explicit verification."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import itertools
import time
import warnings

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .resize_geometry import GeometryFailure, exact_bounds, matrix, transformed_shape, retained_support
from .geometry_common import ResizeFailure, fingerprint, template_key
from .brace_generation import materialize_brace_generation_interfaces, expand_brace_generation_rules
from .brace_policy import active_brace_rules, brace_relation_owner


def structure_changes(before, after, prefix=()):
    """Report actual additions/removals, descending through shared instances."""
    original = {m['id']: m for m in before['assembly']['members']}
    current = {m['id']: m for m in after['assembly']['members']}
    result = {
        'added': [{'path': [*prefix, identifier], 'name': member['name']}
                  for identifier, member in current.items() if identifier not in original],
        'removed': [{'path': [*prefix, identifier], 'name': member['name']}
                    for identifier, member in original.items() if identifier not in current],
    }
    for identifier in original:
        if identifier not in current:
            continue
        old, new = original[identifier].get('instanceDocument'), current[identifier].get('instanceDocument')
        # Resolving an existing publication for the first time does not add
        # physical members. Only compare saved child instances on both sides.
        if old and new:
            children = structure_changes(old, new, (*prefix, identifier))
            for action in result: result[action].extend(children[action])
    return result


def flatten(document, publications, parent=None, prefix=(), ancestors=()):
    parent = np.eye(4) if parent is None else parent
    leaves, endpoints, connections, bindings = [], {}, [], []
    # Freeze the existing physical repetitions into independent, stable instances.
    # Keep the source patterns as design provenance; geometry mode never repeats them again.
    if document.get("patterns") and not document.get("geometryDesign"):
        parameters = {p["id"]: p["value"] for p in document["parameters"]}
        originals = {m["id"]: m for m in document["assembly"]["members"]}
        for pattern in document["patterns"]:
            count = parameters.get(pattern["countParameterId"])
            spacing = parameters.get(pattern["spacingParameterId"])
            if count is None or count < 1 or count != int(count) or count > 150 or spacing is None:
                raise ResizeFailure("阵列数量或间距无效，请先修正构成规则", "RESIZE_PATTERN_INVALID")
            for index in range(1, int(count)):
                mapping = {}
                for id in pattern["memberIds"]:
                    clone = deepcopy(originals[id])
                    clone["id"] = f'{id}#{pattern["id"]}-{index}'
                    if clone["id"] in originals:
                        raise ResizeFailure("阵列实例标识重复", "RESIZE_PATTERN_INVALID")
                    mapping[id] = clone["id"]
                    clone["position"]["xyz".index(pattern["axis"])] += spacing * index
                    document["assembly"]["members"].append(clone)
                for mate in list(document["assembly"]["mates"]):
                    if mate["from"]["memberId"] in mapping and mate["to"]["memberId"] in mapping:
                        clone = deepcopy(mate); clone["id"] += f'#{pattern["id"]}-{index}'
                        for side in ("from", "to"): clone[side]["memberId"] = mapping[clone[side]["memberId"]]
                        document["assembly"]["mates"].append(clone)
                for binding in list(document["bindings"]):
                    if binding["memberId"] in mapping:
                        document["bindings"].append({**binding, "id": binding["id"] + f'#{pattern["id"]}-{index}', "memberId": mapping[binding["memberId"]]})
    for member in document["assembly"]["members"]:
        path = (*prefix, member["id"])
        world = parent @ matrix(member["position"], member["rotation"])
        if member["template"].get("templateKind") == "component":
            reference = member["template"]
            identity = template_key(reference)
            if identity in ancestors or len(path) > 16:
                raise ResizeFailure("组件循环引用或嵌套超过16层", "RESIZE_NESTING")
            source = publications.get(identity)
            if source is None:
                raise ResizeFailure("缺少子组件固定发布版本", "RESIZE_REFERENCE")
            nested = deepcopy(member.get("instanceDocument") or source)
            if not nested.get("geometryDesign"):
                for parameter in nested["parameters"]:
                    if parameter["id"] in member["parameterValues"]:
                        parameter["value"] = member["parameterValues"][parameter["id"]]
                for binding in nested["bindings"]:
                    p = next((p for p in nested["parameters"] if p["id"] == binding["parameterId"]), None)
                    m = next((m for m in nested["assembly"]["members"] if m["id"] == binding["memberId"]), None)
                    if p and m and p["value"] is not None:
                        m["parameterValues"][binding["memberParameterId"]] = p["value"]
            member["instanceDocument"] = nested
            child_leaves, child_endpoints, child_connections, child_bindings = flatten(nested, publications, world, path, (*ancestors, identity))
            leaves.extend(child_leaves); endpoints.update(child_endpoints); connections.extend(child_connections); bindings.extend(child_bindings)
            for port in nested["ports"]:
                source_key = (*path, port["source"]["memberId"]), port["source"]["interfaceId"]
                if source_key in endpoints:
                    endpoints[(path, port["id"])] = endpoints[source_key]
        else:
            index = len(leaves)
            # Endpoints use paths, not list offsets, so siblings and nested instances cannot alias.
            leaves.append({"path": path, "member": member, "parent": parent, "world": world})
            for interface in member["interfaces"]:
                endpoints[(path, interface["id"])] = (path, interface["id"])
        if len(leaves) > 150:
            raise ResizeFailure("本次超过150个叶子零件，请分批调整子组件", "RESIZE_RESOURCE_LIMIT")
    for mate in document["assembly"]["mates"]:
        a = endpoints.get(((*prefix, mate["from"]["memberId"]), mate["from"]["interfaceId"]))
        b = endpoints.get(((*prefix, mate["to"]["memberId"]), mate["to"]["interfaceId"]))
        if not a or not b:
            raise ResizeFailure("内部连接引用无法映射到零件实例", "RESIZE_INTERFACE_MISSING")
        connections.append({"id": "/".join((*prefix, mate["id"])), "a": {"path": list(a[0]), "interfaceId": a[1]}, "b": {"path": list(b[0]), "interfaceId": b[1]},
                            "mode": "axis" if mate["relation"] == "同轴" else "plane" if mate["relation"] == "贴合" else "coincident", "origin": "existing", "offset": [0, 0, mate["offset"]], "normalDot": -1,
                            "_normalOffset": mate['offset'] if mate['relation'] == '重合' else None})
    for binding in document["bindings"]:
        parameter = next((p for p in document["parameters"] if p["id"] == binding["parameterId"]), None)
        bindings.append({**binding, "path": (*prefix, binding["memberId"]), "group": (*prefix, binding["parameterId"]), "definition": parameter})
    for slot in document['slots']:
        if not slot.get('memberId') or not slot.get('interfaceId') or not slot.get('baseInterface'): continue
        base = slot['baseInterface']
        a = endpoints.get(((*prefix, base['memberId']), base['interfaceId']))
        b = endpoints.get(((*prefix, slot['memberId']), slot['interfaceId']))
        if not a or not b: raise ResizeFailure('槽位来源接口无法定位：'+slot['name'], 'RESIZE_INTERFACE_MISSING')
        connections.append({'id': '/'.join((*prefix, 'slot-'+slot['id'])), 'a': {'path': list(a[0]), 'interfaceId': a[1]}, 'b': {'path': list(b[0]), 'interfaceId': b[1]}, 'mode': 'preserve-offset', 'origin': 'existing', 'offset': [0, 0, 0], '_captureOffset': True})
    if prefix:
        for relation in (document.get("geometryDesign") or {}).get("relations", []):
            clone = deepcopy(relation)
            clone["id"] = "/".join((*prefix, relation["id"]))
            for side in ("a", "b"): clone[side]["path"] = [*prefix, *clone[side]["path"]]
            if clone["mode"] == "preserve-offset": clone["offset"] = (parent[:3, :3] @ clone["offset"]).tolist()
            connections.append(clone)
    return leaves, endpoints, connections, bindings


@dataclass
class _Measurement:
    base_parameters: dict
    base_transforms: dict
    baseline: dict
    base_frames: dict
    base_boxes: list
    bounds: np.ndarray
    original_size: np.ndarray
    target: np.ndarray
    inspection: dict


@dataclass
class _Constraints:
    locks: list
    required: set
    guard_occurrences: dict
    anchor: np.ndarray
    scale: np.ndarray
    desired_centers: dict
    grounded: set
    symmetries: list


@dataclass
class _VariableSpace:
    variables: list
    x0: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    discrete: list
    seed: np.ndarray
    influences: list
    sensitivities: dict
    guard_bases: dict
    guard_probes: dict
    guard_minimums: dict


class ResizeSolver:
    def __init__(self, document, provider, publications, request, cancelled=lambda: False, progress=lambda message: None, inspected=None):
        self.original_document = deepcopy(document)
        self.document, self.provider, self.publications = deepcopy(document), provider, publications
        self.request, self.cancelled, self.progress = deepcopy(request), cancelled, progress
        if self.request.get('inspectOnly'):
            # Inspection always measures the saved model, including callers
            # that reuse a resize body while preparing a recovery step.
            self.request.pop('dimensions', None)
        elif self.request.get('autoFillBraces') is not None:
            if self.request['autoFillBraces'] and not self.document.get('braceGenerationRules'):
                raise ResizeFailure('当前柱片尚未配置撑杆生成规则，请先让AI依据真实接口配置规则', 'BRACE_RULE_REQUIRED')
            for rule in self.document.get('braceGenerationRules', []):
                rule['enabled'] = self.request['autoFillBraces']
        self.inspected = inspected
        self.started = time.monotonic()
        self.check()
        original_brace_members = {
            member['id']: member for member in self.original_document['assembly']['members']
        } if active_brace_rules(self.document) else {}
        brace_rule_ids = {rule['id'] for rule in active_brace_rules(self.document)}
        self.brace_member_ids = {
            identifier for rule in active_brace_rules(self.document)
            for identifier in (rule['horizontalMemberId'], rule['diagonalMemberId'])
        } | {member['id'] for member in self.document['assembly']['members']
             if member.get('generatedBy') in brace_rule_ids}
        if self.request.get("dimensions"):
            for rule in active_brace_rules(self.document):
                parameter = next((p for p in self.document["parameters"] if p["id"] == rule["heightParameterId"]), None)
                if parameter is not None:
                    parameter["value"] = float(self.request["dimensions"][2])
        brace_geometry = materialize_brace_generation_interfaces(self.document, self.provider)
        self.check()
        brace_relations = expand_brace_generation_rules(self.document, brace_geometry)
        self.preserve_brace_locks(original_brace_members)
        self.brace_member_ids.update(member['id'] for member in self.document['assembly']['members']
                                     if member.get('generatedBy') in brace_rule_ids)
        self.fixed_geometry_members = {
            member_id for rule in active_brace_rules(self.document)
            for member_id in (rule["horizontalMemberId"], rule["diagonalMemberId"])
        }
        self.leaves, self.endpoints, self.relations, self.bindings = flatten(self.document, publications)
        self.relations.extend(brace_relations)
        if len(self.leaves) > 150:
            raise ResizeFailure("本次超过150个叶子零件，请分批调整子组件", "RESIZE_RESOURCE_LIMIT")
        self.by_path = {leaf["path"]: leaf for leaf in self.leaves}
        self.geometry = {}
        self.surrogates = {}
        self.frame_arrays = {}
        self.envelope_corrections = {}
        self.approximate = False

    def preserve_brace_locks(self, originals):
        """Rule expansion must not redefine a user's locked CAD baseline."""
        if not originals:
            return
        design = self.request.get('design') or self.document.get('geometryDesign') or {}
        current = {member['id']: member for member in self.document['assembly']['members']}
        columns = {rule[key] for rule in active_brace_rules(self.document)
                   for key in ('leftColumnMemberId', 'rightColumnMemberId')}
        source_parameters = {p['id']: p['value'] for p in self.original_document['parameters']}
        for lock in design.get('locks', []):
            # Brace rules currently own root members only. Descendant locks
            # remain enforced against the original nested instance in solve().
            if len(lock['path']) != 1 or lock['path'][0] not in originals:
                continue
            identifier = lock['path'][0]
            before, after = originals[identifier], current.get(identifier)
            if after is None:
                message = f"{before['name']}已锁定，本次缩短会移除此撑杆；请保留当前高度或先解除该零件的锁定"
                raise ResizeFailure(message, 'RESIZE_LOCK_CONFLICT', [{**lock, 'message': message}])
            parameter_id = lock.get('parameterId')
            original_values = dict(before['parameterValues'])
            if identifier in columns and not self.original_document.get('geometryDesign'):
                # Before geometry mode, original component bindings define the
                # physical column size. Compare with that original value, not
                # a stale member snapshot or the newly requested height.
                for binding in self.original_document.get('bindings', []):
                    value = source_parameters.get(binding['parameterId'])
                    if binding['memberId'] == identifier and value is not None:
                        original_values[binding['memberParameterId']] = value
            parameters = [parameter_id] if parameter_id else set(original_values) | set(after['parameterValues'])
            for pid in parameters:
                old, new = original_values.get(pid), after['parameterValues'].get(pid)
                if old == new:
                    continue
                # An omitted value means the source template's default, not a
                # free parameter that preprocessing may silently overwrite.
                if old is None or new is None:
                    geometry = self.provider.evaluate(before['template'], before['parameterValues'])
                    definition = next((d for d in geometry.definitions if d['id'] == pid), None)
                    if definition is None:
                        continue  # solve() reports an invalid parameter lock.
                    if old is None: old = definition['default']
                    if new is None: new = definition['default']
                if old != new:
                    message = f"{before['name']}的 {pid} 已锁定为 {old}，目标高度需要改为 {new}；请调整目标高度或先解除该尺寸的锁定"
                    raise ResizeFailure(message, 'RESIZE_LOCK_CONFLICT', [{**lock, 'parameterId': pid, 'before': old, 'after': new, 'message': message}])
            if parameter_id is None:
                # Rigid placement is only a solver seed. A whole-member lock
                # must retain the saved position/rotation and satisfy the new
                # node relations from that pose, or fail without changing it.
                after['position'] = deepcopy(before['position'])
                after['rotation'] = deepcopy(before['rotation'])

    def check(self):
        if self.cancelled():
            raise ResizeFailure("试算已取消", "RESIZE_CANCELLED")
        if time.monotonic() - self.started > 600:
            raise ResizeFailure("本次计算超时，未找到满足条件的结果；可锁定更多无关尺寸后重试", "RESIZE_TIMEOUT")

    def measure(self, parameters, transforms):
        geoms, frames, boxes = {}, {}, []
        for leaf in self.leaves:
            self.check()
            path = leaf["path"]
            transform = transforms[path]
            if self.approximate and path in self.surrogates:
                base, values, probes = self.surrogates[path]
                # Reuse source-frame derivatives throughout a refinement. Batch
                # the many hole coordinates instead of looping per probe/frame.
                cached = self.frame_arrays.get(path)
                if cached is None or cached[0] is not base or cached[1] is not probes:
                    ids, pids = list(base.frames), list(probes)
                    centers = np.array([base.frames[id]["center"] for id in ids], dtype=float).reshape(-1, 3)
                    normals = np.array([base.frames[id]["normal"] for id in ids], dtype=float).reshape(-1, 3)
                    slopes = {}
                    for field in ("center", "normal"):
                        slopes[field] = np.array([[(probes[pid][0].frames[id][field]-base.frames[id][field])/probes[pid][1]
                                                  if id in probes[pid][0].frames else np.zeros(3) for id in ids] for pid in pids],
                                                dtype=float).reshape(len(pids), len(ids), 3)
                    cached = (base, probes, ids, pids, centers, normals, slopes)
                    self.frame_arrays[path] = cached
                _, _, ids, pids, centers, normals, slopes = cached
                if pids:
                    deltas = np.array([parameters[path][pid]-values[pid] for pid in pids])
                    centers = centers + np.einsum('p,pfc->fc', deltas, slopes["center"])
                    normals = normals + np.einsum('p,pfc->fc', deltas, slopes["normal"])
                normals = normals/np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
                local_frames = {id: {**base.frames[id], "center": centers[i], "normal": normals[i]} for i, id in enumerate(ids)}
                geometry = replace(base, frames=local_frames)
                world_centers = centers @ transform[:3, :3].T + transform[:3, 3]
                world_normals = normals @ transform[:3, :3].T
                frames.update({(path, id): {**local_frames[id], "center": world_centers[i], "normal": world_normals[i]} for i, id in enumerate(ids)})
                def envelope(g):
                    if g.hull is not None:
                        points = g.hull @ transform[:3, :3].T + transform[:3, 3]
                        return np.array([points.min(axis=0), points.max(axis=0)])
                    center = transform[:3, :3] @ ((g.bounds[0]+g.bounds[1])/2) + transform[:3, 3]
                    half = np.abs(transform[:3, :3]) @ ((g.bounds[1]-g.bounds[0])/2)
                    return np.array([center-half, center+half])
                box = envelope(base) + (self.envelope_corrections.get(path, 0) if base.hull is None else 0)
                linear_box = envelope(self.linear_bases[path])
                for pid, (probe, step) in probes.items():
                    weight = (parameters[path][pid] - values[pid]) / step
                    if abs(weight) < 1e-12: continue
                    # Probes use the same source body's convex support cloud.
                    box += weight * (envelope(probe) - linear_box)
                boxes.append(box)
            else:
                geometry = self.provider.evaluate(leaf["member"]["template"], parameters[path])
                boxes.append(exact_bounds(geometry.shape, transform))
                for id, frame in geometry.frames.items():
                    frames[(path, id)] = {**frame, "center": transform[:3, :3] @ frame["center"] + transform[:3, 3], "normal": transform[:3, :3] @ frame["normal"]}
            geoms[path] = geometry
        if not boxes:
            raise ResizeFailure("请先加入零件", "RESIZE_EMPTY")
        bounds = np.array([np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)])
        return geoms, frames, boxes, bounds

    def endpoint(self, value):
        key = (tuple(value["path"]), value["interfaceId"])
        return self.endpoints.get(key, key)

    def infer(self, frames):
        design = self.request.get("design") or self.document.get("geometryDesign") or {}
        # Brace mates are rebuilt from the current target height in the solver
        # constructor. A previously saved geometryDesign can still contain
        # generated mates for an older height; treating those as user locks
        # makes a later shrink fail on interfaces that no longer exist.
        brace_rule_ids = {rule['id'] for rule in active_brace_rules(self.document)}
        user_relations = [
            relation for relation in design.get("relations", [])
            if not (relation.get('origin') != 'user' and brace_relation_owner(relation.get('id')) in brace_rule_ids)
            and not (relation.get('origin') == 'inferred' and any(
                endpoint.get('path', [None])[0] in self.brace_member_ids
                for endpoint in (relation['a'], relation['b']) if endpoint.get('path')))
        ]
        used = set()
        selected = {self.endpoint(r[side]) for r in self.relations + user_relations for side in ("a", "b")}
        for relation in self.relations + user_relations:
            a, b = self.endpoint(relation["a"]), self.endpoint(relation["b"])
            if a not in frames or b not in frames:
                raise ResizeFailure("连接接口缺少真实CAD定位，无法计算：" + relation["id"], "RESIZE_INTERFACE_MISSING")
            used.add(frozenset([a, b]))
        self.relations.extend(deepcopy(user_relations))
        candidates, choices = [], []
        keys = [key for key in frames if not key[1].startswith("@support:")]
        for a in keys:
            if a in selected: continue
            nearby = []
            for b in keys:
                if a[0] == b[0] or b in selected or frozenset([a, b]) in used:
                    continue
                distance = float(np.linalg.norm(frames[a]["center"] - frames[b]["center"]))
                normal = float(np.dot(frames[a]["normal"], frames[b]["normal"]))
                delta = frames[b]["center"] - frames[a]["center"]
                tangential = np.linalg.norm(delta - frames[a]["normal"] * np.dot(delta, frames[a]["normal"]))
                feature_axes = frames[a].get("featureAxis") and frames[b].get("featureAxis")
                if tangential <= 0.05 and distance <= (100 if feature_axes else 10) and abs(normal) >= np.cos(np.deg2rad(0.1)):
                    nearby.append(b)
            # Collinear feature centers provide design evidence beyond proximity.
            # Pick the uniquely nearest source occurrence; equal alternatives remain a choice.
            if nearby and frames[a].get("featureAxis") and all(frames[b].get("featureAxis") for b in nearby):
                nearby.sort(key=lambda b: np.linalg.norm(frames[a]["center"]-frames[b]["center"]))
                if len(nearby) == 1 or np.linalg.norm(frames[a]["center"]-frames[nearby[1]]["center"]) - np.linalg.norm(frames[a]["center"]-frames[nearby[0]]["center"]) > .05:
                    nearby = nearby[:1]
            if len(nearby) == 1 and (frames[a].get("featureAxis") and frames[nearby[0]].get("featureAxis") or np.linalg.norm(frames[a]["center"] - frames[nearby[0]]["center"]) <= 0.05):
                b = nearby[0]
                id = fingerprint([a, b])[:16]
                candidates.append({"id": id, "a": {"path": list(a[0]), "interfaceId": a[1]}, "b": {"path": list(b[0]), "interfaceId": b[1]}, "mode": "preserve-offset", "origin": "inferred", "offset": (frames[b]["center"] - frames[a]["center"]).tolist(), "normalDot": float(np.dot(frames[a]["normal"], frames[b]["normal"]))})
                used.add(frozenset([a, b]))
            elif nearby:
                choices.append({"message": "接口有多个候选或存在间隙，请确认联动对象", "a": {"path": list(a[0]), "interfaceId": a[1]}, "candidates": [{"path": list(b[0]), "interfaceId": b[1]} for b in nearby]})
        self.relations.extend(candidates)
        # Preserve verified planar contacts, including undeclared rod ends and shelf support faces.
        # This uses source-owned CAD faces and a real face-to-face distance, never box proximity.
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        supports = [(key, frame) for key, frame in frames.items() if frame.get("contactShape") is not None]
        world_shapes, plane_pairs = {}, set()
        for index, (a, fa) in enumerate(supports):
            for b, fb in supports[index+1:]:
                self.check()
                if a[0] == b[0]: continue
                dot = float(np.dot(fa["normal"], fb["normal"]))
                if dot > -np.cos(np.deg2rad(0.1)): continue
                offset = float(np.dot(fb["center"]-fa["center"], fa["normal"]))
                if abs(offset) > 0.05: continue
                pair = (a[0], b[0], tuple(np.round(fa["normal"], 5)), round(float(np.dot(fa["center"], fa["normal"])), 3))
                if pair in plane_pairs: continue
                for key, frame in [(a, fa), (b, fb)]:
                    if key not in world_shapes: world_shapes[key] = transformed_shape(frame["contactShape"], self.by_path[key[0]]["world"])
                ba, bb = exact_bounds(world_shapes[a]), exact_bounds(world_shapes[b])
                if np.any(ba[1]+0.05 < bb[0]) or np.any(bb[1]+0.05 < ba[0]): continue
                distance = BRepExtrema_DistShapeShape(world_shapes[a], world_shapes[b])
                if not distance.IsDone() or distance.Value() > 0.05: continue
                plane_pairs.add(pair)
                relation = {"id": "contact-" + fingerprint([a, b])[:16], "a": {"path": list(a[0]), "interfaceId": a[1]}, "b": {"path": list(b[0]), "interfaceId": b[1]}, "mode": "plane", "origin": "inferred", "offset": [0, 0, offset], "normalDot": -1}
                if not any(r["id"] == relation["id"] for r in self.relations): self.relations.append(relation)
        # User-selected pairs retain measured baseline offsets; do not fabricate coincidence.
        for relation in self.relations:
            a, b = self.endpoint(relation['a']), self.endpoint(relation['b'])
            normal_offset = relation.pop('_normalOffset', None)
            if normal_offset is not None: relation['offset'] = (frames[a]['normal'] * normal_offset).tolist()
            if relation['mode'] == 'axis' and relation['origin'] == 'existing':
                relation['normalDot'] = 1 if np.dot(frames[a]['normal'], frames[b]['normal']) >= 0 else -1
            if relation.pop('_captureOffset', False) or relation["origin"] == "user" and relation["mode"] == "preserve-offset":
                a, b = self.endpoint(relation["a"]), self.endpoint(relation["b"])
                relation["offset"] = (frames[b]["center"] - frames[a]["center"]).tolist()
                relation["normalDot"] = float(np.dot(frames[a]["normal"], frames[b]["normal"]))
        if choices:
            raise ResizeFailure("接口关系不唯一，请选择联动关系后重试", "RESIZE_NEEDS_CHOICE", choices)
        unique = {}
        for relation in self.relations:
            if relation.get('normalDot') is not None: relation['normalDot'] = float(np.clip(relation['normalDot'], -1, 1))
            for side in ('a', 'b'):
                path, id = self.endpoint(relation[side])
                relation[side] = {'path': list(path), 'interfaceId': id}
            key = fingerprint({**{k: relation[k] for k in ('a', 'b', 'mode')}, 'offset': np.round(relation['offset'], 9).tolist()})
            unique[key] = relation
        self.relations = list(unique.values())

    def solve(self):
        measurement = self._measure_baseline()
        if self.request.get("inspectOnly"):
            return measurement.inspection
        constraints = self._prepare_constraints(measurement)
        space = self._prepare_variables(measurement, constraints)
        result = self._find_solution(measurement, constraints, space)
        return self._build_result(measurement, constraints, space, result)

    def _measure_baseline(self):
        self.progress("读取实际模型尺寸及接口")
        base_parameters = {leaf["path"]: deepcopy(leaf["member"]["parameterValues"]) for leaf in self.leaves}
        base_transforms = {leaf["path"]: leaf["world"].copy() for leaf in self.leaves}
        baseline, base_frames, base_boxes, bounds = self.measure(base_parameters, base_transforms)
        for path, geometry in baseline.items():
            for definition in geometry.definitions:
                base_parameters[path].setdefault(definition["id"], definition["default"])
        original_size = bounds[1] - bounds[0]
        target = np.array(self.request.get("dimensions") or original_size, dtype=float)
        if target.shape != (3,) or not np.isfinite(target).all() or np.any(target <= 0) or np.any(original_size <= 1e-9):
            raise ResizeFailure("目标长宽高必须为正数，且原组件不能为零尺寸", "RESIZE_DIMENSION_INVALID")
        old_dimensions = {p["id"]: p for p in self.document["parameters"]}
        differences = [{"parameterId": pid, "saved": old_dimensions[pid]["value"], "actual": float(original_size[axis])} for axis, pid in enumerate(("componentLength", "componentWidth", "componentHeight")) if pid in old_dimensions and old_dimensions[pid]["value"] is not None and abs(old_dimensions[pid]["value"]-original_size[axis])>.1]
        inspection = {"dimensions": original_size.tolist(), "parameterDifferences": differences, "members": [{"path": list(l["path"]), "name": l["member"]["name"], "parameters": baseline[l["path"]].definitions} for l in self.leaves], "interfaces": [{"path": list(k[0]), "interfaceId": k[1]} for k in base_frames], "diagnostics": [d for g in baseline.values() for d in g.diagnostics]}
        if self.inspected: self.inspected(inspection)
        return _Measurement(base_parameters, base_transforms, baseline, base_frames, base_boxes, bounds,
                            original_size, target, inspection)

    @staticmethod
    def _locked(locks, path, parameter=None):
        return any(tuple(lock["path"]) == path[:len(lock["path"])] and (lock.get("parameterId") is None or lock.get("parameterId") == parameter) for lock in locks)

    def _interface_failure(self, key, current):
        message = f'{self.by_path[key[0]]["member"]["name"]}：接口 {key[1]} ' + ("在目标尺寸下不再生成" if key not in current else "来源发生改变")
        return ResizeFailure(message, "RESIZE_INTERFACE_MISSING", [{"path": list(key[0]), "interfaceId": key[1], "message": message}])

    def _prepare_constraints(self, measurement):
        self.infer(measurement.base_frames)
        self.tolerances = {'dimensionMm': .1, 'positionMm': .05, 'angleDegrees': .1}
        for frame in measurement.base_frames.values():
            for key, value in frame.get('tolerances', {}).items():
                if key in self.tolerances and isinstance(value, (int, float)) and np.isfinite(value) and value > 0:
                    self.tolerances[key] = min(self.tolerances[key], value)
        design = self.request.get("design") or self.document.get("geometryDesign") or {}
        locks = design.get("locks", [])
        def collect_locks(doc, prefix=()):
            for member in doc['assembly']['members']:
                if member.get('instanceDocument'):
                    path = (*prefix, member['id'])
                    for lock in (member['instanceDocument'].get('geometryDesign') or {}).get('locks', []):
                        candidate = {**lock, 'path': [*path, *lock['path']]}
                        if candidate not in locks: locks.append(candidate)
                    collect_locks(member['instanceDocument'], path)
        collect_locks(self.document)
        for lock in locks:
            path = tuple(lock['path'])
            matching = [p for p in self.by_path if p[:len(path)] == path]
            if not matching or lock.get('parameterId') and not any(d['id'] == lock['parameterId'] for p in matching for d in measurement.baseline[p].definitions):
                raise ResizeFailure("锁定对象或参数已失效，请重新选择", "RESIZE_LOCK_INVALID", [lock])
        required = {self.endpoint(r[side]) for r in self.relations for side in ("a", "b")}
        def protect_exports(doc, prefix=()):
            for port in doc["ports"]:
                source = port["source"]
                required.add(self.endpoint({"path": [*prefix, source["memberId"]], "interfaceId": source["interfaceId"]}))
            for slot in doc["slots"]:
                for source in (slot.get("baseInterface"), slot):
                    if source and source.get("memberId") and source.get("interfaceId"):
                        required.add(self.endpoint({"path": [*prefix, source["memberId"]], "interfaceId": source["interfaceId"]}))
            for member in doc["assembly"]["members"]:
                if member.get("instanceDocument"):
                    protect_exports(member["instanceDocument"], (*prefix, member["id"]))
        protect_exports(self.document)
        generated = {(path, i["id"]) for path, g in measurement.baseline.items() for i in g.resolved
                     if i.get("declarationMode") == "featureDerived" and i.get("sourceFeatureRuleId") and i.get("sourceFeatureId")}
        required.update(key for key in measurement.base_frames if key not in generated or self._locked(locks, key[0]))
        guard_occurrences = {path: [i for i in g.resolved if (path, i["id"]) in required] for path, g in measurement.baseline.items()}
        for leaf in self.leaves:
            if not measurement.baseline[leaf["path"]].frames and not self._locked(locks, leaf["path"]):
                raise ResizeFailure(f'{leaf["member"]["name"]}缺少可定位的真实接口，请补充语义面来源', "RESIZE_INTERFACE_MISSING", [{"path": list(leaf["path"])}])
        connected = {self.endpoint(r[s])[0] for r in self.relations for s in ("a", "b")}
        missing = [l for l in self.leaves if l["path"] not in connected and not self._locked(locks, l["path"])]
        if len(self.leaves) > 1 and missing:
            raise ResizeFailure("部分零件没有可确认的联动关系，请补充接口关系或锁定这些零件", "RESIZE_NEEDS_CHOICE", [{"path": list(l["path"]), "message": l["member"]["name"] + "：尚未确定与其他零件的几何关系"} for l in missing])
        anchor = np.array([(measurement.bounds[0, 0] + measurement.bounds[1, 0]) / 2, (measurement.bounds[0, 1] + measurement.bounds[1, 1]) / 2, measurement.bounds[0, 2]])
        scale = measurement.target / measurement.original_size
        desired_centers = {leaf["path"]: anchor + ((box[0] + box[1]) / 2 - anchor) * scale for leaf, box in zip(self.leaves, measurement.base_boxes)}
        grounded = {leaf["path"] for leaf, box in zip(self.leaves, measurement.base_boxes) if abs(box[0, 2]-measurement.bounds[0, 2]) < 1e-5}
        symmetries = []
        templates = []
        for leaf in self.leaves:
            reference = leaf["member"]["template"]
            templates.append({**reference, "sha256": template_key(reference)[1]})
        for i, a in enumerate(self.leaves):
            ca = (measurement.base_boxes[i][0]+measurement.base_boxes[i][1])/2
            for j, b in enumerate(self.leaves[i+1:], i+1):
                # Explicit locks take precedence over inferred mirror symmetry.
                if any(tuple(lock['path']) == p[:len(lock['path'])] for lock in locks for p in (a['path'], b['path'])): continue
                if templates[i] != templates[j] or measurement.base_parameters[a["path"]] != measurement.base_parameters[b["path"]]: continue
                cb = (measurement.base_boxes[j][0]+measurement.base_boxes[j][1])/2
                for axis in (0, 1):
                    if abs(ca[axis]-cb[axis]) < .1: continue
                    mirrored = ca.copy(); mirrored[axis] = 2*anchor[axis]-ca[axis]
                    if np.allclose(mirrored, cb, atol=1e-5) and np.allclose(measurement.base_boxes[i][1]-measurement.base_boxes[i][0], measurement.base_boxes[j][1]-measurement.base_boxes[j][0], atol=1e-5):
                        symmetries.append((a["path"], b["path"], axis, i, j))
        return _Constraints(locks, required, guard_occurrences, anchor, scale, desired_centers, grounded,
                            symmetries)

    @staticmethod
    def _decode(measurement, variables, x):
        params = deepcopy(measurement.base_parameters)
        poses = {p: np.array([*t[:3, 3], 0., 0., 0.]) for p, t in measurement.base_transforms.items()}
        for (path, field), value in zip(variables, x):
            if isinstance(field, int): poses[path][field] = value
            else: params[path][field] = float(value)
        transforms = {}
        for path, pose in poses.items():
            t = np.eye(4); t[:3, :3] = Rotation.from_rotvec(pose[3:]).as_matrix() @ measurement.base_transforms[path][:3, :3]; t[:3, 3] = pose[:3]; transforms[path] = t
        return params, transforms

    def _prepare_variables(self, measurement, constraints):
        generation_margins = getattr(self.provider, "generation_margins", lambda reference, parameters, occurrences: {})
        guard_bases, guard_probes, guard_minimums = {}, {}, {}
        variables, x0, lower, upper, discrete = [], [], [], [], []
        influences, sensitivities = [], {}
        self.progress("识别零件尺寸对接口及外形的影响")
        self.linear_bases = {}
        for leaf in self.leaves:
            path, member = leaf["path"], leaf["member"]
            g = measurement.baseline[path]
            probe_geometry = getattr(self.provider, "probe", self.provider.evaluate)
            linear_base = probe_geometry(member["template"], measurement.base_parameters[path])
            self.linear_bases[path] = linear_base
            self.surrogates[path] = (g, measurement.base_parameters[path], {})
            guard_bases[path] = generation_margins(member["template"], measurement.base_parameters[path], constraints.guard_occurrences[path])
            guard_probes[path] = {}
            # A small interior margin avoids floor(count) losing an occurrence
            # through floating point roundoff at the exact generation boundary.
            guard_minimums[path] = {id: np.minimum(.01, np.maximum(0., value)) for id, value in guard_bases[path].items()}
            t = measurement.base_transforms[path]
            center = t[:3, :3] @ ((g.bounds[0]+g.bounds[1])/2) + t[:3, 3]
            half = np.abs(t[:3, :3]) @ ((g.bounds[1]-g.bounds[0])/2)
            self.envelope_corrections[path] = exact_bounds(g.shape, t)-np.array([center-half, center+half])
            for definition in g.definitions:
                self.check()
                pid = definition["id"]
                if member["id"] in self.fixed_geometry_members or member.get("generatedBy") or definition["unit"] not in ("mm", "cm", "m", "in", "°", "deg", "rad") or definition["valueType"] not in ("number", "integer") or self._locked(constraints.locks, path, pid):
                    continue
                value = measurement.base_parameters[path].get(pid, definition["default"])
                low, high = definition["minimum"], definition["maximum"]
                if low is None or high is None or low >= high:
                    continue
                values = definition.get("allowedValues") or (list(range(int(np.ceil(low)), int(np.floor(high)) + 1)) if definition["valueType"] == "integer" and high - low <= 32 else [])
                step = max(abs(value) * 1e-4, 0.01)
                sample = next((v for v in values if v != value), None) if values else value + step if value + step <= high else value - step
                if sample is None or sample < low or sample > high or definition["valueType"] == "integer" and not values:
                    continue
                try:
                    self.progress(f'识别 {member["name"]} · {definition["label"]} 的几何影响')
                    probe = probe_geometry(member["template"], {**measurement.base_parameters[path], pid: sample})
                    # Correct body-only probes by the full rebuilt baseline. Features
                    # that alter the envelope will still be checked on final CAD.
                    probe = replace(probe, bounds=g.bounds + probe.bounds-linear_base.bounds)
                    probe_guards = generation_margins(member["template"], {**measurement.base_parameters[path], pid: sample}, constraints.guard_occurrences[path])
                except (GeometryFailure, RuntimeError, ValueError):
                    continue
                changed = [id for id, f in g.frames.items() if id in probe.frames and (np.linalg.norm(f["center"] - probe.frames[id]["center"]) > 1e-4 or np.linalg.norm(f["normal"] - probe.frames[id]["normal"]) > 1e-5)]
                guard_changed = any(np.max(np.abs(probe_guards[id]-v)) > 1e-6 for id, v in guard_bases[path].items())
                if np.max(np.abs(probe.bounds - g.bounds)) < 1e-4 and not changed and not guard_changed:
                    continue
                influences.append({"path": list(path), "parameterId": pid, "interfaces": changed, "minimum": low, "maximum": high,
                    "movements": [{"interfaceId": id, "perUnit": ((probe.frames[id]["center"]-g.frames[id]["center"])/(sample-value)).tolist()} for id in changed]})
                sensitivities[(path, pid)] = ((probe.bounds[1] - probe.bounds[0]) - (g.bounds[1] - g.bounds[0])) / (sample - value)
                self.surrogates[path][2][pid] = (probe, sample - value)
                guard_probes[path][pid] = {id: (probe_guards[id]-v)/(sample-value) for id, v in guard_bases[path].items()}
                variables.append((path, pid)); x0.append(float(value)); lower.append(float(low)); upper.append(float(high))
                if values:
                    discrete.append((len(x0) - 1, sorted(values, key=lambda v: abs(v - value))))
            if not self._locked(constraints.locks, path):
                pose = [*measurement.base_transforms[path][:3, 3], 0., 0., 0.]
                aligned = [int(np.argmax(np.abs(measurement.base_transforms[path][:3, axis]))) for axis in range(3) if np.max(np.abs(measurement.base_transforms[path][:3, axis])) > 1-1e-8]
                for index, value in enumerate(pose):
                    if index >= 3 and (len(aligned) > 1 or len(aligned) == 1 and index-3 != aligned[0]): continue
                    variables.append((path, index)); x0.append(value); lower.append(-np.inf); upper.append(np.inf)
        if len(variables) > 256:
            raise ResizeFailure("联动变量超过256项，请锁定无关尺寸后重试", "RESIZE_RESOURCE_LIMIT")
        if np.prod([len(v) for _, v in discrete]) > 64:
            raise ResizeFailure("离散规格组合超过64种，请锁定规格后重试", "RESIZE_NEEDS_CHOICE", [{"message": "请锁定不需要变化的整数或规格参数"}])
        self.check()
        x0, lower, upper = np.array(x0), np.array(lower), np.array(upper)
        seed = x0.copy()
        for leaf in self.leaves:
            path = leaf["path"]
            indices = [i for i, (p, field) in enumerate(variables) if p == path and isinstance(field, str)]
            old_extent = measurement.baseline[path].bounds[1] - measurement.baseline[path].bounds[0]
            rotation = measurement.base_transforms[path][:3, :3]
            new_extent = old_extent * np.linalg.norm(constraints.scale[:, None] * rotation, axis=0)
            if indices:
                jac = np.stack([sensitivities[variables[i]] for i in indices], axis=1)
                delta = np.linalg.lstsq(jac, new_extent - old_extent, rcond=None)[0]
                seed[indices] = np.clip(x0[indices] + delta, lower[indices], upper[indices])
            major = int(np.argmax(old_extent))
            axis = rotation[:, major]
            desired_axis = constraints.scale * axis; desired_axis /= np.linalg.norm(desired_axis)
            cross = np.cross(axis, desired_axis); norm = np.linalg.norm(cross)
            corrected = rotation if norm < 1e-10 else Rotation.from_rotvec(cross / norm * np.arctan2(norm, np.dot(axis, desired_axis))).as_matrix() @ rotation
            center = (measurement.baseline[path].bounds[0] + measurement.baseline[path].bounds[1]) / 2
            center = center + (new_extent - old_extent) / 2
            pose = [*(constraints.desired_centers[path] - corrected @ center), *Rotation.from_matrix(corrected @ rotation.T).as_rotvec()]
            for i, (p, field) in enumerate(variables):
                if p == path and isinstance(field, int): seed[i] = pose[field]
        # Seed geometry-neutral parameters from the scaled connection layout.
        # In particular, do not start a shorter array with its old hole pitch.
        seed_params, seed_transforms = self._decode(measurement, variables, seed)
        self.approximate = True
        seed_frames = self.measure(seed_params, seed_transforms)[1]
        self.approximate = False
        for path, (g, values, probes) in self.surrogates.items():
            indices = [i for i, (p, pid) in enumerate(variables) if p == path and isinstance(pid, str)
                       and np.linalg.norm(sensitivities[(p, pid)]) < 1e-6 and not any(i == d[0] for d in discrete)]
            if not indices: continue
            pids = [variables[i][1] for i in indices]
            rows, wanted = [], []
            rotation = seed_transforms[path][:3, :3]
            for id in g.frames:
                key = (path, id)
                if key not in constraints.required: continue
                rows.extend(np.stack([rotation @ ((probes[pid][0].frames[id]['center']-g.frames[id]['center'])/probes[pid][1])
                                      if id in probes[pid][0].frames else np.zeros(3) for pid in pids], axis=1))
                desired = constraints.anchor + (measurement.base_frames[key]['center']-constraints.anchor)*constraints.scale
                wanted.extend(desired-seed_frames[key]['center'])
            guard_rows, guard_values = [], []
            for id, value in guard_bases[path].items():
                current = value.copy()
                for pid, derivatives in guard_probes[path].items():
                    current = current + (seed_params[path][pid]-values[pid])*derivatives[id]
                guard_rows.extend(np.stack([guard_probes[path][pid][id] for pid in pids], axis=1))
                guard_values.extend(guard_minimums[path][id]-current)
            a = np.array(rows).reshape(-1, len(indices)); b = np.array(wanted)
            ga = np.array(guard_rows).reshape(-1, len(indices)); gb = np.array(guard_values)
            if not len(a) and not len(ga): continue
            normalization = np.maximum(np.abs(x0[indices]), 1)
            initial = least_squares(lambda delta: np.r_[a@delta-b, np.maximum(0., gb-ga@delta)*100, delta/normalization*.001],
                                    np.zeros(len(indices)), bounds=(lower[indices]-seed[indices], upper[indices]-seed[indices]),
                                    max_nfev=40, ftol=1e-9, xtol=1e-9, gtol=1e-9)
            seed[indices] += initial.x
        return _VariableSpace(variables, x0, lower, upper, discrete, seed, influences, sensitivities,
                              guard_bases, guard_probes, guard_minimums)

    def _find_solution(self, measurement, constraints, space):
        generation_margins = getattr(self.provider, "generation_margins", lambda reference, parameters, occurrences: {})
        last_error, failure_details = [], []
        def residual(x, verify=False):
            params, transforms = self._decode(measurement, space.variables, x)
            geoms, frames, boxes, actual = self.measure(params, transforms)
            ptol = self.tolerances['positionMm']; atol = np.deg2rad(self.tolerances['angleDegrees'])
            errors = [*((actual[1] - actual[0] - measurement.target) / self.tolerances['dimensionMm']), *((np.array([(actual[0, 0]+actual[1, 0])/2, (actual[0, 1]+actual[1, 1])/2, actual[0, 2]]) - constraints.anchor) / ptol)]
            for leaf, box in zip(self.leaves, boxes):
                if leaf["path"] in constraints.grounded: errors.append((box[0, 2]-constraints.anchor[2])/.01)
            for a, b, axis, i, j in constraints.symmetries:
                ca = (boxes[i][0]+boxes[i][1])/2; cb = (boxes[j][0]+boxes[j][1])/2
                ca[axis] = 2*constraints.anchor[axis]-ca[axis]
                errors.extend((ca-cb)/.01)
                for pid in params[a]:
                    if isinstance(params[a][pid], (int, float)) and not isinstance(params[a][pid], bool): errors.append((params[a][pid]-params[b][pid])/.01)
            # Preserve the existing coordinate-aligned structure. Diagonal members
            # may turn within their original plane, but cannot tilt out or flip.
            for path, transform in transforms.items():
                before = measurement.base_transforms[path][:3, :3]
                after = transform[:3, :3]
                for axis in range(3):
                    if np.max(np.abs(before[:, axis])) > 1-1e-8:
                        errors.extend((after[:, axis]-before[:, axis])/1e-5)
                errors.append(max(0., -float(np.dot(after[:, np.argmax(measurement.baseline[path].bounds[1]-measurement.baseline[path].bounds[0])], before[:, np.argmax(measurement.baseline[path].bounds[1]-measurement.baseline[path].bounds[0])])))/0.001)
            for relation in self.relations:
                a, b = self.endpoint(relation["a"]), self.endpoint(relation["b"])
                for key in (a, b):
                    if key not in frames or frames[key]["source"] != measurement.base_frames[key]["source"]:
                        raise self._interface_failure(key, frames)
                fa, fb = frames[a], frames[b]
                delta = fb["center"] - fa["center"]
                if relation["mode"] == "plane": errors.append((np.dot(delta, fa["normal"]) - relation["offset"][2]) / ptol)
                elif relation["mode"] == "axis": errors.extend(np.cross(delta, fa["normal"]) / ptol)
                else: errors.extend((delta - np.array(relation["offset"])) / ptol)
                normal_dot = relation.get("normalDot")
                if normal_dot is not None:
                    dot = np.clip(np.dot(fa["normal"], fb["normal"]), -1, 1)
                    if abs(normal_dot) > 0.999999:
                        errors.extend(np.cross(fa["normal"], fb["normal"]) / np.sin(atol))
                        errors.append(max(0, -dot * np.sign(normal_dot)) / 0.001)
                    else:
                        errors.append((np.arccos(dot)-np.arccos(np.clip(normal_dot, -1, 1)))/atol)
                if verify:
                    if relation['mode'] == 'axis': errors.append(np.linalg.norm(np.cross(delta, fa['normal']))/ptol)
                    elif relation['mode'] != 'plane': errors.append(np.linalg.norm(delta-np.array(relation['offset']))/ptol)
                    if normal_dot is not None: errors.append(abs(np.arccos(np.clip(np.dot(fa['normal'], fb['normal']), -1, 1))-np.arccos(np.clip(normal_dot, -1, 1)))/atol)
                if verify and relation["id"].startswith("contact-"):
                    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
                    distance = BRepExtrema_DistShapeShape(transformed_shape(retained_support(fa["contactShape"], geoms[a[0]].shape), transforms[a[0]]), transformed_shape(retained_support(fb["contactShape"], geoms[b[0]].shape), transforms[b[0]]))
                    if not distance.IsDone() or distance.Value() > ptol:
                        raise ResizeFailure("调整后承托或端面接触断开", "RESIZE_CONTACT_LOST", [relation])
            for path, base_guards in space.guard_bases.items():
                if self.approximate:
                    current = {id: np.array(value, dtype=float) for id, value in base_guards.items()}
                    for pid, derivatives in space.guard_probes[path].items():
                        delta = params[path][pid]-self.surrogates[path][1][pid]
                        for id, derivative in derivatives.items(): current[id] += delta*derivative
                else:
                    current = generation_margins(self.by_path[path]["member"]["template"], params[path], constraints.guard_occurrences[path])
                for id, value in current.items():
                    errors.extend(np.maximum(0., space.guard_minimums[path][id]-value)/.001)
            groups = {}
            for binding in self.bindings:
                if binding["path"] not in params: continue
                value = params[binding["path"]].get(binding["memberParameterId"])
                if not isinstance(value, (int, float)): continue
                group = tuple(binding["group"])
                if group in groups: errors.append((value - groups[group]) / 0.05)
                else: groups[group] = value
                definition = binding.get("definition")
                if definition:
                    errors.extend([max(0, definition["min"]-value)/0.001, max(0, value-definition["max"])/0.001])
            if verify:
                return np.array(errors), params, transforms, geoms, frames, boxes, actual
            # Preserve configuration without constraining legitimate stretch/rotation.
            soft = []
            for leaf, box in zip(self.leaves, boxes):
                p = leaf["path"]
                soft.extend(((box[0] + box[1]) / 2 - constraints.desired_centers[p]) * 0.002)
                for id, frame in measurement.baseline[p].frames.items():
                    key = (p, id)
                    if key in frames:
                        desired = constraints.anchor + (measurement.base_frames[key]["center"] - constraints.anchor) * constraints.scale
                        soft.extend((frames[key]["center"] - desired) * 0.002 / max(1, len(measurement.baseline[p].frames) ** 0.5))
            for i, (path, field) in enumerate(space.variables):
                delta = (x[i]-space.x0[i])/max(abs(space.x0[i]), 1)
                # Smooth L1 discourages spreading a required length change across
                # many unrelated section and hole parameters.
                soft.append(delta*.03 if isinstance(field, int) else delta*2/np.sqrt(np.sqrt(delta*delta+1e-8)+1e-4))
            return np.r_[errors, soft]
        self.progress("反算零件尺寸、位置和角度")
        accepted = []
        # First try extending an existing boundary member while keeping its
        # interface pattern and all other parts unchanged. Increasing an upright
        # above the last hole need not spread every brace and every hole pitch.
        direct = space.x0.copy()
        if not space.discrete:
            changed_axes = np.flatnonzero(np.abs(measurement.target-measurement.original_size) > 1e-6)
            for leaf, box in zip(self.leaves, measurement.base_boxes):
                path = leaf['path']; rotation = measurement.base_transforms[path][:3, :3]
                columns, indices = [], []
                for i, (p, pid) in enumerate(space.variables):
                    if p != path or not isinstance(pid, str): continue
                    derivative = np.abs(rotation) @ space.sensitivities[(p, pid)]
                    if any(abs(box[0, axis]-measurement.bounds[0, axis]) < .01 and abs(box[1, axis]-measurement.bounds[1, axis]) < .01 and abs(derivative[axis]) > 1e-6 and np.linalg.norm(np.delete(derivative, axis)) < 1e-6 for axis in changed_axes):
                        columns.append(derivative); indices.append(i)
                if indices:
                    direct[indices] = np.clip(space.x0[indices]+np.linalg.lstsq(np.stack(columns, axis=1), measurement.target-measurement.original_size, rcond=None)[0], space.lower[indices], space.upper[indices])
            if np.any(np.abs(direct-space.x0) > 1e-6):
                try:
                    result = residual(direct, True)
                    if np.max(np.abs(result[0])) <= 1: accepted.append((float(np.linalg.norm((direct-space.x0)/np.maximum(np.abs(space.x0), 1))), result))
                except ResizeFailure as error:
                    if error.code in ('RESIZE_CANCELLED', 'RESIZE_TIMEOUT'): raise
                except (GeometryFailure, ValueError, RuntimeError): pass
        combinations = itertools.product(*[values for _, values in space.discrete]) if space.discrete else [()]
        if accepted: combinations = []
        for combination in combinations:
            trial = space.seed.copy()
            fixed = {index: value for (index, _), value in zip(space.discrete, combination)}
            for index, value in fixed.items(): trial[index] = value
            active = [i for i in range(len(trial)) if i not in fixed]
            def expand(z):
                result = trial.copy(); result[active] = z; return result
            try:
                for refinement in range(3):
                    self.approximate = True
                    if active:
                        solved = least_squares(lambda z: residual(expand(z)), trial[active], bounds=(space.lower[active], space.upper[active]), method="trf", x_scale="jac", max_nfev=80, ftol=1e-9, xtol=1e-9, gtol=1e-9)
                        candidate = expand(solved.x)
                    else: candidate = trial
                    self.approximate = False
                    result = residual(candidate, True)
                    self.progress(f'复核第 {refinement+1} 轮：最大约束误差 {np.max(np.abs(result[0])):.3f} 倍容差')
                    if np.max(np.abs(result[0])) <= 1:
                        accepted.append((float(np.linalg.norm((candidate-space.x0)/np.maximum(np.abs(space.x0), 1))), result))
                        break
                    trial = candidate
                    last_error = [f'当前最大约束误差为允许值的 {np.max(np.abs(result[0])):.3f} 倍；实际外形为 ' + ' × '.join(f'{v:.3f}' for v in result[6][1]-result[6][0]) + ' mm。']
                    failure_details = []
                    # Re-linearize around actual CAD, then verify again; a surrogate never passes validation.
                    actual_params, actual_geoms = result[1], result[3]
                    for path, (old, _, probes) in list(self.surrogates.items()):
                        g = actual_geoms[path]; t = result[2][path]
                        center = t[:3, :3] @ ((g.bounds[0]+g.bounds[1])/2) + t[:3, 3]
                        half = np.abs(t[:3, :3]) @ ((g.bounds[1]-g.bounds[0])/2)
                        self.envelope_corrections[path] = exact_bounds(g.shape, t)-np.array([center-half, center+half])
                        updated = {}
                        space.guard_bases[path] = generation_margins(self.by_path[path]["member"]["template"], actual_params[path], constraints.guard_occurrences[path])
                        space.guard_probes[path] = {}
                        for pid, (_, step) in probes.items():
                            definition = next(d for d in actual_geoms[path].definitions if d["id"] == pid)
                            value = actual_params[path][pid]
                            if value + step > definition["maximum"]: step = -abs(step)
                            if value + step < definition["minimum"]: step = abs(step)
                            probe_geometry = getattr(self.provider, "probe", self.provider.evaluate)
                            linear = probe_geometry(self.by_path[path]["member"]["template"], actual_params[path])
                            self.linear_bases[path] = linear
                            probe = probe_geometry(self.by_path[path]["member"]["template"], {**actual_params[path], pid: value + step})
                            probe_guards = generation_margins(self.by_path[path]["member"]["template"], {**actual_params[path], pid: value + step}, constraints.guard_occurrences[path])
                            space.guard_probes[path][pid] = {id: (probe_guards[id]-v)/step for id, v in space.guard_bases[path].items()}
                            updated[pid] = (replace(probe, bounds=actual_geoms[path].bounds + probe.bounds-linear.bounds), step)
                        self.surrogates[path] = (actual_geoms[path], actual_params[path], updated)
            except ResizeFailure as error:
                if error.code in ("RESIZE_CANCELLED", "RESIZE_TIMEOUT"): raise
                last_error.append(str(error))
                failure_details = error.details
            except (GeometryFailure, RuntimeError, ValueError) as error:
                last_error.append(str(error))
                failure_details = []
        if not accepted:
            limits = [{"path": list(path), "parameterId": field, "minimum": float(lo), "maximum": float(hi)} for (path, field), lo, hi in zip(space.variables, space.lower, space.upper) if isinstance(field, str)]
            raise ResizeFailure("本次未找到满足接口、外形和零件限制的结果。" + (last_error[-1] if last_error else "请检查尺寸范围或调整联动关系。"), "RESIZE_NO_SOLUTION", failure_details or limits)
        accepted.sort(key=lambda a: a[0])
        if len(accepted) > 1 and space.discrete and self.request.get("solutionIndex") is None:
            raise ResizeFailure("存在多种满足要求的离散规格方案，请选择", "RESIZE_NEEDS_CHOICE",
                [{"message": f"方案 {i+1}", "solutionIndex": i, "specifications": [{"path": list(path), "parameterId": pid, "value": item[1][1][path][pid]} for index, _ in space.discrete for path, pid in [space.variables[index]]]} for i, item in enumerate(accepted)])
        selection = self.request.get("solutionIndex") or 0
        if selection >= len(accepted): raise ResizeFailure("候选方案已变化，请重新试算", "RESIZE_NEEDS_CHOICE")
        return accepted[selection][1]

    def _build_result(self, measurement, constraints, space, result):
        residuals, params, transforms, geoms, frames, _, actual = result
        # Only unreferenced generated occurrences may disappear. Static, used,
        # exported and slot interfaces remain protected; no source may change.
        for key, original in measurement.base_frames.items():
            if key not in frames and key not in constraints.required: continue
            if key not in frames or frames[key]["source"] != original["source"]:
                raise self._interface_failure(key, frames)
        self.progress("重建并复核所有受影响零件")
        changes = []
        for leaf in self.leaves:
            path, member = leaf["path"], leaf["member"]
            before = {key: deepcopy(member[key]) for key in ("parameterValues", "position", "rotation")}
            local = np.linalg.inv(leaf["parent"]) @ transforms[path]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                angles = Rotation.from_matrix(local[:3, :3]).as_euler("XYZ").tolist()
            if np.allclose(local[:3, :3], matrix([0, 0, 0], member["rotation"])[:3, :3], atol=1e-7): angles = member["rotation"]
            member.update(parameterValues=params[path], position=local[:3, 3].tolist(), rotation=angles)
            old = {i["id"]: i for i in member["interfaces"]}
            member["interfaces"] = deepcopy(geoms[path].interfaces)
            for i in member["interfaces"]:
                for field in ("connectionTypeId", "connectableTypeIds", "hardware"):
                    if field in old.get(i["id"], {}): i[field] = deepcopy(old[i["id"]][field])
            member["resolvedInterfaces"] = deepcopy(geoms[path].resolved)
            after = {key: member[key] for key in before}
            if any(not np.allclose(before[key], after[key], atol=1e-7) if key != "parameterValues" else before[key] != after[key] for key in before):
                changes.append({"path": list(path), "name": member["name"], "before": before, "after": after})
        self.refresh_document(self.document, geoms, transforms, constraints.locks)
        dimensions = (actual[1] - actual[0]).tolist()
        self.document["schemaVersion"] = "component-1.1"
        self.document["geometryDesign"].update(locks=constraints.locks, relations=self.relations, dimensions=dimensions)
        self.document["geometryDesign"]["verification"].update(targetDimensions=measurement.target.tolist(), dimensionErrors=(actual[1]-actual[0]-measurement.target).tolist(), maximumNormalizedResidual=float(np.max(np.abs(residuals))))
        self.set_dimensions(self.document, dimensions)
        return {"document": self.document, "dimensions": dimensions, "originalDimensions": measurement.original_size.tolist(), "changes": changes, "influences": space.influences,
                "structureChanges": structure_changes(self.original_document, self.document),
                "relations": self.relations, "diagnostics": [d for g in geoms.values() for d in g.diagnostics], "checksum": fingerprint(self.document)}

    @staticmethod
    def set_dimensions(document, dimensions):
        for axis, value in zip("XYZ", dimensions):
            id = "geometryEnvelope" + axis
            entry = next((p for p in document["parameters"] if p["id"] == id), None)
            if entry: entry["value"] = value
            else: document["parameters"].append({"id": id, "label": f"实际外形{axis}", "value": value, "unit": "mm", "min": 0, "max": 1e9})

    def refresh_document(self, document, geoms, transforms, locks, prefix=(), parent=None):
        parent = np.eye(4) if parent is None else parent
        members = {m["id"]: m for m in document["assembly"]["members"]}
        for member in members.values():
            if member.get("instanceDocument"):
                self.refresh_document(member["instanceDocument"], geoms, transforms, locks, (*prefix, member["id"]), parent @ matrix(member["position"], member["rotation"]))
                member["interfaces"] = [{**deepcopy(p["interface"]), "id": p["id"], "name": p["name"]} for p in member["instanceDocument"]["ports"]]
                member["parameterValues"].update({p["id"]: p["value"] for p in member["instanceDocument"]["parameters"]})
        for port in document["ports"]:
            source = members.get(port["source"]["memberId"])
            interface = next((i for i in (source or {}).get("interfaces", []) if i["id"] == port["source"]["interfaceId"]), None)
            if interface is None: raise ResizeFailure("尺寸变化使对外接口失效：" + port["name"], "RESIZE_INTERFACE_MISSING")
            path, id = self.endpoint({'path': [*prefix, port['source']['memberId']], 'interfaceId': port['source']['interfaceId']})
            if path not in geoms or id not in geoms[path].frames:
                raise ResizeFailure("对外接口缺少真实几何定位：" + port['name'], "RESIZE_INTERFACE_MISSING")
            port["interface"] = deepcopy(interface)
        for slot in document["slots"]:
            for endpoint in [slot.get("baseInterface"), {"memberId": slot.get("memberId"), "interfaceId": slot.get("interfaceId")}]:
                if endpoint and endpoint.get("memberId") and not any(i["id"] == endpoint["interfaceId"] for i in members.get(endpoint["memberId"], {}).get("interfaces", [])):
                    raise ResizeFailure("尺寸变化使槽位接口失效：" + slot["name"], "RESIZE_INTERFACE_MISSING")
        groups = {}
        for binding in document["bindings"]:
            m = members.get(binding["memberId"])
            if m: groups[binding["parameterId"]] = m["parameterValues"].get(binding["memberParameterId"])
        for p in document["parameters"]:
            if p["id"] in groups and groups[p["id"]] is not None: p["value"] = groups[p["id"]]
        boxes = [exact_bounds(g.shape, np.linalg.inv(parent) @ transforms[path]) for path, g in geoms.items() if path[:len(prefix)] == prefix]
        dimensions = (np.max([b[1] for b in boxes], axis=0)-np.min([b[0] for b in boxes], axis=0)).tolist()
        relations = []
        for relation in self.relations:
            if all(tuple(relation[s]["path"][:len(prefix)]) == prefix for s in ("a", "b")):
                r = deepcopy(relation)
                for side in ("a", "b"): r[side]["path"] = r[side]["path"][len(prefix):]
                if r["mode"] == "preserve-offset": r["offset"] = (parent[:3, :3].T @ r["offset"]).tolist()
                relations.append(r)
        document["schemaVersion"] = "component-1.1"
        document["geometryDesign"] = {"version": 1, "axes": "xyz", "anchor": "bottom-center", "locks": [{**l, "path": l["path"][len(prefix):]} for l in locks if tuple(l["path"][:len(prefix)]) == prefix and len(l["path"]) > len(prefix)], "relations": relations, "dimensions": dimensions,
            "solverVersion": "geometry-resize-1", "inputHash": fingerprint(self.request), "verified": True}
        document["geometryDesign"]["interfaceSnapshots"] = [{"path": list(path[len(prefix):]), "interfaceId": id, "localCenter": frame["center"].tolist(), "localNormal": frame["normal"].tolist(), "localU": frame["u"].tolist(), "source": list(frame["source"])} for path, g in geoms.items() if path[:len(prefix)] == prefix for id, frame in g.frames.items()]
        document["geometryDesign"]["verification"] = {"dimensionToleranceMm": self.tolerances['dimensionMm'], "positionToleranceMm": self.tolerances['positionMm'], "angleToleranceDegrees": self.tolerances['angleDegrees'], "cadValid": True, "leafCount": len(boxes)}
        self.set_dimensions(document, dimensions)
