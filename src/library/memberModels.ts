import { Box3, Euler, Matrix4, Quaternion, Vector3 } from "three";
import type { ComponentMember, Vector3 as Position } from "../domain/component.ts";
import { resolveComponentComposition } from "../domain/composition.ts";
import { fetchPublishedComponent } from "./componentPublications.ts";
import type { PublishedTemplate } from "./publishedLibrary.ts";
import { cachedPreview } from "./useTemplatePreview.ts";

export function memberTemplate(member: ComponentMember): PublishedTemplate {
  return { ...member.template, code: member.code, name: member.name,
    createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "" };
}

/** Reuse the same geometry cache as the canvas, including saved instance transforms. */
export async function memberBounds(member: ComponentMember, ancestors = new Set<string>()): Promise<Box3> {
  let bounds: Box3;
  if (member.template.templateKind === "component") {
    const id = member.template.publicationId;
    if (ancestors.has(id) || ancestors.size >= 16) throw new Error("组件嵌套关系无法读取，请检查发布版本。");
    const publication = await fetchPublishedComponent(id);
    if (publication.templateId !== member.template.templateId || publication.version !== member.template.version ||
        publication.sha256.toLowerCase() !== member.template.sha256.toLowerCase()) throw new Error("发布版本已变化，请刷新列表后重试。");
    bounds = new Box3();
    const next = new Set([...ancestors, id]);
    for (const child of resolveComponentComposition(publication.document, member.parameterValues)) {
      bounds.union(await memberBounds(child, next));
    }
  } else {
    const cached = cachedPreview(memberTemplate(member), member.parameterValues);
    try { bounds = (await cached.promise).geometry.boundingBox!.clone(); }
    finally { cached.release(); }
  }
  return bounds.applyMatrix4(new Matrix4().compose(new Vector3(...member.position),
    new Quaternion().setFromEuler(new Euler(...member.rotation)), new Vector3(1, 1, 1)));
}

async function memberMinimumZ(member: ComponentMember, parentMatrix: Matrix4, ancestors: Set<string>): Promise<number> {
  if (member.position.length !== 3 || member.rotation.length !== 3 ||
      ![...member.position, ...member.rotation].every(Number.isFinite)) throw new Error("成员位置或旋转数值无效，无法贴地。");
  const localMatrix = new Matrix4().compose(new Vector3(...member.position),
    new Quaternion().setFromEuler(new Euler(...member.rotation)), new Vector3(1, 1, 1));
  const worldMatrix = new Matrix4().multiplyMatrices(parentMatrix, localMatrix);
  if (!worldMatrix.elements.every(Number.isFinite)) throw new Error("成员变换超出有效范围，无法贴地。");
  let minimumZ = Infinity;
  if (member.template.templateKind === "component") {
    const id = member.template.publicationId;
    if (ancestors.has(id) || ancestors.size >= 16) throw new Error("组件嵌套关系无法读取，请检查发布版本。");
    const publication = await fetchPublishedComponent(id);
    if (publication.templateId !== member.template.templateId || publication.version !== member.template.version ||
        publication.sha256.toLowerCase() !== member.template.sha256.toLowerCase()) throw new Error("发布版本已变化，请刷新列表后重试。");
    const next = new Set([...ancestors, id]);
    for (const child of resolveComponentComposition(publication.document, member.parameterValues)) {
      minimumZ = Math.min(minimumZ, await memberMinimumZ(child, worldMatrix, next));
    }
  } else {
    const cached = cachedPreview(memberTemplate(member), member.parameterValues);
    try {
      const { geometry } = await cached.promise;
      const positions = geometry.getAttribute("position");
      if (!positions || positions.itemSize < 3 || positions.count === 0) throw new Error("模型没有有效几何，无法贴地。");
      const point = new Vector3();
      const indices = geometry.getIndex();
      const count = indices ? indices.count : positions.count;
      for (let index = 0; index < count; index += 1) {
        point.fromBufferAttribute(positions, indices ? indices.getX(index) : index).applyMatrix4(worldMatrix);
        if (![point.x, point.y, point.z].every(Number.isFinite)) throw new Error("模型包含无效坐标，无法贴地。");
        minimumZ = Math.min(minimumZ, point.z);
      }
    } finally { cached.release(); }
  }
  if (!Number.isFinite(minimumZ)) throw new Error("模型没有有效几何，无法贴地。");
  return minimumZ;
}

/** Ground actual transformed vertices; rotated bounding boxes may include empty space below the model. */
export async function memberGroundPosition(member: ComponentMember): Promise<Position> {
  const minimumZ = await memberMinimumZ(member, new Matrix4(), new Set());
  const z = member.position[2] - minimumZ;
  if (!Number.isFinite(z)) throw new Error("成员变换超出有效范围，无法贴地。");
  return [member.position[0], member.position[1], z === 0 ? 0 : z];
}
