import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Grid, Html, Line, OrbitControls, TransformControls } from "@react-three/drei";
import { AlertTriangle, Boxes, Copy, Eye, Trash2 } from "lucide-react";
import { DoubleSide } from "three";
import type { BufferGeometry, Group, PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { ComponentMember, ComponentParameter, ComponentPattern, ParameterBinding, Vector3 } from "../domain/component";
import { resolveComponentComposition } from "../domain/composition";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { fetchPublishedComponent } from "../library/componentPublications";
import { useTemplatePreview } from "../library/useTemplatePreview";

type VisualMember = ComponentMember & { sourceMemberId?: string; patternInstance?: boolean };

// Published part geometry is stored in engineering units. Keep the grid and all
// persisted member coordinates unchanged, but enlarge the rendered model so it
// remains easy to inspect and pick in the viewport.
const MODEL_DISPLAY_SCALE = 2.4;

function CompatibleOutline() {
  const x = 100, y = 50, z = 40;
  const points: [number, number, number][] = [
    [-x, -y, -z], [x, -y, -z], [x, -y, -z], [x, y, -z], [x, y, -z], [-x, y, -z], [-x, y, -z], [-x, -y, -z],
    [-x, -y, z], [x, -y, z], [x, -y, z], [x, y, z], [x, y, z], [-x, y, z], [-x, y, z], [-x, -y, z],
    [-x, -y, -z], [-x, -y, z], [x, -y, -z], [x, -y, z], [x, y, -z], [x, y, z], [-x, y, -z], [-x, y, z],
  ];
  return <Line points={points} segments dashed color="#5c9ebc" lineWidth={1.5} scale={MODEL_DISPLAY_SCALE} />;
}

function DraggableGroup({ member, selected, compatible = false, onSelect, onHover, onMove, onDragStateChange, movable = true, children }: { member: ComponentMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onMove: (position: Vector3) => void; onDragStateChange?: (dragging: boolean) => void; movable?: boolean; children: (hovered: boolean) => ReactNode }) {
  const groupRef = useRef<Group | null>(null); const [groupObject, setGroupObject] = useState<Group | null>(null); const positionRef = useRef<Vector3>(member.position); const [localPosition, setLocalPosition] = useState<Vector3>(member.position); const [hovered, setHovered] = useState(false);
  const captureGroup = useCallback((group: Group | null) => { groupRef.current = group; setGroupObject(group); }, []);
  useEffect(() => { positionRef.current = member.position; setLocalPosition(member.position); }, [member.position]);
  return <>
    <group ref={captureGroup} position={localPosition} rotation={member.rotation}
      onPointerOver={event => { event.stopPropagation(); setHovered(true); onHover?.(true); }}
      onPointerOut={event => { event.stopPropagation(); setHovered(false); onHover?.(false); }}
      onClick={event => { event.stopPropagation(); onSelect(); }}>
      {compatible && !selected && <CompatibleOutline />}
      {children(hovered)}
    </group>
    {movable && selected && groupObject && <TransformControls object={groupObject} mode="translate" space="world" showX showY showZ size={0.9}
      onMouseDown={() => onDragStateChange?.(true)}
      onObjectChange={() => { const object = groupRef.current; if (!object) return; positionRef.current = [object.position.x, object.position.y, object.position.z]; }}
      onMouseUp={() => { onMove(positionRef.current); onDragStateChange?.(false); }} />}
    {(selected || hovered) && <Html position={[0, 0, 0]} center distanceFactor={Math.max(10, Math.abs(member.position[2]) / 20)} className="model-label" style={{ pointerEvents: "none" }}>{member.name}</Html>}
  </>;
}

function partTemplate(member: ComponentMember): PublishedTemplate {
  return { templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId, version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name, createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "" };
}

function PartMesh({ member, materialColor, position = [0, 0, 0] as Vector3 }: { member: ComponentMember; materialColor?: string; position?: Vector3 }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  if (!preview.result?.geometry) return null;
  return <mesh geometry={preview.result.geometry} position={position} rotation={member.rotation} scale={MODEL_DISPLAY_SCALE} dispose={null}>
    <meshStandardMaterial color={materialColor || "#7394b0"} metalness={.18} roughness={.55} side={DoubleSide}/>
  </mesh>;
}

function NestedComponentVisual({ member, selected = false }: { member: ComponentMember; selected?: boolean }) {
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
      ? <NestedComponentVisual key={child.id} member={child} selected={selected}/>
      : <PartMesh key={child.id} member={child} position={child.position} materialColor={selected ? "#c89532" : undefined}/>) }
  </group>;
}

function MemberModel({ member, selected, compatible, onSelect, onHover, onMove, onDragStateChange }: { member: VisualMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onMove: (position: Vector3) => void; onDragStateChange?: (dragging: boolean) => void }) {
  const template = useMemo(() => partTemplate(member), [member.template.publicationId, member.template.templateId, member.template.version, member.template.sha256, member.code, member.name]);
  const preview = useTemplatePreview(template, member.parameterValues);
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onMove={onMove} onDragStateChange={onDragStateChange} movable={!member.patternInstance}>
    {hovered => {
      const color = selected ? "#c89532" : hovered ? "#d9b85f" : compatible ? "#a8cad8" : "#7394b0";
      return preview.result?.geometry
        ? <mesh geometry={preview.result.geometry} scale={MODEL_DISPLAY_SCALE} dispose={null}><meshStandardMaterial color={color} metalness={.18} roughness={.55} side={DoubleSide}/></mesh>
        : <mesh scale={MODEL_DISPLAY_SCALE}><boxGeometry args={[160, 80, 60]}/><meshBasicMaterial color={color} wireframe transparent opacity={.75}/></mesh>;
    }}
  </DraggableGroup>;
}

function ComponentMemberModel({ member, selected, compatible, onSelect, onHover, onMove, onDragStateChange }: { member: VisualMember; selected: boolean; compatible?: boolean; onSelect: () => void; onHover?: (hovered: boolean) => void; onMove: (position: Vector3) => void; onDragStateChange?: (dragging: boolean) => void }) {
  return <DraggableGroup member={member} selected={selected} compatible={compatible} onSelect={onSelect} onHover={onHover} onMove={onMove} onDragStateChange={onDragStateChange} movable={!member.patternInstance}>
    {/* The outer drag group owns the instance transform; avoid applying rotation twice. */}
    {hovered => <NestedComponentVisual member={{ ...member, position: [0, 0, 0], rotation: [0, 0, 0] }} selected={selected || hovered}/>}
  </DraggableGroup>;
}

function PreviewScene({ members, parameters, bindings, patterns, selectedMemberId, compatibleMemberIds, onSelectMember, onHoverMember, onMoveMember, geometry }: { members: ComponentMember[]; parameters: ComponentParameter[]; bindings: ParameterBinding[]; patterns: ComponentPattern[]; selectedMemberId: string | null; compatibleMemberIds?: ReadonlySet<string>; onSelectMember: (id: string) => void; onHoverMember?: (id: string | null) => void; onMoveMember: (id: string, position: Vector3) => void; geometry: BufferGeometry | null }) {
  const { camera, size } = useThree(); const controls = useRef<OrbitControlsImpl>(null); const [cameraEnabled, setCameraEnabled] = useState(true);
  const setDragging = (dragging: boolean) => { setCameraEnabled(!dragging); if (controls.current) controls.current.enabled = !dragging; };
  const memberRadius = members.length ? Math.max(8, ...members.flatMap(member => [...member.position.map(Math.abs), ...Object.values(member.parameterValues).filter((value): value is number => typeof value === "number" && Number.isFinite(value)).map(Math.abs)])) : 8;
  const patternRadius = patterns.length ? Math.max(...patterns.map(pattern => (parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0) * Math.max(1, (parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1) - 1))) : 0;
  const radius = geometry?.boundingSphere?.radius || Math.max(memberRadius, patternRadius);
  // Member previews already apply MODEL_DISPLAY_SCALE. Keep the assembly
  // camera in engineering units so the enlarged meshes occupy more of the
  // viewport instead of being cancelled out by an equally enlarged camera
  // distance. A standalone preview still needs the scale factor to fit.
  const cameraRadius = Math.max(8, radius * (members.length ? 1.1 : MODEL_DISPLAY_SCALE * 1.1));
  const renderedMembers = useMemo<VisualMember[]>(() => resolveComponentComposition({ assembly: { members, mates: [] }, parameters, bindings, patterns }, Object.fromEntries(parameters.map(parameter => [parameter.id, parameter.value]))), [members, parameters, bindings, patterns]);
  useEffect(() => { const viewCamera = camera as PerspectiveCamera; const vertical = viewCamera.fov * Math.PI / 180; const distance = cameraRadius / Math.sin(vertical / 2) * 1.8; viewCamera.position.set(distance, -distance, distance); viewCamera.up.set(0, 0, 1); viewCamera.near = .001; viewCamera.far = distance + cameraRadius * 100; viewCamera.updateProjectionMatrix(); controls.current?.target.set(0, 0, 0); controls.current?.update(); }, [camera, size.width, size.height, cameraRadius]);
  return <><Grid args={[10000, 10000]} rotation={[Math.PI / 2, 0, 0]} cellSize={100} cellThickness={.5} sectionSize={1000} sectionThickness={1} fadeDistance={10000} cellColor="#c0c8d0" sectionColor="#8da6be" side={DoubleSide}/>{geometry && !renderedMembers.length && <mesh><primitive object={geometry} attach="geometry" dispose={null}/><meshStandardMaterial color="#7394b0" metalness={.18} roughness={.55} side={DoubleSide}/></mesh>}{renderedMembers.map((member, index) => {
    const memberId = member.sourceMemberId || member.id;
    const compatible = compatibleMemberIds?.has(memberId) || false;
    return member.template.templateKind === "component"
      ? <ComponentMemberModel key={`${member.id}-${index}`} member={member} selected={memberId === selectedMemberId} compatible={compatible} onSelect={() => onSelectMember(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onMove={position => onMoveMember(memberId, position)} onDragStateChange={setDragging}/>
      : <MemberModel key={`${member.id}-${index}`} member={member} selected={memberId === selectedMemberId} compatible={compatible} onSelect={() => onSelectMember(memberId)} onHover={hovered => onHoverMember?.(hovered ? memberId : null)} onMove={position => onMoveMember(memberId, position)} onDragStateChange={setDragging}/>;
  })}<OrbitControls ref={controls} enabled={cameraEnabled} makeDefault minDistance={radius / 100} maxDistance={radius * 100}/></>;
}

type Props = { members: ComponentMember[]; parameters?: ComponentParameter[]; bindings?: ParameterBinding[]; patterns?: ComponentPattern[]; mateCount: number; previewTemplate: PublishedTemplate | null; selectedMemberId?: string | null; compatibleMemberIds?: ReadonlySet<string>; onSelectMember?: (id: string) => void; onHoverMember?: (id: string | null) => void; onMoveMember?: (id: string, position: Vector3) => void; onCopyMember?: (id: string) => void; onRemoveMember?: (id: string) => void; onDropTemplate?: () => void };
export function ComponentViewport({ members, parameters = [], bindings = [], patterns = [], mateCount, previewTemplate, selectedMemberId = null, compatibleMemberIds, onSelectMember = () => undefined, onHoverMember, onMoveMember = () => undefined, onCopyMember = () => undefined, onRemoveMember = () => undefined, onDropTemplate = () => undefined }: Props) {
  const preview = useTemplatePreview(members.length || previewTemplate?.templateKind === "component" ? null : previewTemplate); const loaded = preview.result;
  const hoverHandler = onHoverMember || ((id: string | null) => { if (id) onSelectMember(id); });
  return <section className="viewport" aria-label="组件三维视图" tabIndex={0} onPointerLeave={() => hoverHandler(null)} onDragOver={event => { if (event.dataTransfer.types.includes("text/plain")) event.preventDefault(); }} onDrop={event => { if (event.dataTransfer.types.includes("text/plain")) { event.preventDefault(); onDropTemplate(); } }} onKeyDown={event => { if (!selectedMemberId) return; if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); onRemoveMember(selectedMemberId); } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") { event.preventDefault(); onCopyMember(selectedMemberId); } }}><div className="viewport-toolbar"><button className="tool active" type="button"><Eye size={15}/>预览</button><span>从左侧拖入零部件 · 选择后使用操作轴移动 · 滚轮缩放</span>{selectedMemberId && <><button className="tool" type="button" title="复制成员 (Ctrl/Cmd+D)" aria-label="复制选中成员" onClick={() => onCopyMember(selectedMemberId)}><Copy size={14}/></button><button className="tool" type="button" title="删除选中成员 (Delete)" aria-label="删除选中成员" onClick={() => onRemoveMember(selectedMemberId)}><Trash2 size={14}/></button></>}<span className="view-status">{members.length} 个成员 · {patterns.length ? `${patterns.length} 条构成规则` : `${mateCount} 个连接`}</span></div><div className="viewport-scene"><Canvas camera={{ position: [7, 5, 7], up: [0, 0, 1], fov: 42 }}><color attach="background" args={["#f5f7f9"]}/><ambientLight intensity={1.8}/><directionalLight position={[4, 6, 5]} intensity={2}/><PreviewScene members={members} parameters={parameters} bindings={bindings} patterns={patterns} selectedMemberId={selectedMemberId} compatibleMemberIds={compatibleMemberIds} onSelectMember={onSelectMember} onHoverMember={hoverHandler} onMoveMember={onMoveMember} geometry={loaded?.geometry || null}/></Canvas></div>{!loaded && !members.length && <div className="empty-overlay" role="status"><Boxes size={30}/><b>{previewTemplate ? previewTemplate.name : "浏览已发布零部件"}</b><span>{preview.error || (previewTemplate ? "正在准备模型…" : "点击左侧模板预览零件")}</span>{preview.error && <><AlertTriangle size={18}/><button type="button" className="ghost" onClick={preview.retry}>重试</button></>}</div>}{members.length > 0 && <div className="preview-caption" role="status"><b>组件成员</b><span>选中成员后拖动操作轴可调整位置</span></div>}</section>;
}
