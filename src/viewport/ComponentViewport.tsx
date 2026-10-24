import { useEffect, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { AlertTriangle, Boxes, Eye, RefreshCw } from "lucide-react";
import { DoubleSide, Vector3 } from "three";
import type { BufferGeometry, PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import type { ComponentMember } from "../domain/component";
import type { PublishedTemplate } from "../library/publishedLibrary";
import { useTemplatePreview } from "../library/useTemplatePreview";

function PreviewScene({ geometry }: { geometry: BufferGeometry | null }) {
  const { camera, size } = useThree();
  const controls = useRef<OrbitControlsImpl>(null);
  const radius = geometry?.boundingSphere?.radius || 8;
  const center = geometry?.boundingSphere?.center || new Vector3();
  const section = 10 ** Math.floor(Math.log10(radius));
  const maxDistance = radius * 100;

  useEffect(() => {
    const viewCamera = camera as PerspectiveCamera;
    const target = geometry?.boundingSphere?.center || new Vector3();
    const modelRadius = geometry?.boundingSphere?.radius || 8;
    const vertical = viewCamera.fov * Math.PI / 180;
    const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * size.width / size.height);
    const distance = modelRadius / Math.sin(Math.min(vertical, horizontal) / 2) * 1.2;
    viewCamera.position.copy(target).add(new Vector3(1, -1, 0.8).normalize().multiplyScalar(distance));
    viewCamera.up.set(0, 0, 1);
    viewCamera.near = Math.max(modelRadius / 10000, 0.001);
    viewCamera.far = distance + modelRadius * 100;
    viewCamera.updateProjectionMatrix();
    if (controls.current) {
      controls.current.target.copy(target);
      controls.current.update();
    }
  }, [geometry, camera, size.width, size.height]);

  return <>
    {/* Keep the floor visible beneath the model throughout the permitted zoom range. */}
    <Grid key={section} args={[section * 40, section * 40]} position={[center.x, center.y, geometry?.boundingBox?.min.z || 0]} rotation={[Math.PI / 2, 0, 0]} cellSize={section / 5} cellThickness={.5} sectionSize={section} sectionThickness={1} fadeDistance={maxDistance * 2} cellColor="#c0c8d0" sectionColor="#8da6be" side={DoubleSide}/>
    {geometry && <mesh><primitive object={geometry} attach="geometry" dispose={null}/><meshStandardMaterial color="#7394b0" metalness={.18} roughness={.55} side={DoubleSide}/></mesh>}
    <OrbitControls ref={controls} makeDefault minDistance={radius / 100} maxDistance={maxDistance}/>
  </>;
}

type Props = { members: ComponentMember[]; mateCount: number; previewTemplate: PublishedTemplate | null };

export function ComponentViewport({ members, mateCount, previewTemplate }: Props) {
  const isComponent = previewTemplate?.templateKind === "component";
  const preview = useTemplatePreview(isComponent ? null : previewTemplate);
  const loaded = preview.result;
  return <section className="viewport" aria-label="组件三维视图">
    <div className="viewport-toolbar"><button className="tool active" type="button"><Eye size={15}/>预览</button><span>拖拽视图旋转 · 滚轮缩放</span><span className="view-status">{members.length} 个成员 · {mateCount} 个连接</span></div>
    <div className="viewport-scene"><Canvas camera={{ position: [7, 5, 7], up: [0, 0, 1], fov: 42 }}>
      <color attach="background" args={["#f5f7f9"]}/><ambientLight intensity={1.8}/><directionalLight position={[4, 6, 5]} intensity={2}/>
      <PreviewScene geometry={loaded?.geometry || null}/>
    </Canvas></div>
    {!loaded && <div className={`empty-overlay ${preview.error ? "preview-error" : ""}`} role="status" data-preview-state={preview.error ? "error" : isComponent ? "unavailable" : previewTemplate ? "loading" : "idle"}>
      {preview.error ? <AlertTriangle size={30}/> : previewTemplate && !isComponent ? <RefreshCw size={30} className="spinning"/> : <Boxes size={30}/>}
      <b>{previewTemplate ? previewTemplate.name : members.length ? "组件成员模型尚未加载" : "浏览已发布零部件"}</b>
      <span>{isComponent ? "此组件暂无可显示的模型，可从发布详情加入当前组件。" : preview.error || (previewTemplate ? "正在准备模型…" : "点击左侧模板预览零件")}</span>
      {preview.error && <button type="button" className="ghost" onClick={preview.retry}>重试</button>}
    </div>}
    {loaded && previewTemplate && <div className="preview-caption" role="status" data-preview-state={preview.loading ? "loading" : "ready"}>
      <b>{previewTemplate.name} · V{previewTemplate.version}</b>
      {preview.info?.parameters.length ? <div className="preview-parameters" aria-label="零件参数">
        {preview.info.parameters.map(parameter => <label key={parameter.id}>{parameter.label}
          {parameter.valueType === "boolean" ? <input type="checkbox" checked={Boolean(preview.values[parameter.id])} onChange={event => preview.setParameter(parameter.id, event.target.checked)}/> : parameter.valueType === "enum" && parameter.allowedValues.length ? <select value={String(preview.values[parameter.id] ?? parameter.default)} onChange={event => preview.setParameter(parameter.id, event.target.value)}>{parameter.allowedValues.map(value => <option key={String(value)} value={String(value)}>{String(value)}</option>)}</select> : <span className="preview-parameter-value"><input type={parameter.valueType === "string" ? "text" : "number"} min={parameter.minimum ?? undefined} max={parameter.maximum ?? undefined} step={parameter.valueType === "integer" ? 1 : "any"} value={String(preview.values[parameter.id] ?? parameter.default)} onChange={event => preview.setParameter(parameter.id, parameter.valueType === "string" ? event.target.value : Number(event.target.value))}/><small>{parameter.unit}</small></span>}
        </label>)}
      </div> : null}
      {preview.loading && <span className="preview-update" role="status"><RefreshCw size={13} className="spinning"/>正在更新</span>}
      {preview.error && <span className="preview-update preview-update-error" role="alert">{preview.error}</span>}
    </div>}
  </section>;
}
