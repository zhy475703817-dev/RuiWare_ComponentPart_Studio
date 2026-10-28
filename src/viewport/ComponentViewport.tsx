import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { AlertTriangle, Boxes, Copy, Eye, Trash2 } from "lucide-react";
import { DoubleSide, Vector3 as ThreeVector3 } from "three";
import type { BufferGeometry, PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { ComponentMember, ComponentParameter, ComponentPattern, Vector3 } from "../domain/component";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { fetchPublishedComponent } from "../library/componentPublications";
import { useTemplatePreview } from "../library/useTemplatePreview";

type VisualMember = ComponentMember & { sourceMemberId?: string; patternInstance?: boolean };

function DraggableGroup({ member, selected, onSelect, onMove, movable = true, children }: { member: ComponentMember; selected: boolean; onSelect: () => void; onMove: (position: Vector3) => void; movable?: boolean; children: ReactNode }) {
  const dragging = useRef(false); const start = useRef(new ThreeVector3()); const origin = useRef(member.position); const positionRef = useRef<Vector3>(member.position); const [localPosition, setLocalPosition] = useState<Vector3>(member.position);
  useEffect(() => { origin.current = member.position; positionRef.current = member.position; setLocalPosition(member.position); }, [member.position]);
  return <group position={localPosition} rotation={member.rotation}
    onPointerDown={event => { event.stopPropagation(); onSelect(); if (!movable) return; dragging.current = true; start.current.copy(event.point); origin.current = member.position; }}
    onPointerMove={event => { if (!dragging.current) return; event.stopPropagation(); const delta = event.point.clone().sub(start.current); const next: Vector3 = [origin.current[0] + delta.x, origin.current[1] + delta.y, origin.current[2]]; positionRef.current = next; setLocalPosition(next); }}
    onPointerUp={event => { event.stopPropagation(); if (dragging.current) onMove(positionRef.current); dragging.current = false; }}
    onPointerMissed={() => { dragging.current = false; }}>
    {children}
  </group>;
}

function partTemplate(member: ComponentMember): PublishedTemplate {
  return { templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId, version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name, createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "" };
}

function PartMesh({ member, materialColor, position = [0, 0, 0] as Vector3 }: { member: ComponentMember; materialColor?: string; position?: Vector3 }) {
  const preview = useTemplatePreview(partTemplate(member), member.parameterValues);
  if (!preview.result?.geometry) return null;
  return <mesh geometry={preview.result.geometry} position={position} rotation={member.rotation}>
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
  const childMembers = useMemo(() => {
    if (!document) return [];
    const result = structuredClone(document.assembly.members);
    const resolvedParameters = document.parameters.map(parameter => ({ ...parameter, value: typeof member.parameterValues[parameter.id] === "number" ? member.parameterValues[parameter.id] as number : parameter.value }));
    for (const binding of document.bindings) {
      const value = member.parameterValues[binding.parameterId];
      const child = result.find(item => item.id === binding.memberId);
      const definition = child?.parameterDefinitions?.find(item => item.id === binding.memberParameterId && item.unit === document.parameters.find(parameter => parameter.id === binding.parameterId)?.unit);
      if (!child || !definition || typeof value !== "number") continue;
      const minimum = Math.max(document.parameters.find(parameter => parameter.id === binding.parameterId)?.min ?? -Infinity, definition.minimum ?? -Infinity);
      const maximum = Math.min(document.parameters.find(parameter => parameter.id === binding.parameterId)?.max ?? Infinity, definition.maximum ?? Infinity);
      child.parameterValues[binding.memberParameterId] = Math.min(maximum, Math.max(minimum, value));
    }
    const used = new Set(document.patterns.flatMap(pattern => pattern.memberIds));
    const rendered = result.filter(item => !used.has(item.id));
    for (const pattern of document.patterns) {
      const count = Math.max(1, Math.round(resolvedParameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1));
      const spacing = resolvedParameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0;
      for (const child of result.filter(item => pattern.memberIds.includes(item.id))) for (let index = 0; index < count; index += 1) {
        const position: Vector3 = [...child.position];
        if (pattern.axis === "x") position[0] += spacing * index;
        if (pattern.axis === "y") position[1] += spacing * index;
        if (pattern.axis === "z") position[2] += spacing * index;
        rendered.push({ ...child, position });
      }
    }
    return rendered;
  }, [document, JSON.stringify(member.parameterValues)]);
  if (!document) return null;
  return <group position={member.position} rotation={member.rotation}>
    {childMembers.map(child => child.template.templateKind === "component"
      ? <NestedComponentVisual key={child.id} member={child} selected={selected}/>
      : <PartMesh key={child.id} member={child} position={child.position} materialColor={selected ? "#c89532" : undefined}/>) }
  </group>;
}

function MemberModel({ member, selected, onSelect, onMove }: { member: VisualMember; selected: boolean; onSelect: () => void; onMove: (position: Vector3) => void }) {
  const preview = useTemplatePreview(partTemplate(member), member.parameterValues);
  if (!preview.result?.geometry) return null;
  return <DraggableGroup member={member} selected={selected} onSelect={onSelect} onMove={onMove} movable={!member.patternInstance}>
    <mesh geometry={preview.result.geometry}>
      <meshStandardMaterial color={selected ? "#c89532" : "#7394b0"} metalness={.18} roughness={.55} side={DoubleSide}/>
    </mesh>
  </DraggableGroup>;
}

function ComponentMemberModel({ member, selected, onSelect, onMove }: { member: VisualMember; selected: boolean; onSelect: () => void; onMove: (position: Vector3) => void }) {
  return <DraggableGroup member={member} selected={selected} onSelect={onSelect} onMove={onMove} movable={!member.patternInstance}>
    <NestedComponentVisual member={{ ...member, position: [0, 0, 0] }} selected={selected}/>
  </DraggableGroup>;
}

function PreviewScene({ members, parameters, patterns, selectedMemberId, onSelectMember, onMoveMember, geometry }: { members: ComponentMember[]; parameters: ComponentParameter[]; patterns: ComponentPattern[]; selectedMemberId: string | null; onSelectMember: (id: string) => void; onMoveMember: (id: string, position: Vector3) => void; geometry: BufferGeometry | null }) {
  const { camera, size } = useThree(); const controls = useRef<OrbitControlsImpl>(null);
  const memberRadius = members.length ? Math.max(8, ...members.flatMap(member => [...member.position.map(Math.abs), ...Object.values(member.parameterValues).filter((value): value is number => typeof value === "number" && Number.isFinite(value)).map(Math.abs)])) : 8;
  const patternRadius = patterns.length ? Math.max(...patterns.map(pattern => (parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0) * Math.max(1, (parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1) - 1))) : 0;
  const radius = geometry?.boundingSphere?.radius || Math.max(memberRadius, patternRadius);
  const renderedMembers = useMemo<VisualMember[]>(() => {
    if (!patterns.length) return members;
    const used = new Set(patterns.flatMap(pattern => pattern.memberIds));
    const result: VisualMember[] = members.filter(member => !used.has(member.id));
    for (const pattern of patterns) {
      const count = Math.max(1, Math.round(parameters.find(parameter => parameter.id === pattern.countParameterId)?.value ?? 1));
      const spacing = parameters.find(parameter => parameter.id === pattern.spacingParameterId)?.value ?? 0;
      for (const member of members.filter(item => pattern.memberIds.includes(item.id))) for (let index = 0; index < count; index += 1) {
        const position: Vector3 = [...member.position];
        if (pattern.axis === "x") position[0] += spacing * index;
        if (pattern.axis === "y") position[1] += spacing * index;
        if (pattern.axis === "z") position[2] += spacing * index;
        result.push({ ...member, id: index === 0 ? member.id : `${member.id}#${pattern.id}-${index}`, sourceMemberId: member.id, patternInstance: index > 0, position });
      }
    }
    return result;
  }, [members, parameters, patterns]);
  useEffect(() => { const viewCamera = camera as PerspectiveCamera; const vertical = viewCamera.fov * Math.PI / 180; const distance = radius / Math.sin(vertical / 2) * 1.8; viewCamera.position.set(distance, -distance, distance); viewCamera.up.set(0, 0, 1); viewCamera.near = .001; viewCamera.far = distance + radius * 100; viewCamera.updateProjectionMatrix(); controls.current?.target.set(0, 0, 0); controls.current?.update(); }, [camera, size.width, size.height, radius]);
  return <><Grid args={[radius * 40, radius * 40]} rotation={[Math.PI / 2, 0, 0]} cellSize={radius / 5} cellThickness={.5} sectionSize={radius} sectionThickness={1} fadeDistance={radius * 200} cellColor="#c0c8d0" sectionColor="#8da6be" side={DoubleSide}/>{geometry && !renderedMembers.length && <mesh><primitive object={geometry} attach="geometry" dispose={null}/><meshStandardMaterial color="#7394b0" metalness={.18} roughness={.55} side={DoubleSide}/></mesh>}{renderedMembers.map((member, index) => member.template.templateKind === "component"
    ? <ComponentMemberModel key={`${member.id}-${index}`} member={member} selected={(member.sourceMemberId || member.id) === selectedMemberId} onSelect={() => onSelectMember(member.sourceMemberId || member.id)} onMove={position => onMoveMember(member.sourceMemberId || member.id, position)}/>
    : <MemberModel key={`${member.id}-${index}`} member={member} selected={(member.sourceMemberId || member.id) === selectedMemberId} onSelect={() => onSelectMember(member.sourceMemberId || member.id)} onMove={position => onMoveMember(member.sourceMemberId || member.id, position)}/>)}<OrbitControls ref={controls} makeDefault minDistance={radius / 100} maxDistance={radius * 100}/></>;
}

type Props = { members: ComponentMember[]; parameters?: ComponentParameter[]; patterns?: ComponentPattern[]; mateCount: number; previewTemplate: PublishedTemplate | null; selectedMemberId?: string | null; onSelectMember?: (id: string) => void; onMoveMember?: (id: string, position: Vector3) => void; onCopyMember?: (id: string) => void; onRemoveMember?: (id: string) => void; onDropTemplate?: () => void };
export function ComponentViewport({ members, parameters = [], patterns = [], mateCount, previewTemplate, selectedMemberId = null, onSelectMember = () => undefined, onMoveMember = () => undefined, onCopyMember = () => undefined, onRemoveMember = () => undefined, onDropTemplate = () => undefined }: Props) {
  const preview = useTemplatePreview(members.length || previewTemplate?.templateKind === "component" ? null : previewTemplate); const loaded = preview.result;
  return <section className="viewport" aria-label="组件三维视图" tabIndex={0} onDragOver={event => { if (event.dataTransfer.types.includes("text/plain")) event.preventDefault(); }} onDrop={event => { if (event.dataTransfer.types.includes("text/plain")) { event.preventDefault(); onDropTemplate(); } }} onKeyDown={event => { if (!selectedMemberId) return; if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); onRemoveMember(selectedMemberId); } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") { event.preventDefault(); onCopyMember(selectedMemberId); } }}><div className="viewport-toolbar"><button className="tool active" type="button"><Eye size={15}/>预览</button><span>从左侧拖入零部件 · 点击选择并拖拽移动 · 滚轮缩放</span>{selectedMemberId && <><button className="tool" type="button" title="复制成员 (Ctrl/Cmd+D)" aria-label="复制选中成员" onClick={() => onCopyMember(selectedMemberId)}><Copy size={14}/></button><button className="tool" type="button" title="删除选中成员 (Delete)" aria-label="删除选中成员" onClick={() => onRemoveMember(selectedMemberId)}><Trash2 size={14}/></button></>}<span className="view-status">{members.length} 个成员 · {patterns.length ? `${patterns.length} 条构成规则` : `${mateCount} 个连接`}</span></div><div className="viewport-scene"><Canvas camera={{ position: [7, 5, 7], up: [0, 0, 1], fov: 42 }}><color attach="background" args={["#f5f7f9"]}/><ambientLight intensity={1.8}/><directionalLight position={[4, 6, 5]} intensity={2}/><PreviewScene members={members} parameters={parameters} patterns={patterns} selectedMemberId={selectedMemberId} onSelectMember={onSelectMember} onMoveMember={onMoveMember} geometry={loaded?.geometry || null}/></Canvas></div>{!loaded && !members.length && <div className="empty-overlay" role="status"><Boxes size={30}/><b>{previewTemplate ? previewTemplate.name : "浏览已发布零部件"}</b><span>{preview.error || (previewTemplate ? "正在准备模型…" : "点击左侧模板预览零件")}</span>{preview.error && <><AlertTriangle size={18}/><button type="button" className="ghost" onClick={preview.retry}>重试</button></>}</div>}{members.length > 0 && <div className="preview-caption" role="status"><b>组件成员</b><span>选中成员后拖拽其模型即可调整位置</span></div>}</section>;
}
