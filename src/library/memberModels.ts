import { Box3, Euler, Matrix4, Quaternion, Vector3 } from "three";
import type { ComponentMember } from "../domain/component.ts";
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
