import type { ComponentDocument, ComponentMember } from "../domain/component.ts";
import type { ValidationCheck } from "../domain/validation.ts";
import type { PublishedTemplate } from "./publishedLibrary.ts";

export type ComponentPublication = PublishedTemplate & { templateKind: "component"; document: ComponentDocument; checks: ValidationCheck[] };

const messages: Record<string, string> = {
  REMOTE_STORAGE_NOT_CONFIGURED: "\u5c1a\u672a\u914d\u7f6e\u5171\u4eab\u53d1\u5e03\u5e93\uff0c\u8bf7\u4f7f\u7528\u73b0\u6709\u5b58\u50a8\u914d\u7f6e\u5e76\u91cd\u542f\u7ec4\u4ef6\u5e73\u53f0\u3002",
  REMOTE_STORAGE_UNAVAILABLE: "\u5171\u4eab\u53d1\u5e03\u5e93\u6682\u65f6\u65e0\u6cd5\u8bbf\u95ee\uff0c\u8349\u7a3f\u5df2\u4fdd\u7559\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002",
  REMOTE_STORAGE_INTEGRITY_ERROR: "\u6a21\u677f\u5305\u6821\u9a8c\u5931\u8d25\uff0c\u8bf7\u8054\u7cfb\u53d1\u5e03\u8005\u786e\u8ba4\u53d1\u5e03\u7248\u672c\u3002",
  REVISION_CONFLICT: "草稿已发生变化，请重新读取后再发布。",
  COMPONENT_PUBLICATION_INVALID: "请填写组件名称，并检查成员的发布版本。",
  COMPONENT_REFERENCE_INVALID: "引用的组件版本不一致，请删除该成员后重新加入。",
  COMPONENT_PUBLICATION_NOT_FOUND: "引用的组件发布版本不存在，请刷新列表后重新选择。",
};

async function request(url: string, body?: unknown): Promise<ComponentPublication> {
  const response = await fetch(url, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal: AbortSignal.timeout(20000),
    headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(messages[data?.error?.code] || "组件发布库操作失败，请检查连接后重试。");
  return data as ComponentPublication;
}

export const publishComponentDraft = (draftId: string, expectedRevision: number) => request(`/api/component-drafts/${encodeURIComponent(draftId)}/publish`, { expectedRevision });
export const fetchPublishedComponent = (publicationId: string) => request(`/api/published-components/${encodeURIComponent(publicationId)}`);

export function componentPublicationMember(publication: ComponentPublication, id: string): ComponentMember {
  const { document } = publication;
  return {
    id, name: publication.name, code: publication.code, role: publication.name,
    template: { templateKind: "component", publicationId: publication.publicationId, templateId: publication.templateId, version: publication.version, sha256: publication.sha256 },
    position: [0, 0, 0], rotation: [0, 0, 0],
    parameterValues: Object.fromEntries(document.parameters.filter(parameter => parameter.value !== null).map(parameter => [parameter.id, parameter.value!])),
    interfaces: document.ports.flatMap(port => {
      const source = document.assembly.members.find(member => member.id === port.source.memberId)?.interfaces.find(item => item.id === port.source.interfaceId);
      return source ? [{ id: port.id, name: port.name, type: source.type, tags: [...port.tags] }] : [];
    }),
  };
}
