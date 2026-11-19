import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useThree } from "@react-three/fiber";
import { TransformControls } from "@react-three/drei";
import { Box3, Group, Plane, Raycaster, Vector2, Vector3 } from "three";
import type { Camera, Object3D, Ray } from "three";
import type { TransformControls as TransformControlsImpl } from "three-stdlib";
import type { ComponentMember, Vector3 as Position } from "../domain/component";
import { boundsInSelection, projectBoxBounds, selectionPivot, selectionRectangle, transformSelection } from "./selectionGeometry";
import type { MemberTransform, SelectionMode, SelectionRectangle } from "./selectionGeometry";

type SelectionGroup = { id: string; name?: string; memberIds: string[]; parentId?: string };
export type ViewportEditing = {
  selectedMemberIds: string[];
  groups?: SelectionGroup[];
  onSelectMembers: (ids: string[], mode: SelectionMode) => void;
  onTransformMembers: (transforms: MemberTransform[]) => void;
  onDuplicateMembers?: (ids: string[], offset: Position) => void;
  onEnterComponent?: (id: string, childPath: string[]) => void;
  pasteMembers?: ComponentMember[] | null;
  pasteAnchor?: Position;
  onPasteAt?: (position: Position) => void;
};
type Props = ViewportEditing & {
  members: ComponentMember[];
  mode: "translate" | "rotate";
  planarMove: boolean;
  onRectangle: (rectangle: SelectionRectangle | null) => void;
  onDragging: (dragging: boolean) => void;
  onTransformPreview: (id: string, transform: MemberTransform, mode: "translate" | "rotate") => void;
  ghost: (offset: Position, members?: ComponentMember[]) => ReactNode;
};

function roots(scene: Object3D): Object3D[] {
  const result: Object3D[] = [];
  const visit = (object: Object3D) => {
    if (object.userData.viewportGhost) return;
    if (object.userData.viewportMemberId) result.push(object);
    else object.children.forEach(visit);
  };
  scene.children.forEach(visit);
  return result;
}

function selectionUnit(id: string, groups: SelectionGroup[]): string[] {
  let group = groups.find(item => item.memberIds.includes(id));
  const visited = new Set<string>();
  while (group?.parentId && !visited.has(group.id)) {
    visited.add(group.id);
    const parent = groups.find(item => item.id === group!.parentId);
    if (!parent) break;
    group = parent;
  }
  return group?.memberIds ?? [id];
}

/** Prefer the ground; side views use a camera-facing plane through the copied anchor. */
export function pastePointFromRay(ray: Ray, camera: Camera, anchor: Position): Position | null {
  const ground = new Plane(new Vector3(0, 0, 1), 0);
  let point = Math.abs(ray.direction.z) > .02 ? ray.intersectPlane(ground, new Vector3()) : null;
  if (!point || point.distanceToSquared(ray.origin) < 1e-8) {
    const placementPlane = new Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new Vector3()), new Vector3(...anchor));
    point = ray.intersectPlane(placementPlane, new Vector3());
  }
  return point && point.toArray().every(Number.isFinite) ? point.toArray() as Position : null;
}

/** Native canvas gestures leave the left button available for selection and gizmos. */
export function ViewportInteraction(props: Props) {
  const { camera, scene, gl, size } = useThree();
  const latest = useRef(props);
  latest.current = props;
  const pivot = useMemo(() => new Group(), []);
  const [controlsEpoch, setControlsEpoch] = useState(0);
  const controlsInstance = useRef<TransformControlsImpl | null>(null);
  const captureControls = useCallback((controls: TransformControlsImpl | null) => {
    const previous = controlsInstance.current;
    controlsInstance.current = controls;
    if (previous && previous !== controls) queueMicrotask(() => {
      // StrictMode can detach and reattach the same ref before this task runs.
      if (controlsInstance.current !== previous) previous.dispose();
    });
  }, []);
  const [pastePoint, setPastePoint] = useState<Position | null>(null);
  const [duplicatePreview, setDuplicatePreview] = useState<{ members: ComponentMember[]; offset: Position } | null>(null);
  const altPointerDown = useRef(false);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const gesture = useRef<{ x: number; y: number; mode: SelectionMode; dragging: boolean } | null>(null);
  const dragSnapshot = useRef<{ members: ComponentMember[]; pivot: Position; duplicate: boolean } | null>(null);
  const dragging = useRef(false);
  const placementAt = useCallback((x: number, y: number): Position | null => {
    const rect = gl.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    camera.updateMatrixWorld();
    const caster = new Raycaster();
    caster.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera);
    return pastePointFromRay(caster.ray, camera, latest.current.pasteAnchor ?? [0, 0, 0]);
  }, [camera, gl]);
  const cancelTransform = useCallback((resetControls = true) => {
    if (!dragging.current && !dragSnapshot.current) return;
    const snapshot = dragSnapshot.current;
    dragging.current = false;
    dragSnapshot.current = null;
    setDuplicatePreview(null);
    if (snapshot) {
      const objects = roots(scene);
      for (const original of snapshot.members) {
        const member = latest.current.members.find(item => item.id === original.id) ?? original;
        for (const object of objects) {
          if (object.userData.viewportMemberId !== member.id || object.userData.viewportPatternInstance) continue;
          object.position.set(...member.position); object.rotation.set(...member.rotation); object.updateMatrixWorld(true);
        }
      }
    }
    pivot.position.set(...selectionPivot(latest.current.members.filter(member => latest.current.selectedMemberIds.includes(member.id))));
    pivot.rotation.set(0, 0, 0);
    pivot.updateMatrixWorld(true);
    latest.current.onDragging(false);
    // TransformControls does not handle pointercancel/blur; remount to clear its internal drag state.
    if (resetControls) setControlsEpoch(value => value + 1);
  }, [pivot, scene]);
  const selected = props.members.filter(member => props.selectedMemberIds.includes(member.id));
  const selectedIdsSignature = JSON.stringify([...props.selectedMemberIds].sort());
  const controlsVisible = selected.length > 0 && !props.pasteMembers;
  const signature = JSON.stringify(selected.map(member => [member.id, member.position, member.rotation]));
  useEffect(() => {
    if (dragging.current) cancelTransform();
  }, [selectedIdsSignature, controlsVisible, props.mode, props.planarMove, cancelTransform]);
  useEffect(() => {
    if (dragging.current) return;
    pivot.position.set(...selectionPivot(selected));
    pivot.rotation.set(0, 0, 0);
    pivot.updateMatrixWorld(true);
  }, [signature, pivot]);

  useEffect(() => {
    const canvas = gl.domElement;
    const raycaster = new Raycaster();
    const rayAt = (x: number, y: number) => {
      const rect = canvas.getBoundingClientRect();
      camera.updateMatrixWorld();
      raycaster.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera);
      return rect;
    };
    const hitAt = (x: number, y: number) => {
      rayAt(x, y);
      scene.updateMatrixWorld(true);
      for (const hit of raycaster.intersectObjects(roots(scene), true)) {
        let object: Object3D | null = hit.object;
        let path: string[] = [];
        while (object) {
          if (object.userData.viewportChildPath) path = object.userData.viewportChildPath;
          if (object.userData.viewportMemberId) return { id: object.userData.viewportMemberId as string, path };
          object = object.parent;
        }
      }
      return null;
    };
    let doubleClickTarget: { id: string; path: string[] } | null = null;
    let clickEligible = false;
    let beforeClick: string[] = [];
    const down = (event: PointerEvent) => {
      lastPointer.current = { x: event.clientX, y: event.clientY };
      canvas.closest<HTMLElement>(".viewport")?.focus({ preventScroll: true });
      if (event.button !== 0 || dragging.current) return;
      altPointerDown.current = event.altKey;
      clickEligible = false;
      beforeClick = [...latest.current.selectedMemberIds];
      gesture.current = { x: event.clientX, y: event.clientY, mode: event.ctrlKey || event.metaKey ? "remove" : event.shiftKey ? "add" : "replace", dragging: false };
      // Selection modifiers take precedence over a gizmo overlapping the model.
      if (latest.current.pasteMembers || event.ctrlKey || event.metaKey || event.shiftKey) { event.preventDefault(); event.stopImmediatePropagation(); }
    };
    const move = (event: PointerEvent) => {
      lastPointer.current = { x: event.clientX, y: event.clientY };
      if (latest.current.pasteMembers) {
        setPastePoint(placementAt(event.clientX, event.clientY));
        return;
      }
      const start = gesture.current;
      if (!start || dragging.current) return;
      if (!start.dragging && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 5) return;
      start.dragging = true;
      const rect = canvas.getBoundingClientRect();
      latest.current.onRectangle(selectionRectangle({ x: start.x - rect.left, y: start.y - rect.top }, { x: event.clientX - rect.left, y: event.clientY - rect.top }));
    };
    const up = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const start = gesture.current;
      gesture.current = null;
      clickEligible = false;
      latest.current.onRectangle(null);
      if (!start || dragging.current) return;
      const current = latest.current;
      if (current.pasteMembers) {
        const point = placementAt(event.clientX, event.clientY);
        if (point) current.onPasteAt?.(point);
        event.preventDefault(); event.stopImmediatePropagation();
        return;
      }
      const groups = current.groups ?? [];
      if (!start.dragging) {
        const hit = hitAt(event.clientX, event.clientY);
        clickEligible = true;
        current.onSelectMembers(hit ? selectionUnit(hit.id, groups) : [], start.mode);
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const rectangle = selectionRectangle({ x: start.x - rect.left, y: start.y - rect.top }, { x: event.clientX - rect.left, y: event.clientY - rect.top });
      scene.updateMatrixWorld(true);
      const units = new Map<string, { ids: string[]; box: Box3 }>();
      for (const object of roots(scene)) {
        const ids = selectionUnit(object.userData.viewportMemberId, groups);
        const key = [...ids].sort().join("|");
        const unit = units.get(key) ?? { ids, box: new Box3() };
        unit.box.union(new Box3().setFromObject(object));
        units.set(key, unit);
      }
      const ids = [...units.values()].filter(unit => boundsInSelection(projectBoxBounds(unit.box, camera, { width: rect.width, height: rect.height }), rectangle)).flatMap(unit => unit.ids);
      current.onSelectMembers([...new Set(ids)], start.mode);
    };
    const click = (event: MouseEvent) => {
      if (event.detail === 1) {
        const hit = hitAt(event.clientX, event.clientY);
        doubleClickTarget = clickEligible && !event.ctrlKey && !event.metaKey && !event.shiftKey && hit
          && selectionUnit(hit.id, latest.current.groups ?? []).every(id => beforeClick.includes(id)) ? hit : null;
      }
    };
    const doubleClick = (event: MouseEvent) => {
      const target = doubleClickTarget;
      doubleClickTarget = null;
      if (event.button !== 0 || !clickEligible || !target || latest.current.pasteMembers || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const hit = hitAt(event.clientX, event.clientY);
      if (!hit || hit.id !== target.id || JSON.stringify(hit.path) !== JSON.stringify(target.path)) return;
      if (latest.current.members.find(member => member.id === hit.id)?.template.templateKind === "component") latest.current.onEnterComponent?.(hit.id, hit.path);
      else latest.current.onSelectMembers([hit.id], "replace");
    };
    const cancel = () => {
      altPointerDown.current = false;
      gesture.current = null; clickEligible = false; doubleClickTarget = null;
      latest.current.onRectangle(null); cancelTransform();
    };
    const keyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (gesture.current || dragging.current) event.preventDefault();
      cancel();
    };
    const contextMenu = (event: Event) => event.preventDefault();
    canvas.addEventListener("pointerdown", down, true);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", keyDown);
    canvas.addEventListener("click", click);
    canvas.addEventListener("dblclick", doubleClick);
    canvas.addEventListener("contextmenu", contextMenu);
    return () => {
      canvas.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("blur", cancel);
      window.removeEventListener("keydown", keyDown);
      canvas.removeEventListener("click", click);
      canvas.removeEventListener("dblclick", doubleClick);
      canvas.removeEventListener("contextmenu", contextMenu);
      gesture.current = null; latest.current.onRectangle(null); cancelTransform(false);
    };
  }, [camera, gl, scene, placementAt, cancelTransform]);

  useEffect(() => {
    if (!props.pasteMembers) { setPastePoint(null); return; }
    const rect = gl.domElement.getBoundingClientRect();
    const pointer = lastPointer.current ?? { x: rect.left + size.width / 2, y: rect.top + size.height / 2 };
    setPastePoint(placementAt(pointer.x, pointer.y));
  }, [props.pasteMembers, props.pasteAnchor, gl, size.width, size.height, placementAt]);

  function previewTransform() {
    const snapshot = dragSnapshot.current;
    if (!snapshot) return [];
    if (props.planarMove) pivot.position.z = snapshot.pivot[2];
    const transforms = transformSelection(snapshot.members, snapshot.pivot, pivot.position.toArray() as Position, [pivot.rotation.x, pivot.rotation.y, pivot.rotation.z]);
    if (snapshot.duplicate) {
      setDuplicatePreview({ members: snapshot.members, offset: pivot.position.toArray().map((value, axis) => value - snapshot.pivot[axis]) as Position });
      return transforms;
    }
    const objects = roots(scene);
    for (const transform of transforms) for (const object of objects) {
      if (object.userData.viewportMemberId !== transform.id || object.userData.viewportPatternInstance) continue;
      object.position.set(...transform.position); object.rotation.set(...transform.rotation); object.updateMatrixWorld(true);
    }
    const first = transforms.find(item => item.id === props.selectedMemberIds[0]) ?? transforms[0];
    if (first) props.onTransformPreview(first.id, first, props.mode);
    return transforms;
  }
  const anchor = props.pasteAnchor ?? [0, 0, 0];
  return <>
    <primitive object={pivot}/>
    {controlsVisible && <TransformControls ref={captureControls} key={controlsEpoch} object={pivot} mode={props.mode} space="world" size={.9} showX showY showZ={!props.planarMove || props.mode === "rotate"}
      onMouseDown={() => {
        dragging.current = true; gesture.current = null; props.onRectangle(null);
        const duplicate = props.mode === "translate" && altPointerDown.current && Boolean(props.onDuplicateMembers);
        altPointerDown.current = false;
        dragSnapshot.current = { members: structuredClone(selected), pivot: pivot.position.toArray() as Position, duplicate };
        props.onDragging(true);
      }}
      onObjectChange={previewTransform}
      onMouseUp={() => {
        if (!dragging.current || !dragSnapshot.current) return;
        const snapshot = dragSnapshot.current;
        const transforms = previewTransform();
        dragSnapshot.current = null; dragging.current = false;
        setDuplicatePreview(null);
        if (snapshot.duplicate) {
          const offset = pivot.position.toArray().map((value, axis) => value - snapshot.pivot[axis]) as Position;
          pivot.position.set(...snapshot.pivot);
          if (offset.every(Number.isFinite) && offset.some(value => Math.abs(value) > 1e-8)) props.onDuplicateMembers?.(snapshot.members.map(member => member.id), offset);
        } else if (transforms.length) props.onTransformMembers(transforms);
        pivot.rotation.set(0, 0, 0);
        pivot.updateMatrixWorld(true);
        props.onDragging(false);
      }}/>}
    {duplicatePreview && props.ghost(duplicatePreview.offset, duplicatePreview.members)}
    {props.pasteMembers && pastePoint && props.ghost(pastePoint.map((coordinate, index) => coordinate - anchor[index]) as Position)}
  </>;
}
