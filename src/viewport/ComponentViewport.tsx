import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, Line, OrbitControls, TransformControls } from "@react-three/drei";
import { AlertTriangle, Boxes, Copy, Eye, Move3d, Rotate3d, Trash2 } from "lucide-react";
import { BufferAttribute, BufferGeometry, DoubleSide } from "three";
import { Vector3 as ThreeVector3 } from "three";
import type { Group, PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { ComponentMember, ComponentParameter, ComponentPattern, InterfaceSelection, ParameterBinding, PartInterface, ResolvedPartInterface, Vector3 } from "../domain/component";
import { interfaceHighlights } from "../domain/interfaceGeometry";
import { resolveComponentComposition } from "../domain/composition";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { fetchPublishedComponent } from "../library/componentPublications";
import { useTemplatePreview } from "../library/useTemplatePreview";

type VisualMember = ComponentMember & { sourceMemberId?: string; patternInstance?: boolean };
const INTERFACE_HIGHLIGHT_COLOR = "#12b8d4";
type TransformMode = "translate" | "rotate";
type TransformSnapshot = { position: Vector3; rotation: Vector3; mode: TransformMode };

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
  const transparent = isTransparentMaterial(member);
  return <meshStandardMaterial color={color} metalness={transparent ? .05 : .18} roughness={transparent ? .2 : .55} transparent={transparent} opacity={transparent ? .45 : 1} depthWrite={!transparent} side={DoubleSide}/>;
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

const WORLD_LABELS: { position: Vector3; text: string; className: string }[] = [
  { position: [0, 0, 0], text: "(0, 0, 0)", className: "world-origin-label" },
  { position: [120, 0, 0], text: "X", className: "world-axis-label axis-x" },
  { position: [0, 120, 0], text: "Y", className: "world-axis-label axis-y" },
  { position: [0, 0, 120], text: "Z", className: "world-axis-label axis-z" },
];

// Project into React-owned DOM instead of creating nested roots inside the scene.
function WorldLabelsProjector({ elements }: { elements: RefObject<(HTMLSpanElement | null)[]> }) {
  const point = useMemo(() => new ThreeVector3(), []);
  useFrame(({ camera, size }) => {
    WORLD_LABELS.forEach((label, index) => {
      const element = elements.current[index];
      if (!element) return;
      point.set(...label.position).project(camera);
      element.style.display = point.z >= -1 && point.z <= 1 ? "block" : "none";
      element.style.left = `${(point.x + 1) * size.width / 2}px`;
      element.style.top = `${(1 - point.y) * size.height / 2}px`;
    });
  });
  return null;
}

function ModelStatus({ name, preview }: { name: string; preview: ReturnType<typeof useTemplatePreview> }) {
  const id = useId();
  const register = useContext(ModelStatusRegistry);
  const retry = useRef(preview.retry);
  const updating = Boolean(preview.result);
  useEffect(() => { retry.current = preview.retry; }, [preview.retry]);
  useEffect(() => {
    if (!preview.loading && !preview.error) {
      register(id, null);
      return;
    }
    register(id, { id, name, updating, error: preview.error, retry: () => retry.current() });
    return () => register(id, null);
  }, [id, name, preview.error, preview.loading, updating, register]);
  return null;
}

function DraggableGroup({ member, selected, compatible = false, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove, movable = true, children }: { member: ComponentMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean; movable?: boolean; children: (hovered: boolean) => ReactNode }) {
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
    <group ref={captureGroup} position={localPosition} rotation={member.rotation}
      onPointerOver={event => { event.stopPropagation(); setHovered(true); onHover?.(true); onPointerHover?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); }}
      onPointerMove={event => { event.stopPropagation(); onPointerHover?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); }}
      onPointerOut={event => { event.stopPropagation(); setHovered(false); onHover?.(false); onPointerHoverEnd?.(); }}
      onClick={event => { event.stopPropagation(); onSelect(); onPointerSelect?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); }}>
      {compatible && !selected && <CompatibleOutline />}
      {children(hovered)}
    </group>
    {movable && selected && groupObject && <TransformControls object={groupObject} mode={transformMode} space="world" showX showY showZ={!planarMove || transformMode === "rotate"} size={0.9}
      onMouseDown={() => onDragStateChange?.(true)}
      onObjectChange={() => { const snapshot = readTransform(); if (snapshot) onTransformPreview(snapshot); }}
      onMouseUp={() => { const snapshot = readTransform(); if (snapshot) { onTransformPreview(snapshot); onTransformCommit(snapshot); } onDragStateChange?.(false); }} />}
  </>;
}

function partTemplate(member: ComponentMember): PublishedTemplate {
  return { templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId, version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name, createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "" };
}

function PartMesh({ member, materialColor, position = [0, 0, 0] as Vector3, onSelect, onPointerSelect }: { member: ComponentMember; materialColor?: string; position?: Vector3; onSelect?: () => void; onPointerSelect?: (point: ScreenPoint) => void }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  return <group position={position} rotation={member.rotation}><ModelStatus name={member.name} preview={preview}/>{preview.result && <mesh geometry={preview.result.geometry} dispose={null} onClick={event => { event.stopPropagation(); onSelect?.(); onPointerSelect?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); }}>
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

function NestedComponentVisual({ member, selected = false, onSelect, onPointerSelect }: { member: ComponentMember; selected?: boolean; onSelect?: () => void; onPointerSelect?: (point: ScreenPoint) => void }) {
  const [document, setDocument] = useState<import("../domain/component").ComponentDocument | null>(null);
  useEffect(() => {
    let active = true;
    void fetchPublishedComponent(member.template.publicationId).then(publication => {
      if (active && publication.templateId === member.template.templateId && publication.version === member.template.version && publication.sha256.toLowerCase() === member.template.sha256.toLowerCase()) setDocument(publication.document);
    }).catch(() => { if (active) setDocument(null); });
    return () => { active = false; };
  }, [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256]);
  const childMembers = useMemo(() => document ? resolveComponentComposition(document, member.parameterValues) : [], [document, JSON.stringify(member.parameterValues)]);
  if (!document) return null;
  return <group position={member.position} rotation={member.rotation}>
    {childMembers.map(child => child.template.templateKind === "component"
      ? <NestedComponentVisual key={child.id} member={child} selected={selected} onSelect={onSelect} onPointerSelect={onPointerSelect}/>
      : <PartMesh key={child.id} member={child} position={child.position} materialColor={selected ? "#c89532" : undefined} onSelect={onSelect} onPointerSelect={onPointerSelect}/>) }
  </group>;
}

function MemberModel({ member, selected, compatible, selectedInterface, selectedInterfaceResolved, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove }: { member: VisualMember; selected: boolean; compatible?: boolean; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  const selectedItem = selectedInterface?.memberId === (member.sourceMemberId || member.id) ? member.interfaces.find(item => item.id === selectedInterface.interfaceId) : undefined;
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onTransformPreview={onTransformPreview} onTransformCommit={onTransformCommit} onDragStateChange={onDragStateChange} onPointerSelect={onPointerSelect} onPointerHover={onPointerHover} onPointerHoverEnd={onPointerHoverEnd} transformMode={transformMode} planarMove={planarMove} movable={!member.patternInstance}>
    {hovered => {
      const color = selected ? "#c89532" : hovered ? "#d9b85f" : compatible ? "#a8cad8" : "#7394b0";
      return <><ModelStatus name={member.name} preview={preview}/>{preview.result && <><mesh geometry={preview.result.geometry} dispose={null} onClick={event => { event.stopPropagation(); onSelect(); onPointerSelect?.({ x: event.nativeEvent.clientX, y: event.nativeEvent.clientY }); }}><PartMaterial member={member} color={color}/></mesh><InterfaceMarker geometry={preview.result.geometry} item={selectedItem} member={member} resolvedInterfaces={selectedInterfaceResolved ?? preview.info?.resolvedInterfaces}/></>}</>;
    }}
  </DraggableGroup>;
}

function ComponentMemberModel({ member, selected, compatible, onSelect, onHover, onTransformPreview, onTransformCommit, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, transformMode, planarMove }: { member: VisualMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onTransformPreview: (snapshot: TransformSnapshot) => void; onTransformCommit: (snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (point: ScreenPoint) => void; onPointerHover?: (point: ScreenPoint) => void; onPointerHoverEnd?: () => void; transformMode: TransformMode; planarMove: boolean }) {
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onTransformPreview={onTransformPreview} onTransformCommit={onTransformCommit} onDragStateChange={onDragStateChange} onPointerSelect={onPointerSelect} onPointerHover={onPointerHover} onPointerHoverEnd={onPointerHoverEnd} transformMode={transformMode} planarMove={planarMove} movable={!member.patternInstance}>
    {/* The outer drag group owns the instance transform; avoid applying rotation twice. */}
    {hovered => <NestedComponentVisual member={{ ...member, position: [0, 0, 0], rotation: [0, 0, 0] }} selected={selected || hovered} onSelect={onSelect} onPointerSelect={onPointerSelect}/>}
  </DraggableGroup>;
}

function PreviewScene({ members, parameters, bindings, patterns, selectedMemberId, selectedInterface, selectedInterfaceResolved, panelSelectionToken, compatibleMemberIds, transformMode, planarMove, onSelectMember, onHoverMember, onTransformMember, onTransformPreview, onDragStateChange, onPointerSelect, onPointerHover, onPointerHoverEnd, onScreenSelection, onClearSelection, geometry }: { members: ComponentMember[]; parameters: ComponentParameter[]; patterns: ComponentPattern[]; bindings: ParameterBinding[]; selectedMemberId: string | null; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; panelSelectionToken: number; compatibleMemberIds?: ReadonlySet<string>; transformMode: TransformMode; planarMove: boolean; onSelectMember: (id: string) => void; onHoverMember?: (id: string | null) => void; onTransformMember: (id: string, position: Vector3, rotation: Vector3) => void; onTransformPreview: (id: string, snapshot: TransformSnapshot) => void; onDragStateChange?: (dragging: boolean) => void; onPointerSelect?: (id: string, point: ScreenPoint) => void; onPointerHover?: (id: string, point: ScreenPoint) => void; onPointerHoverEnd?: (id: string) => void; onScreenSelection?: (id: string, point: ScreenPoint) => void; onClearSelection?: () => void; geometry: BufferGeometry | null }) {
  const { camera, size } = useThree(); const controls = useRef<OrbitControlsImpl>(null); const [cameraEnabled, setCameraEnabled] = useState(true);
  const setDragging = (dragging: boolean) => { setCameraEnabled(!dragging); if (controls.current) controls.current.enabled = !dragging; onDragStateChange?.(dragging); };
  const updateMemberTransform = (id: string, snapshot: TransformSnapshot) => onTransformPreview(id, snapshot);
  const memberRadius = members.length ? Math.max(8, ...members.map(member => Math.hypot(...member.position) + Math.max(0, ...Object.values(member.parameterValues).filter((value): value is number => typeof value === "number" && Number.isFinite(value))) / 2)) : 8;
  const patternRadius = patterns.length ? Math.max(...patterns.map(pattern => (parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0) * Math.max(1, (parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1) - 1))) : 0;
  const radius = members.length
    ? Math.max(memberRadius, patternRadius)
    : geometry?.boundingSphere?.radius || Math.max(memberRadius, patternRadius);
  const cameraRadius = Math.max(8, radius * 1.1);
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
    controls.current?.target.set(...member.position);
    controls.current?.update();
    screenPosition.current.set(...member.position).project(camera);
    const width = size.width, height = size.height;
    const point = { x: (screenPosition.current.x + 1) / 2 * width, y: (-screenPosition.current.y + 1) / 2 * height };
    onScreenSelection(selectedMemberId, point);
  }, [camera, onScreenSelection, panelSelectionToken, renderedMembers, selectedMemberId, size.height, size.width]);
  useEffect(() => { const viewCamera = camera as PerspectiveCamera; const vertical = viewCamera.fov * Math.PI / 180; const distance = cameraRadius / Math.sin(vertical / 2) * 1.8; viewCamera.position.set(distance, -distance, distance); viewCamera.up.set(0, 0, 1); viewCamera.near = .001; viewCamera.far = distance + cameraRadius * 100; viewCamera.updateProjectionMatrix(); controls.current?.target.set(0, 0, 0); controls.current?.update(); }, [camera, size.width, size.height, cameraRadius]);
  return <><Grid args={[10000, 10000]} rotation={[Math.PI / 2, 0, 0]} cellSize={100} cellThickness={.5} sectionSize={1000} sectionThickness={1} fadeDistance={100000} cellColor="#a8b2ba" sectionColor="#7c929f" side={DoubleSide}/><axesHelper args={[100]}/>{geometry && !renderedMembers.length && <mesh><primitive object={geometry} attach="geometry" dispose={null}/><meshStandardMaterial color="#7394b0" metalness={.18} roughness={.55} side={DoubleSide}/></mesh>}{renderedMembers.map((member, index) => {
    const memberId = member.sourceMemberId || member.id;
    const compatible = compatibleMemberIds?.has(memberId) || false;
    return member.template.templateKind === "component"
      ? <ComponentMemberModel key={`${member.id}-${index}`} member={member} selected={memberId === selectedMemberId} compatible={compatible} onSelect={() => onSelectMember(memberId)} onPointerSelect={point => onPointerSelect?.(memberId, point)} onPointerHover={point => onPointerHover?.(memberId, point)} onPointerHoverEnd={() => onPointerHoverEnd?.(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onTransformPreview={snapshot => updateMemberTransform(memberId, snapshot)} onTransformCommit={snapshot => onTransformMember(memberId, snapshot.position, snapshot.rotation)} onDragStateChange={setDragging} transformMode={transformMode} planarMove={planarMove}/>
      : <MemberModel key={`${member.id}-${index}`} member={member} selected={memberId === selectedMemberId} compatible={compatible} selectedInterface={selectedInterface} selectedInterfaceResolved={selectedInterfaceResolved} onSelect={() => onSelectMember(memberId)} onPointerSelect={point => onPointerSelect?.(memberId, point)} onPointerHover={point => onPointerHover?.(memberId, point)} onPointerHoverEnd={() => onPointerHoverEnd?.(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onTransformPreview={snapshot => updateMemberTransform(memberId, snapshot)} onTransformCommit={snapshot => onTransformMember(memberId, snapshot.position, snapshot.rotation)} onDragStateChange={setDragging} transformMode={transformMode} planarMove={planarMove}/>;
  })}<OrbitControls ref={controls} enabled={cameraEnabled} makeDefault minDistance={radius / 100} maxDistance={radius * 100}/></>;
}

type Props = { members: ComponentMember[]; parameters?: ComponentParameter[]; bindings?: ParameterBinding[]; patterns?: ComponentPattern[]; mateCount: number; previewTemplate: PublishedTemplate | null; onShowAssembly?: () => void; selectedMemberId?: string | null; selectedInterface?: InterfaceSelection | null; selectedInterfaceResolved?: ResolvedPartInterface[]; panelSelectionToken?: number; compatibleMemberIds?: ReadonlySet<string>; onSelectMember?: (id: string | null) => void; onHoverMember?: (id: string | null) => void; onTransformMember?: (id: string, position: Vector3, rotation: Vector3) => void; onSetPlanarMovement?: (id: string, enabled: boolean) => void; onGroundMember?: (id: string) => void; onCopyMember?: (id: string) => void; onRemoveMember?: (id: string) => void; onDropTemplate?: () => void };
export function ComponentViewport({ members, parameters = [], bindings = [], patterns = [], mateCount, previewTemplate, onShowAssembly, selectedMemberId = null, selectedInterface = null, selectedInterfaceResolved, panelSelectionToken = 0, compatibleMemberIds, onSelectMember = () => undefined, onHoverMember, onTransformMember = () => undefined, onSetPlanarMovement, onGroundMember, onCopyMember = () => undefined, onRemoveMember = () => undefined, onDropTemplate = () => undefined }: Props) {
  const isPreview = previewTemplate !== null;
  const preview = useTemplatePreview(previewTemplate?.templateKind === "component" ? null : previewTemplate);
  const loaded = preview.result;
  const hoverHandler = onHoverMember || ((id: string | null) => { if (id) onSelectMember(id); });
  const [tooltip, setTooltip] = useState<MemberTooltip | null>(null);
  const [transformMode, setTransformMode] = useState<TransformMode>("translate");
  const [planarMove, setPlanarMove] = useState(false);
  const [transformReadout, setTransformReadout] = useState<TransformSnapshot | null>(null);
  const [transformDraft, setTransformDraft] = useState<{ memberId: string; field: "position" | "rotation"; index: number; value: string } | null>(null);
  const [modelStatuses, setModelStatuses] = useState<ModelStatusEntry[]>([]);
  const cancelTransformCommit = useRef(false);
  const tooltipRef = useRef<typeof tooltip>(null);
  const sceneRef = useRef<HTMLDivElement | null>(null);
  const worldLabelElements = useRef<(HTMLSpanElement | null)[]>([]);
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
  useEffect(() => () => clearTooltipTimers(), []);
  const handlePointerSelect = (id: string, point: ScreenPoint) => {
    const rect = sceneRef.current?.getBoundingClientRect();
    onSelectMember(id);
    showTimedTooltip(id, { x: point.x - (rect?.left || 0), y: point.y - (rect?.top || 0) }, "pointer");
  };
  const handlePointerHover = (id: string, point: ScreenPoint) => {
    if (selectedMemberId === id) return;
    if (tooltipRef.current?.memberId === id && tooltipRef.current.source !== "hover") return;
    const rect = sceneRef.current?.getBoundingClientRect();
    const localPoint = { x: point.x - (rect?.left || 0), y: point.y - (rect?.top || 0) };
    showHoverTooltip(id, localPoint);
  };
  const handlePointerHoverEnd = (id: string) => { if (selectedMemberId !== id) clearHoverTooltip(id); };
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
    if (!selectedMember || !Number.isFinite(value)) return;
    const vector: Vector3 = [...selectedMember[field]];
    vector[index] = field === "rotation" ? value * Math.PI / 180 : value;
    onTransformMember(selectedMember.id, field === "position" ? vector : selectedMember.position, field === "rotation" ? vector : selectedMember.rotation);
  };
  const commitTransformInput = () => {
    if (cancelTransformCommit.current) { cancelTransformCommit.current = false; setTransformDraft(null); return; }
    if (!transformDraft || !selectedMember || transformDraft.memberId !== selectedMember.id) { setTransformDraft(null); return; }
    const value = Number(transformDraft.value);
    if (transformDraft.value.trim() && Number.isFinite(value)) updateSelectedTransform(transformDraft.field, transformDraft.index, value);
    setTransformDraft(null);
  };
  const clearSelection = () => { clearTooltipTimers(); tooltipRef.current = null; setTooltip(null); onHoverMember?.(null); onSelectMember(null); };
  const handleViewportPointerLeave = () => { hoverHandler(null); clearHoverTooltip(); };
  const tooltipMember = tooltip ? members.find(member => member.id === tooltip.memberId) : undefined;
  return <section className="viewport" aria-label="组件三维视图" tabIndex={0} onPointerLeave={handleViewportPointerLeave}
    onDragOver={event => { if (event.dataTransfer.types.includes("text/plain")) event.preventDefault(); }}
    onDrop={event => { if (event.dataTransfer.types.includes("text/plain")) { event.preventDefault(); onDropTemplate(); } }}
    onKeyDown={event => {
      if (isPreview || !selectedMemberId || (event.target instanceof HTMLElement && event.target.closest("input,textarea,select,button"))) return;
      if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); onRemoveMember(selectedMemberId); }
      else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") { event.preventDefault(); onCopyMember(selectedMemberId); }
    }}>
    <div className="viewport-toolbar">
      <button className={`tool ${!isPreview ? "active" : ""}`} type="button" onClick={onShowAssembly}><Boxes size={15}/>当前组件</button>
      {isPreview && <span className="tool active"><Eye size={15}/>模板预览</span>}
      <span className="viewport-help">{isPreview ? "预览不会修改当前组件" : "选择成员后使用操作轴移动或旋转 · 滚轮缩放"}</span>
      {!isPreview && selectedMember && <>
        <button className={`tool ${transformMode === "translate" ? "active" : ""}`} type="button" aria-label="移动模式" onClick={() => setTransformMode("translate")} title="移动模式"><Move3d size={14}/></button>
        <button className={`tool ${transformMode === "rotate" ? "active" : ""}`} type="button" aria-label="旋转模式" onClick={() => setTransformMode("rotate")} title="旋转模式"><Rotate3d size={14}/></button>
        <label className="viewport-planar-toggle"><input type="checkbox" checked={planarMove} onChange={event => { setPlanarMove(event.target.checked); onSetPlanarMovement?.(selectedMember.id, event.target.checked); }}/>仅 XY 平面</label>
        <button className="tool" type="button" onClick={() => onGroundMember?.(selectedMember.id)} title="将当前最低点归零">贴地</button>
      </>}
      {!isPreview && selectedMemberId && <>
        <button className="tool" type="button" title="复制成员 (Ctrl/Cmd+D)" aria-label="复制选中成员" onClick={() => onCopyMember(selectedMemberId)}><Copy size={14}/></button>
        <button className="tool" type="button" title="删除成员 (Delete)" aria-label="删除选中成员" onClick={() => onRemoveMember(selectedMemberId)}><Trash2 size={14}/></button>
      </>}
      <span className="view-status">{members.length} 个成员 · {patterns.length ? `${patterns.length} 条构成规则` : `${mateCount} 个连接`}</span>
    </div>
    {!isPreview && selectedMember && <div className="viewport-transform-panel" aria-label="选中物体变换">
      <strong>{selectedMember.name}</strong><span className="transform-origin-note">世界坐标 · mm / °</span>
      {(["position", "rotation"] as const).map(field => <div className="viewport-transform-row" key={field}>
        <span>{field === "position" ? "位置" : "旋转"}</span>{["X", "Y", "Z"].map((axis, index) => { const draft = transformDraft?.memberId === selectedMember.id && transformDraft.field === field && transformDraft.index === index ? transformDraft.value : null; return <label key={axis}>{axis}<input type="number" step="any" disabled={field === "position" && planarMove && index === 2} value={draft ?? Number((selectedMember[field][index] * (field === "rotation" ? 180 / Math.PI : 1)).toFixed(6))} onFocus={event => setTransformDraft({ memberId: selectedMember.id, field, index, value: event.currentTarget.value })} onBlur={commitTransformInput} onKeyDown={event => { if (event.key === "Enter") event.currentTarget.blur(); else if (event.key === "Escape") { cancelTransformCommit.current = true; setTransformDraft(null); event.currentTarget.blur(); } }} onChange={event => setTransformDraft({ memberId: selectedMember.id, field, index, value: event.target.value })}/><small>{field === "position" ? "mm" : "°"}</small></label>; })}</div>)}
    </div>}
    <div className="viewport-scene" ref={sceneRef} style={{ top: selectedMember && !isPreview ? 132 : 42 }}>
      <ModelStatusRegistry.Provider value={registerModelStatus}><Canvas camera={{ position: [7, 5, 7], up: [0, 0, 1], fov: 42 }} onPointerMissed={clearSelection}>
        <WorldLabelsProjector elements={worldLabelElements}/>
        <color attach="background" args={["#f5f7f9"]}/><ambientLight intensity={1.8}/><directionalLight position={[4, 6, 5]} intensity={2}/>
        <PreviewScene members={isPreview ? [] : members} parameters={parameters} bindings={bindings} patterns={isPreview ? [] : patterns}
          selectedMemberId={isPreview ? null : selectedMemberId} selectedInterface={selectedInterface} selectedInterfaceResolved={selectedInterfaceResolved}
          panelSelectionToken={panelSelectionToken} compatibleMemberIds={compatibleMemberIds} onSelectMember={onSelectMember} onHoverMember={hoverHandler}
          onPointerSelect={handlePointerSelect} onPointerHover={handlePointerHover} onPointerHoverEnd={handlePointerHoverEnd}
          onScreenSelection={handleScreenSelection} onClearSelection={clearSelection} onTransformMember={onTransformMember} onTransformPreview={handleTransformPreview} onDragStateChange={dragging => { if (!dragging) setTransformReadout(null); }} transformMode={transformMode} planarMove={planarMove} geometry={loaded?.geometry || null}/>
      </Canvas></ModelStatusRegistry.Provider>
      <div className="world-label-layer">{WORLD_LABELS.map((label, index) => <span key={label.text} ref={element => { worldLabelElements.current[index] = element; }} className={label.className}>{label.text}</span>)}</div>
      {modelStatuses.length > 0 && <div className="member-status-layer">{modelStatuses.map(status => <div key={status.id} className="member-model-status" role={status.error ? "alert" : "status"}>
        <b>{status.name}</b><span>{status.error ? "模型暂时无法显示，请重试" : status.updating ? "正在更新模型…" : "正在加载模型…"}</span>
        {status.error && <button type="button" className="ghost" onClick={status.retry}>重试</button>}
      </div>)}</div>}
      {!isPreview && tooltip && tooltipMember && <div className="viewport-tooltip" style={{ left: tooltip.point.x, top: tooltip.point.y, opacity: tooltip.opacity }}>{tooltipMember.name}</div>}
      {!isPreview && transformReadout && <div className="transform-readout" role="status"><b>{transformReadout.mode === "rotate" ? "旋转" : "移动"}</b> {transformReadout.mode === "rotate" ? `X ${ (transformReadout.rotation[0] * 180 / Math.PI).toFixed(2) }° · Y ${ (transformReadout.rotation[1] * 180 / Math.PI).toFixed(2) }° · Z ${ (transformReadout.rotation[2] * 180 / Math.PI).toFixed(2) }°` : `X ${transformReadout.position[0].toFixed(2)} · Y ${transformReadout.position[1].toFixed(2)} · Z ${transformReadout.position[2].toFixed(2)} mm`}</div>}
    </div>
    {!loaded && (isPreview || !members.length) && <div className="empty-overlay" style={{ top: 42 }} role="status">
      <Boxes size={30}/><b>{previewTemplate ? previewTemplate.name : "从左侧加入零部件，开始构建组件"}</b>
      <span>{previewTemplate?.templateKind === "component" ? "此组件可加入当前组件后查看" : preview.error || (isPreview ? "正在准备模型…" : "点击模板可预览，点击加号可加入组件")}</span>
      {preview.error && <><AlertTriangle size={18}/><button type="button" className="ghost" onClick={preview.retry}>重试</button></>}
    </div>}
    {isPreview && loaded && <div className="preview-caption" role="status"><b>{previewTemplate.name} · V{previewTemplate.version}</b><span>模板预览 · 点击左侧加号加入当前组件</span></div>}
    {!isPreview && members.length > 0 && <div className="preview-caption" role="status"><b>{members.find(member => member.id === selectedMemberId)?.name || "当前组件"}</b><span>点击模型或成员列表选择，拖动操作轴调整位置或角度</span></div>}
  </section>;
}
