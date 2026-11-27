import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Grid, Line, TransformControls } from "@react-three/drei";
import { AlertTriangle, Boxes, Copy, Eye, LocateFixed, Move3d, Rotate3d, RotateCcw, Trash2 } from "lucide-react";
import { BufferAttribute, BufferGeometry, DoubleSide } from "three";
import { Vector3 as ThreeVector3 } from "three";
import type { Group, Object3D } from "three";
import type { ComponentMember, ComponentParameter, ComponentPattern, InterfaceSelection, ParameterBinding, PartInterface, ResolvedPartInterface, Vector3 } from "../domain/component";
import { interfaceHighlights } from "../domain/interfaceGeometry";
import { resolveComponentComposition } from "../domain/composition";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { fetchPublishedComponent } from "../library/componentPublications";
import { useTemplatePreview } from "../library/useTemplatePreview";
import { DEFAULT_CAMERA_STORAGE_KEY } from "./cameraView";
import { ViewportCameraControls } from "./ViewportCameraControls";
import { ViewportInteraction } from "./ViewportInteraction";
import type { ViewportEditing } from "./ViewportInteraction";
import type { SelectionRectangle } from "./selectionGeometry";
import { rotationDelta, transformSelection } from "./selectionGeometry";

const ManagedSelection = createContext(false);
const GhostPreview = createContext(false);

type VisualMember = ComponentMember & { sourceMemberId?: string; patternInstance?: boolean };
const INTERFACE_HIGHLIGHT_COLOR = "#12b8d4";
type TransformMode = "translate" | "rotate";
type TransformSnapshot = { position: Vector3; rotation: Vector3; mode: TransformMode };
type HoverGroup = { id: string; name?: string; memberIds: string[]; parentId?: string };

function topLevelHoverGroup(groups: readonly HoverGroup[] | undefined, memberId: string | null): HoverGroup | undefined {
  let group = memberId ? groups?.find(item => item.memberIds.includes(memberId)) : undefined;
  const visited = new Set<string>();
  while (group?.parentId && !visited.has(group.id)) {
    visited.add(group.id);
    const parent = groups?.find(item => item.id === group!.parentId);
    if (!parent) break;
    group = parent;
  }
  return group;
}

function materialText(member: ComponentMember): string {
  const candidate = member as ComponentMember & {
    material?: unknown;
    materialName?: unknown;
    materialType?: unknown;
    materialCategory?: unknown;
  };
  const materialObject = typeof candidate.material === "object" && candidate.material !== null
    ? candidate.material as Record<string, unknown>
    : undefined;
  const materialParameterIds = new Set((member.parameterDefinitions || [])
    .filter(parameter => /material|材质|材料/i.test(parameter.label))
    .map(parameter => parameter.id));
  const values = [
    candidate.materialName,
    candidate.materialType,
    candidate.materialCategory,
    materialObject
      ? materialObject.name
      : candidate.material,
    materialObject?.type,
    materialObject?.category,
    member.name,
    member.code,
    ...Object.entries(member.parameterValues)
      .filter(([key]) => /material|材质|材料/i.test(key) || materialParameterIds.has(key))
      .map(([, value]) => value),
  ];
  return values.filter(value => typeof value === "string").join(" ").trim().toLowerCase();
}
function isTransparentMaterial(member: ComponentMember): boolean {
  const text = materialText(member);
  return /钢化玻璃|夹胶玻璃|亚克力|有机玻璃|玻璃(?!钢|纤维)|\bglass\b(?!\s*fiber)|acrylic|plexiglass/i.test(text);
}
function PartMaterial({ member, color }: { member: ComponentMember; color: string }) {
  const ghost = useContext(GhostPreview);
  const transparent = ghost || isTransparentMaterial(member);
  return <meshStandardMaterial color={color} metalness={transparent ? .05 : .18} roughness={transparent ? .2 : .55} transparent={transparent} opacity={ghost ? .3 : transparent ? .45 : 1} depthWrite={!transparent} side={DoubleSide}/>;
}

function CompatibleOutline() {
  const x = 100, y = 50, z = 40;
  const points: [number, number, number][] = [
    [-x, -y, -z], [x, -y, -z], [x, -y, -z], [x, y, -z], [x, y, -z], [-x, y, -z], [-x, y, -z], [-x, -y, -z],
    [-x, -y, z], [x, -y, z], [x, -y, z], [x, y, z], [x, y, z], [-x, y, z], [-x, y, z], [-x, -y, z],
    [-x, -y, -z], [-x, -y, z], [x, -y, -z], [x, -y, z], [x, y, -z], [x, y, z], [-x, y, -z], [-x, y, z],
  ];
  return <Line points={points} segments dashed color="#5c9ebc" lineWidth={1.5} />;
}

type ScreenPoint = { x: number; y: number };
type TooltipSource = "pointer" | "panel" | "hover";
type MemberTooltip = { memberId: string; point: ScreenPoint; opacity: number; source: TooltipSource; token: number };
type ModelStatusEntry = { id: string; name: string; updating: boolean; error: string; retry: () => void };
const ModelStatusRegistry = createContext<(id: string, entry: ModelStatusEntry | null) => void>(() => undefined);

function ModelStatus({ name, preview }: { name: string; preview: ReturnType<typeof useTemplatePreview> }) {
  const id = useId();
  const register = useContext(ModelStatusRegistry);
  const ghost = useContext(GhostPreview);
  const retry = useRef(preview.retry);
  const updating = Boolean(preview.result);
  useEffect(() => { retry.current = preview.retry; }, [preview.retry]);
  useEffect(() => {
    if (ghost || (!preview.loading && !preview.error)) {
      register(id, null);
      return;
    }
    register(id, { id, name, updating, error: preview.error, retry: () => retry.current() });
    return () => register(id, null);
  }, [id, name, preview.error, preview.loading, updating, register, ghost]);
  return null;
}

function DraggableGroup({ member, selected, compatible = false, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove, movable = true, children }: { member: ComponentMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean; movable?: boolean; children: (hovered: boolean) => ReactNode }) {
  const geometryReadOnly = useContext(GeometryPreviewReadOnly);
  const managed = useContext(ManagedSelection);
  const firstMemberHit = (intersections: { eventObject: Object3D }[]) => intersections.find(hit => hit.eventObject.userData.viewportMemberId)?.eventObject;
  const groupRef = useRef<Group | null>(null); const [groupObject, setGroupObject] = useState<Group | null>(null); const positionRef = useRef<Vector3>(member.position); const rotationRef = useRef<Vector3>(member.rotation); const [localPosition, setLocalPosition] = useState<Vector3>(member.position); const [hovered, setHovered] = useState(false);
  const captureGroup = useCallback((group: Group | null) => { groupRef.current = group; setGroupObject(group); }, []);
  useEffect(() => { positionRef.current = member.position; rotationRef.current = member.rotation; setLocalPosition(member.position); }, [member.position, member.rotation]);
  const readTransform = (): TransformSnapshot | null => {
    const object = groupRef.current;
    if (!object) return null;
    if (planarMove) object.position.z = member.position[2];
    object.updateMatrixWorld(true);
    positionRef.current = [object.position.x, object.position.y, object.position.z];
    rotationRef.current = [object.rotation.x, object.rotation.y, object.rotation.z];
    return { position: positionRef.current, rotation: rotationRef.current, mode: transformMode };
  };
  return <>
    <group ref={captureGroup} position={localPosition} rotation={member.rotation} userData={{ viewportMemberId: (member as VisualMember).sourceMemberId || member.id, viewportPatternInstance: Boolean((member as VisualMember).patternInstance) }}
      onPointerOver={event => {
        if (firstMemberHit(event.intersections) !== event.eventObject) return;
        setHovered(true); onHover?.(true); onPointerHover?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY });
      }}
      onPointerMove={event => {
        if (firstMemberHit(event.intersections) !== event.eventObject) { setHovered(false); return; }
        // Retain the root's hover record while moving between overlapping child meshes.
        setHovered(true); onHover?.(true); onPointerHover?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY });
      }}
      onPointerOut={event => {
        setHovered(false);
        if (!firstMemberHit(event.intersections)) onHover?.(false);
        onPointerHoverEnd?.();
      }}
      onClick={event => { event.stopPropagation(); if (!managed) { onSelect(); onPointerSelect?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); } }}>
      {compatible && !selected && <CompatibleOutline />}
      {children(hovered)}
    </group>
    {movable && !geometryReadOnly && !managed && selected && groupObject && <TransformControls object={groupObject} mode={transformMode} space="world" showX showY showZ={!planarMove || transformMode === "rotate"} size={0.9}
      onMouseDown={() => onDragStateChange?.(true)}
      onObjectChange={() => { const snapshot = readTransform(); if (snapshot) onTransformPreview(snapshot); }}
      onMouseUp={() => { const snapshot = readTransform(); if (snapshot) { onTransformPreview(snapshot); onTransformCommit(snapshot); } onDragStateChange?.(false); }} />}
  </>;
}

function partTemplate(member: ComponentMember): PublishedTemplate {
  return { templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId, version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name, createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "" };
}

function PartMesh({ member, materialColor, position = [0, 0, 0] as Vector3, childPath }: { member: ComponentMember; materialColor?: string; position?: Vector3; childPath?: string[] }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  return <group position={position} rotation={member.rotation} userData={{ viewportChildPath: childPath }}><ModelStatus name={member.name} preview={preview}/>{preview.result && <mesh geometry={preview.result.geometry} dispose={null}>
    <PartMaterial member={member} color={materialColor || "#7394b0"}/>
  </mesh>}</group>;
}

function InterfaceMarker({ geometry, item, member, resolvedInterfaces }: { geometry: BufferGeometry | null; item: PartInterface | undefined; member: ComponentMember; resolvedInterfaces?: ResolvedPartInterface[] }) {
  const markers = useMemo(() => {
    if (!geometry?.boundingBox || !item) return [];
    const highlights = interfaceHighlights({ min: geometry.boundingBox.min.toArray() as Vector3, max: geometry.boundingBox.max.toArray() as Vector3 }, item, resolvedInterfaces ?? member.resolvedInterfaces);
    return highlights.map(highlight => {
      const [a, b, c, d] = highlight.corners;
      const markerGeometry = new BufferGeometry();
      markerGeometry.setAttribute("position", new BufferAttribute(new Float32Array([...a, ...b, ...c, ...a, ...c, ...d]), 3));
      markerGeometry.computeVertexNormals();
      return { geometry: markerGeometry, region: highlight.region };
    });
  }, [geometry, item, member.resolvedInterfaces, resolvedInterfaces]);
  useEffect(() => () => markers.forEach(marker => marker.geometry.dispose()), [markers]);
  return <>{markers.map((marker, index) => <mesh key={index} geometry={marker.geometry} renderOrder={4}>
    <meshBasicMaterial color={INTERFACE_HIGHLIGHT_COLOR} transparent opacity={marker.region ? .82 : .42} depthWrite={false} side={DoubleSide}/>
  </mesh>)}</>;
}

function NestedComponentVisual({ member, selected = false, path = [] }: { member: ComponentMember; selected?: boolean; path?: string[] }) {
  const [document, setDocument] = useState<import("../domain/component").ComponentDocument | null>(null);
  useEffect(() => {
    let active = true;
    if (member.instanceDocument) { setDocument(member.instanceDocument); return; }
    void fetchPublishedComponent(member.template.publicationId).then(publication => {
      if (active && publication.templateId === member.template.templateId && publication.version === member.template.version && publication.sha256.toLowerCase() === member.template.sha256.toLowerCase()) setDocument(publication.document);
    }).catch(() => { if (active) setDocument(null); });
    return () => { active = false; };
  }, [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.instanceDocument]);
  const childMembers = useMemo(() => document ? resolveComponentComposition(document, member.parameterValues) : [], [document, JSON.stringify(member.parameterValues)]);
  if (!document) return null;
  return <group position={member.position} rotation={member.rotation}>
    {childMembers.map(child => child.template.templateKind === "component"
      ? <NestedComponentVisual key={child.id} member={child} selected={selected} path={[...path, child.id]}/>
      : <PartMesh key={child.id} member={child} position={child.position} materialColor={selected ? "#c89532" : undefined} childPath={[...path, child.id]}/>) }
  </group>;
}

function MemberModel({ member, selected, hoverHighlighted = false, compatible, selectedInterface, selectedInterfaceResolved, connectedInterfaces, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove }: { member: VisualMember; selected: boolean; hoverHighlighted?: boolean; compatible?: boolean; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; connectedInterfaces?: ReadonlySet<string>; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  const sourceMemberId = member.sourceMemberId || member.id;
  const selectedItem = selectedInterface?.memberId === sourceMemberId ? member.interfaces.find(item => item.id === selectedInterface.interfaceId) : undefined;
  const connectedItems = member.interfaces.filter(item => connectedInterfaces?.has(`${sourceMemberId}:${item.id}`));
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onTransformPreview={onTransformPreview} onTransformCommit={onTransformCommit} onDragStateChange={onDragStateChange} onPointerSelect={onPointerSelect} onPointerHover={onPointerHover} onPointerHoverEnd={onPointerHoverEnd} transformMode={transformMode} planarMove={planarMove} movable={!member.patternInstance}>
    {hovered => {
      const color = selected ? "#c89532" : hovered || hoverHighlighted ? "#d9b85f" : compatible ? "#a8cad8" : "#7394b0";
      return preview.result?.geometry
        ? <><ModelStatus name={member.name} preview={preview}/><mesh geometry={preview.result.geometry} dispose={null}><PartMaterial member={member} color={color}/></mesh>{connectedItems.map(item => <InterfaceMarker key={`connected-${item.id}`} geometry={preview.result!.geometry} item={item} member={member} resolvedInterfaces={item.id === selectedItem?.id ? (selectedInterfaceResolved ?? preview.info?.resolvedInterfaces) : preview.info?.resolvedInterfaces}/>) }{selectedItem && !connectedItems.some(item => item.id === selectedItem.id) && <InterfaceMarker geometry={preview.result!.geometry} item={selectedItem} member={member} resolvedInterfaces={selectedInterfaceResolved ?? preview.info?.resolvedInterfaces}/>}</>
        : <><ModelStatus name={member.name} preview={preview}/><mesh><boxGeometry args={[160, 80, 60]}/><meshBasicMaterial color={color} transparent={isTransparentMaterial(member)} opacity={isTransparentMaterial(member) ? .45 : 1}/></mesh></>;
    }}
  </DraggableGroup>;
}

function ComponentMemberModel({ member, selected, hoverHighlighted = false, compatible, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove }: { member: VisualMember; selected: boolean; hoverHighlighted?: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean }) {
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onTransformPreview={onTransformPreview} onTransformCommit={onTransformCommit} onDragStateChange={onDragStateChange} onPointerSelect={onPointerSelect} onPointerHover={onPointerHover} onPointerHoverEnd={onPointerHoverEnd} transformMode={transformMode} planarMove={planarMove} movable={!member.patternInstance}>
    {/* The outer drag group owns the instance transform; avoid applying rotation twice. */}
    {hovered => <NestedComponentVisual member={{ ...member, position: [0, 0, 0], rotation: [0, 0, 0] }} selected={selected || hovered || hoverHighlighted}/>}
  </DraggableGroup>;
}

function PreviewScene({ editing, onRectangle, ghost, hoveredMemberIds, members, parameters, bindings, patterns, selectedMemberId, selectedInterface, selectedInterfaceResolved, connectedInterfaces, panelSelectionToken, compatibleMemberIds, transformMode, planarMove, onSelectMember, onHoverMember, onTransformMember, onTransformPreview, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, onScreenSelection, geometry, cameraResetToken, cameraStorageKey }: { editing?: ViewportEditing; onRectangle: (rectangle: SelectionRectangle | null) => void; ghost: (offset: Vector3, members?: ComponentMember[]) => ReactNode; hoveredMemberIds: ReadonlySet<string>; members: ComponentMember[]; parameters: ComponentParameter[]; bindings: ParameterBinding[]; patterns: ComponentPattern[]; selectedMemberId: string | null; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; connectedInterfaces?: ReadonlySet<string>; panelSelectionToken: number; compatibleMemberIds?: ReadonlySet<string>; transformMode: TransformMode; planarMove: boolean; onSelectMember: (id: string) => void; onHoverMember?: (id: string | null) => void; onTransformMember: (id: string, position: Vector3, rotation: Vector3) => void; onTransformPreview: (id: string, snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (id: string, point: ScreenPoint) => void; onPointerHover?: (id: string, point: ScreenPoint) => void; onPointerHoverEnd?: (id: string) => void; onScreenSelection?: (id: string, point: ScreenPoint) => void; geometry: BufferGeometry | null; cameraResetToken: number; cameraStorageKey: string }) {
  const { camera, size } = useThree(); const [cameraEnabled, setCameraEnabled] = useState(true);
  const setDragging = (dragging: boolean) => { setCameraEnabled(!dragging); onDragStateChange?.(dragging); };
  const updateMemberTransform = (id: string, snapshot: TransformSnapshot) => onTransformPreview(id, snapshot);
  const memberRadius = members.length ? Math.max(8, ...members.map(member => Math.hypot(...member.position) + Math.max(0, ...Object.values(member.parameterValues).filter((value): value is number => typeof value === "number" && Number.isFinite(value))) / 2)) : 8;
  const patternRadius = patterns.length ? Math.max(...patterns.map(pattern => (parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0) * Math.max(1, (parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1) - 1))) : 0;
  const radius = members.length
    ? Math.max(memberRadius, patternRadius)
    : geometry?.boundingSphere?.radius || Math.max(memberRadius, patternRadius);
  const renderedMembers = useMemo<VisualMember[]>(() => resolveComponentComposition({ assembly: { members, mates: [] }, parameters, bindings, patterns }, Object.fromEntries(parameters.map(parameter => [parameter.id, parameter.value]))), [members, parameters, bindings, patterns]);
  const screenPosition = useRef(new ThreeVector3());
  const lastPanelSelectionToken = useRef(panelSelectionToken);
  const lastSelectedMemberId = useRef(selectedMemberId);
  useEffect(() => {
    const selectionChanged = lastSelectedMemberId.current !== selectedMemberId;
    lastSelectedMemberId.current = selectedMemberId;
    if (!selectionChanged && lastPanelSelectionToken.current === panelSelectionToken) return;
    lastPanelSelectionToken.current = panelSelectionToken;
    if (!selectedMemberId || !onScreenSelection) return;
    const member = renderedMembers.find(item => (item.sourceMemberId || item.id) === selectedMemberId);
    if (!member) return;
    screenPosition.current.set(...member.position).project(camera);
    const width = size.width, height = size.height;
    const point = { x: (screenPosition.current.x + 1) / 2 * width, y: (-screenPosition.current.y + 1) / 2 * height };
    onScreenSelection(selectedMemberId, point);
  }, [camera, onScreenSelection, panelSelectionToken, renderedMembers, selectedMemberId, size.height, size.width]);
  return <><Grid args={[10000, 10000]} rotation={[Math.PI / 2, 0, 0]} cellSize={100} cellThickness={.5} sectionSize={1000} sectionThickness={1} fadeDistance={100000} cellColor="#a8b2ba" sectionColor="#7c929f" side={DoubleSide}/><axesHelper args={[100]}/>{geometry && !renderedMembers.length && <mesh><primitive object={geometry} attach="geometry" dispose={null}/><meshStandardMaterial color="#7394b0" metalness={.18} roughness={.55} side={DoubleSide}/></mesh>}{renderedMembers.map((member, index) => {
    const memberId = member.sourceMemberId || member.id;
    const compatible = compatibleMemberIds?.has(memberId) || false;
    return member.template.templateKind === "component"
      ? <ComponentMemberModel key={`${member.id}-${index}`} member={member} selected={editing ? editing.selectedMemberIds.includes(memberId) : memberId === selectedMemberId} hoverHighlighted={hoveredMemberIds.has(memberId)} compatible={compatible} onSelect={() => onSelectMember(memberId)} onPointerSelect={point => onPointerSelect?.(memberId, point)} onPointerHover={point => onPointerHover?.(memberId, point)} onPointerHoverEnd={() => onPointerHoverEnd?.(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onTransformPreview={snapshot => updateMemberTransform(memberId, snapshot)} onTransformCommit={snapshot => onTransformMember(memberId, snapshot.position, snapshot.rotation)} onDragStateChange={setDragging} transformMode={transformMode} planarMove={planarMove}/>
      : <MemberModel key={`${member.id}-${index}`} member={member} selected={editing ? editing.selectedMemberIds.includes(memberId) : memberId === selectedMemberId} hoverHighlighted={hoveredMemberIds.has(memberId)} compatible={compatible} selectedInterface={selectedInterface} selectedInterfaceResolved={selectedInterfaceResolved} onSelect={() => onSelectMember(memberId)} onPointerSelect={point => onPointerSelect?.(memberId, point)} onPointerHover={point => onPointerHover?.(memberId, point)} onPointerHoverEnd={() => onPointerHoverEnd?.(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onTransformPreview={snapshot => updateMemberTransform(memberId, snapshot)} onTransformCommit={snapshot => onTransformMember(memberId, snapshot.position, snapshot.rotation)} onDragStateChange={setDragging} transformMode={transformMode} planarMove={planarMove}/>;
  })}{editing && <ViewportInteraction {...editing} members={members} mode={transformMode} planarMove={planarMove} onRectangle={onRectangle} onDragging={setDragging} ghost={ghost} onTransformPreview={(id, transform, mode) => onTransformPreview(id, { ...transform, mode })}/>}<ViewportCameraControls enabled={cameraEnabled} radius={radius} hasContent={members.length > 0 || Boolean(geometry)} resetToken={cameraResetToken} storageKey={cameraStorageKey}/></>;
}

const GeometryPreviewReadOnly = createContext(false);

type Props = Partial<ViewportEditing> & { readOnly?: boolean; onToggleGroup?: () => void; onCopySelection?: () => void; onStartPaste?: () => void; onCancelPaste?: () => void; onRemoveSelection?: () => void; undoCount?: number; onUndoModel?: () => void; undoDisabled?: boolean; members: ComponentMember[]; parameters?: ComponentParameter[]; bindings?: ParameterBinding[]; patterns?: ComponentPattern[]; mateCount: number; previewTemplate: PublishedTemplate | null; onShowAssembly?: () => void; selectedMemberId?: string | null; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; panelSelectionToken?: number; compatibleMemberIds?: ReadonlySet<string>; onSelectMember?: (id: string | null) => void; onHoverMember?: (id: string | null) => void; onTransformMember?: (id: string, position: Vector3, rotation: Vector3) => void; onSetPlanarMovement?: (id: string, enabled: boolean) => void; onGroundMember?: (id: string) => void; onCopyMember?: (id: string) => void; onRemoveMember?: (id: string) => void; onDropTemplate?: () => void; cameraStorageKey?: string };
export function ComponentViewport({ readOnly = false, selectedMemberIds, groups, onSelectMembers, onTransformMembers, onDuplicateMembers, onEnterComponent, pasteMembers, pasteAnchor, onPasteAt, onToggleGroup, onCopySelection, onStartPaste, onCancelPaste, onRemoveSelection, undoCount = 0, onUndoModel, undoDisabled = false, members, parameters = [], bindings = [], patterns = [], mateCount, previewTemplate, onShowAssembly, selectedMemberId = null, selectedInterface = null, selectedInterfaceResolved, panelSelectionToken = 0, compatibleMemberIds, onSelectMember = () => undefined, onHoverMember, onTransformMember = () => undefined, onSetPlanarMovement, onGroundMember, onCopyMember = () => undefined, onRemoveMember = () => undefined, onDropTemplate = () => undefined, cameraStorageKey = DEFAULT_CAMERA_STORAGE_KEY }: Props) {
  const isPreview = previewTemplate !== null;
  const [rectangle, setRectangle] = useState<SelectionRectangle | null>(null);
  const editing: ViewportEditing | undefined = !readOnly && !isPreview && onSelectMembers && onTransformMembers ? { selectedMemberIds: selectedMemberIds ?? (selectedMemberId ? [selectedMemberId] : []), groups, onSelectMembers, onTransformMembers, onDuplicateMembers, onEnterComponent, pasteMembers, pasteAnchor, onPasteAt } : undefined;
  const selectionCount = editing?.selectedMemberIds.length ?? (selectedMemberId ? 1 : 0);
  const preview = useTemplatePreview(previewTemplate?.templateKind === "component" ? null : previewTemplate);
  const loaded = preview.result;
  const [hoveredMemberId, setHoveredMemberId] = useState<string | null>(null);
  const hoverHandler = useCallback((id: string | null) => { setHoveredMemberId(id); onHoverMember?.(id); }, [onHoverMember]);
  const hoveredMemberIds = useMemo(() => new Set(topLevelHoverGroup(groups, hoveredMemberId)?.memberIds ?? (hoveredMemberId ? [hoveredMemberId] : [])), [groups, hoveredMemberId]);
  const [tooltip, setTooltip] = useState<MemberTooltip | null>(null);
  const [transformMode, setTransformMode] = useState<TransformMode>("translate");
  const [cameraResetToken, setCameraResetToken] = useState(0);
  const [planarMove, setPlanarMove] = useState(false);
  const [transformReadout, setTransformReadout] = useState<TransformSnapshot | null>(null);
  const [transformDraft, setTransformDraft] = useState<{ memberId: string; field: "position" | "rotation"; index: number; value: string } | null>(null);
  const [modelStatuses, setModelStatuses] = useState<ModelStatusEntry[]>([]);
  const cancelTransformCommit = useRef(false);
  const transformDragging = useRef(false);
  const shortcutHandler = useRef<(event: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => shortcutHandler.current(event);
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);
  const tooltipRef = useRef<typeof tooltip>(null);
  const sceneRef = useRef<HTMLDivElement | null>(null);
  const tooltipTimers = useRef<number[]>([]);
  const tooltipToken = useRef(0);
  const selectedMember = members.find(member => member.id === selectedMemberId) || null;
  useEffect(() => { setPlanarMove(Boolean(selectedMember?.planarMovement)); setTransformReadout(null); setTransformDraft(null); }, [selectedMemberId, selectedMember?.planarMovement]);
  const clearTooltipTimers = () => { tooltipTimers.current.forEach(timer => window.clearTimeout(timer)); tooltipTimers.current = []; };
  const showTimedTooltip = (memberId: string, point: ScreenPoint, source: Exclude<TooltipSource, "hover">) => {
    clearTooltipTimers();
    const token = ++tooltipToken.current;
    const next = { memberId, point, opacity: 1, source, token };
    tooltipRef.current = next;
    setTooltip(next);
    tooltipTimers.current.push(window.setTimeout(() => setTooltip(current => {
      if (current?.token !== token) return current;
      const fading = { ...current, opacity: 0 };
      tooltipRef.current = fading;
      return fading;
    }), 3000));
    tooltipTimers.current.push(window.setTimeout(() => setTooltip(current => {
      if (current?.token !== token) return current;
      tooltipRef.current = null;
      return null;
    }), 3800));
  };
  const showHoverTooltip = (memberId: string, point: ScreenPoint) => {
    const current = tooltipRef.current;
    if (current?.memberId === memberId && current.source === "hover") {
      const updated = { ...current, point };
      tooltipRef.current = updated;
      setTooltip(updated);
      return;
    }
    clearTooltipTimers();
    const next = { memberId, point, opacity: 1, source: "hover" as const, token: ++tooltipToken.current };
    tooltipRef.current = next;
    setTooltip(next);
  };
  const clearHoverTooltip = (memberId?: string) => {
    const current = tooltipRef.current;
    if (current?.source !== "hover" || (memberId && current.memberId !== memberId)) return;
    clearTooltipTimers();
    tooltipRef.current = null;
    setTooltip(null);
  };
  useEffect(() => {
    if (!hoveredMemberId || members.some(member => member.id === hoveredMemberId)) return;
    hoverHandler(null);
    clearHoverTooltip(hoveredMemberId);
  }, [hoveredMemberId, members, hoverHandler]);
  useEffect(() => () => clearTooltipTimers(), []);
  const handlePointerSelect = (id: string, point: ScreenPoint) => {
    const rect = sceneRef.current?.getBoundingClientRect();
    onSelectMember(id);
    showTimedTooltip(id, { x: point.x - (rect?.left || 0), y: point.y - (rect?.top || 0) }, "pointer");
  };
  const handlePointerHover = (id: string, point: ScreenPoint) => {
    hoverHandler(id);
    const group = topLevelHoverGroup(groups, id);
    if (!group && selectedMemberId === id) return;
    if (!group && tooltipRef.current?.memberId === id && tooltipRef.current.source !== "hover") return;
    const rect = sceneRef.current?.getBoundingClientRect();
    const localPoint = { x: point.x - (rect?.left || 0), y: point.y - (rect?.top || 0) };
    showHoverTooltip(id, localPoint);
  };
  const handlePointerHoverEnd = (id: string) => { clearHoverTooltip(id); };
  const handleScreenSelection = (id: string, point: ScreenPoint) => { showTimedTooltip(id, point, "panel"); };
  const handleTransformPreview = (id: string, snapshot: TransformSnapshot) => { setTransformReadout(snapshot); };
  const registerModelStatus = useCallback((id: string, entry: ModelStatusEntry | null) => {
    setModelStatuses(current => {
      const index = current.findIndex(status => status.id === id);
      if (!entry) return index < 0 ? current : current.filter(status => status.id !== id);
      if (index < 0) return [...current, entry];
      const previous = current[index];
      if (previous.name === entry.name && previous.updating === entry.updating && previous.error === entry.error) return current;
      const updated = [...current];
      updated[index] = entry;
      return updated;
    });
  }, []);
  const updateSelectedTransform = (field: "position" | "rotation", index: number, value: number) => {
    if (readOnly || !selectedMember || !Number.isFinite(value)) return;
    const vector: Vector3 = [...selectedMember[field]];
    vector[index] = field === "rotation" ? value * Math.PI / 180 : value;
    if (editing && selectionCount > 1) {
      const selected = members.filter(member => editing.selectedMemberIds.includes(member.id));
      const delta: Vector3 = field === "rotation" ? rotationDelta(selectedMember.rotation, vector) : [0, 0, 0];
      editing.onTransformMembers(transformSelection(selected, selectedMember.position, field === "position" ? vector : selectedMember.position, delta));
      return;
    }
    onTransformMember(selectedMember.id, field === "position" ? vector : selectedMember.position, field === "rotation" ? vector : selectedMember.rotation);
  };
  const commitTransformInput = () => {
    if (cancelTransformCommit.current) { cancelTransformCommit.current = false; setTransformDraft(null); return; }
    if (!transformDraft || !selectedMember || transformDraft.memberId !== selectedMember.id) { setTransformDraft(null); return; }
    const value = Number(transformDraft.value);
    if (transformDraft.value.trim() && Number.isFinite(value)) updateSelectedTransform(transformDraft.field, transformDraft.index, value);
    setTransformDraft(null);
  };
  const clearSelection = () => { clearTooltipTimers(); tooltipRef.current = null; setTooltip(null); hoverHandler(null); onSelectMember(null); };
  const handleViewportPointerLeave = () => { hoverHandler(null); clearHoverTooltip(); };
  const tooltipMember = tooltip ? members.find(member => member.id === tooltip.memberId) : undefined;
  const tooltipName = tooltip?.source === "hover" ? topLevelHoverGroup(groups, tooltip.memberId)?.name || tooltipMember?.name : tooltipMember?.name;
  shortcutHandler.current = event => {
    if (event.defaultPrevented || readOnly || isPreview || (event.target instanceof HTMLElement && (event.target.isContentEditable || event.target.closest("input,textarea,select")))) return;
    if (transformDragging.current && event.key !== "Escape") return;
    const command = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (event.key === "Escape") { event.preventDefault(); onCancelPaste?.(); setRectangle(null); return; }
    if (command && key === "z") { event.preventDefault(); if (!event.repeat && !undoDisabled) { onCancelPaste?.(); onUndoModel?.(); } }
    else if (command && key === "g") { event.preventDefault(); if (!event.repeat && !undoDisabled) onToggleGroup?.(); }
    else if (command && key === "c") { event.preventDefault(); if (!event.repeat && selectionCount) onCopySelection?.(); }
    else if (command && key === "v") { event.preventDefault(); if (!event.repeat && !undoDisabled) onStartPaste?.(); }
    else if ((event.key === "Delete" || event.key === "Backspace") && selectionCount) { event.preventDefault(); if (!undoDisabled) { if (onRemoveSelection) onRemoveSelection(); else if (selectedMemberId) onRemoveMember(selectedMemberId); } }
    else if (command && key === "d" && selectedMemberId) { event.preventDefault(); if (!undoDisabled) onCopyMember(selectedMemberId); }
  };
  return <section className="viewport" aria-label="组件三维视图" tabIndex={0} onPointerLeave={handleViewportPointerLeave}
    onDragOver={event => { if (event.dataTransfer.types.includes("text/plain")) event.preventDefault(); }}
    onDrop={event => { if (event.dataTransfer.types.includes("text/plain")) { event.preventDefault(); if (!readOnly) onDropTemplate(); } }}>
    <div className="viewport-toolbar"><button className="tool" type="button" disabled={readOnly || undoDisabled || undoCount === 0 || isPreview} onClick={onUndoModel} title="撤销模型操作 (Ctrl+Z)，最多保留15步" aria-label="撤回上一步模型操作"><RotateCcw size={14}/>撤销（{undoCount}/15）</button>
      <button className={`tool ${!isPreview ? "active" : ""}`} type="button" onClick={onShowAssembly}><Boxes size={15}/>当前组件</button>
      {isPreview && <span className="tool active"><Eye size={15}/>模板预览</span>}
      <button className="tool" type="button" onClick={() => setCameraResetToken(token => token + 1)} title="面向世界坐标原点并恢复默认观察距离"><LocateFixed size={14}/>视角复位</button>
      <span className="viewport-help" title="左键点选/框选 · 中键旋转 · Shift+中键平移 · Alt+操作轴平移复制 · Shift加选 · Ctrl减选 · Ctrl+G组合/解组 · Ctrl+C/V复制粘贴 · Esc取消">{isPreview ? "预览不会修改当前组件" : "中键旋转 · Shift+中键平移 · Alt+操作轴平移复制"}</span>
      {!readOnly && !isPreview && selectedMember && <>
        <button className={`tool ${transformMode === "translate" ? "active" : ""}`} type="button" aria-label="移动模式" onClick={() => setTransformMode("translate")} title="移动模式"><Move3d size={14}/></button>
        <button className={`tool ${transformMode === "rotate" ? "active" : ""}`} type="button" aria-label="旋转模式" onClick={() => setTransformMode("rotate")} title="旋转模式"><Rotate3d size={14}/></button>
        <label className="viewport-planar-toggle"><input type="checkbox" checked={planarMove} onChange={event => { setPlanarMove(event.target.checked); onSetPlanarMovement?.(selectedMember.id, event.target.checked); }}/>仅 XY 平面</label>
        <button className="tool" type="button" onClick={() => onGroundMember?.(selectedMember.id)} title="将当前最低点归零">贴地</button>
        <button className="tool" type="button" onClick={() => { setTransformDraft(null); setTransformReadout(null); if (editing) editing.onTransformMembers(members.filter(member => editing.selectedMemberIds.includes(member.id)).map(member => ({ id: member.id, position: member.position, rotation: [0, 0, 0] }))); else onTransformMember(selectedMember.id, selectedMember.position, [0, 0, 0]); }} title="将构件旋转角归零，保持当前位置"><RotateCcw size={14}/>角度复位</button>
      </>}
      {!readOnly && !isPreview && selectedMemberId && <>
        <button className="tool" type="button" title="复制选中对象 (Ctrl+C)，Ctrl+V放置" aria-label="复制选中成员" onClick={() => onCopySelection ? onCopySelection() : onCopyMember(selectedMemberId)}><Copy size={14}/></button>
        <button className="tool" type="button" title="删除成员 (Delete)" aria-label="删除选中成员" onClick={() => onRemoveSelection ? onRemoveSelection() : onRemoveMember(selectedMemberId)}><Trash2 size={14}/></button>
      </>}
      {!readOnly && !isPreview && selectionCount > 0 && <button className="tool" type="button" disabled={undoDisabled} onClick={onToggleGroup} title="组合或解组 (Ctrl+G)"><Boxes size={14}/>组合/解组</button>}
      <span className="view-status">{selectionCount > 0 ? `已选 ${selectionCount} 项 · ` : ""}{members.length} 个成员 · {patterns.length ? `${patterns.length} 条构成规则` : `${mateCount} 个连接`}</span>
    </div>
    {!readOnly && !isPreview && selectedMember && <div className="viewport-transform-panel" aria-label="选中物体变换">
      <strong>{selectionCount > 1 ? `已选 ${selectionCount} 个构件（整体操作轴）` : selectedMember.name}</strong><span className="transform-origin-note">世界坐标 · mm / °</span>
      {(["position", "rotation"] as const).map(field => <div className="viewport-transform-row" key={field}>
        <span>{field === "position" ? "位置" : "旋转"}</span>{["X", "Y", "Z"].map((axis, index) => { const draft = transformDraft?.memberId === selectedMember.id && transformDraft.field === field && transformDraft.index === index ? transformDraft.value : null; return <label key={axis}>{axis}<input type="number" step="any" disabled={field === "position" && planarMove && index === 2} value={draft ?? Number((selectedMember[field][index] * (field === "rotation" ? 180 / Math.PI : 1)).toFixed(6))} onFocus={event => setTransformDraft({ memberId: selectedMember.id, field, index, value: event.currentTarget.value })} onBlur={commitTransformInput} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") { cancelTransformCommit.current = true; setTransformDraft(null); event.currentTarget.blur(); } }} onChange={event => setTransformDraft({ memberId: selectedMember.id, field, index, value: event.target.value })}/><small>{field === "position" ? "mm" : "°"}</small></label>; })}</div>)}
    </div>}
    {(isPreview || !selectedMember) && <div className="viewport-transform-placeholder">{isPreview ? "模板预览 · 选择当前组件后可编辑构件" : "选择零部件或组件，精确设置位置和旋转角度"}<span>世界坐标 · mm / °</span></div>}
    <div className="viewport-scene" ref={sceneRef}>
      <GeometryPreviewReadOnly.Provider value={readOnly}><ManagedSelection.Provider value={Boolean(editing)}><ModelStatusRegistry.Provider value={registerModelStatus}><Canvas camera={{ position: [7, 5, 7], up: [0, 0, 1], fov: 42 }} onPointerMissed={editing ? undefined : clearSelection}>
        <color attach="background" args={["#f5f7f9"]}/><ambientLight intensity={1.8}/><directionalLight position={[4, 6, 5]} intensity={2}/>
        <PreviewScene editing={editing} onRectangle={setRectangle} hoveredMemberIds={hoveredMemberIds} ghost={(offset, explicitMembers) => {
          const previewMembers = explicitMembers ? resolveComponentComposition({ assembly: { members: explicitMembers, mates: [] }, parameters, bindings, patterns }, Object.fromEntries(parameters.map(parameter => [parameter.id, parameter.value]))) : pasteMembers ?? [];
          return <group position={offset} userData={{ viewportGhost: true }}><GhostPreview.Provider value={true}>{previewMembers.map(member => member.template.templateKind === "component" ? <NestedComponentVisual key={member.id} member={member}/> : <PartMesh key={member.id} member={member} position={member.position}/>)}</GhostPreview.Provider></group>;
        }} members={isPreview ? [] : members} parameters={parameters} bindings={bindings} patterns={isPreview ? [] : patterns}
          selectedMemberId={isPreview ? null : selectedMemberId} selectedInterface={selectedInterface} selectedInterfaceResolved={selectedInterfaceResolved}
          panelSelectionToken={panelSelectionToken} compatibleMemberIds={compatibleMemberIds} onSelectMember={onSelectMember} onHoverMember={hoverHandler}
          onPointerSelect={handlePointerSelect} onPointerHover={handlePointerHover} onPointerHoverEnd={handlePointerHoverEnd}
          onScreenSelection={handleScreenSelection} onTransformMember={onTransformMember} onTransformPreview={handleTransformPreview} onDragStateChange={dragging => { transformDragging.current = dragging; if (!dragging) setTransformReadout(null); }} transformMode={transformMode} planarMove={planarMove} geometry={loaded?.geometry || null} cameraResetToken={cameraResetToken} cameraStorageKey={cameraStorageKey}/>
      </Canvas></ModelStatusRegistry.Provider></ManagedSelection.Provider></GeometryPreviewReadOnly.Provider>
      {!readOnly && rectangle && <div className={`viewport-selection-rectangle ${rectangle.crossing ? "crossing" : "window"}`} data-testid="selection-rectangle" style={{ left: rectangle.left, top: rectangle.top, width: rectangle.right - rectangle.left, height: rectangle.bottom - rectangle.top }}/>}
      {!readOnly && pasteMembers && <div className="viewport-paste-hint" role="status">移动鼠标定位副本 · 单击放置 · Esc取消</div>}
      {modelStatuses.length > 0 && <div className="member-status-layer">{modelStatuses.map(status => <div key={status.id} className="member-model-status" role={status.error ? "alert" : "status"}>
        <b>{status.name}</b><span>{status.error ? "模型暂时无法显示，请重试" : status.updating ? "正在更新模型…" : "正在加载模型…"}</span>
        {status.error && <button type="button" className="ghost" onClick={status.retry}>重试</button>}
      </div>)}</div>}
      {!isPreview && tooltip && tooltipName && <div className="viewport-tooltip" style={{ left: tooltip.point.x, top: tooltip.point.y, opacity: tooltip.opacity }}>{tooltipName}</div>}
      {!isPreview && transformReadout && <div className="transform-readout" role="status"><b>{transformReadout.mode === "rotate" ? "旋转" : "移动"}</b> {transformReadout.mode === "rotate" ? `X ${ (transformReadout.rotation[0] * 180 / Math.PI).toFixed(2) }° · Y ${ (transformReadout.rotation[1] * 180 / Math.PI).toFixed(2) }° · Z ${ (transformReadout.rotation[2] * 180 / Math.PI).toFixed(2) }°` : `X ${transformReadout.position[0].toFixed(2)} · Y ${transformReadout.position[1].toFixed(2)} · Z ${transformReadout.position[2].toFixed(2)} mm`}</div>}
    </div>
    {!loaded && (isPreview || !members.length) && <div className="empty-overlay" role="status">
      <Boxes size={30}/><b>{previewTemplate ? previewTemplate.name : "从左侧加入零部件，开始构建组件"}</b>
      <span>{previewTemplate?.templateKind === "component" ? "此组件可加入当前组件后查看" : preview.error || (isPreview ? "正在准备模型…" : "点击模板可预览，点击加号可加入组件")}</span>
      {preview.error && <><AlertTriangle size={18}/><button type="button" className="ghost" onClick={preview.retry}>重试</button></>}
    </div>}
    {isPreview && loaded && <div className="preview-caption" role="status"><b>{previewTemplate.name} · V{previewTemplate.version}</b><span>模板预览 · 点击左侧加号加入当前组件</span></div>}
    {!isPreview && members.length > 0 && <div className="preview-caption" role="status"><b>{members.find(member => member.id === selectedMemberId)?.name || "当前组件"}</b><span>点击模型或成员列表选择，拖动操作轴调整位置或角度</span></div>}
  </section>;
}

