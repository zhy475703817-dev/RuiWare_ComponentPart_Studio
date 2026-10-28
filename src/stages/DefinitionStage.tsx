import { useEffect, useMemo, useRef, useState } from "react";
import { Download, FileText, Image as ImageIcon, Paperclip, X } from "lucide-react";
import type { ComponentDefinition, ComponentMember, ConnectionMethod, ConnectionType, EvidenceFile } from "../domain/component";
import { interfacesForReconstruction, loadReconstructionInfo } from "../library/templatePreview";
import type { PublishedTemplate } from "../library/publishedLibrary";

type Props = {
  definition: ComponentDefinition;
  members: ComponentMember[];
  onChange: (patch: Partial<ComponentDefinition>) => void;
  draftId?: string | null;
};

const connectionOptions: ConnectionType[] = ["螺栓连接", "焊接", "卡扣连接", "插接", "铆接", "胶接", "型材槽连接", "销钉连接", "插接/卡扣"];
const embeddedFileLimit = 5 * 1024 * 1024;
const interfaceTypeLabels: Record<string, string> = { locating: "定位", connecting: "连接", supporting: "支撑", adjustable: "可调", processDatum: "工艺基准", other: "其他", plane: "平面", axis: "轴线", point: "点位" };
const locatingTypeLabels: Record<string, string> = { planeContact: "面接触", axisCoincident: "同轴", pinHole: "销孔定位", edgeStop: "边挡", slotAdjustable: "长孔可调", keyedAntiError: "键防错" };
const declarationModeLabels: Record<string, string> = { staticGeometry: "固定几何", featureDerived: "特征解析" };
const regionModeLabels: Record<string, string> = { rectangle: "矩形范围" };

function summarizeInterfaces(members: ComponentMember[]) {
  const groups = new Map<string, { count: number; dimensions: Set<string>; modes: Set<string> }>();
  for (const member of members) for (const item of member.interfaces) {
      const kind = item.interfaceType || item.type;
      const group = groups.get(kind) || { count: 0, dimensions: new Set<string>(), modes: new Set<string>() };
      group.count += 1;
      const region = item.region;
      const dimensions = region?.uSpan !== undefined && region.vSpan !== undefined
        ? `${region.uSpan} × ${region.vSpan} mm`
        : region?.uSpanExpression || region?.vSpanExpression
          ? `U ${region.uSpanExpression || "?"} × V ${region.vSpanExpression || "?"}`
          : "未声明";
      group.dimensions.add(dimensions);
      if (item.locatingType) group.modes.add(locatingTypeLabels[item.locatingType] || item.locatingType);
      if (item.declarationMode) group.modes.add(declarationModeLabels[item.declarationMode] || item.declarationMode);
      if (region?.mode) group.modes.add(regionModeLabels[region.mode] || region.mode);
      groups.set(kind, group);
  }
  return [...groups].map(([kind, group]) => ({
    kind: interfaceTypeLabels[kind] || kind,
    count: group.count,
    dimensions: [...group.dimensions].join(" / "),
    modes: [...group.modes].join("、") || "未声明",
  }));
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function DefinitionStage({ definition, members, onChange, draftId }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [evidenceError, setEvidenceError] = useState("");
  const [tagInput, setTagInput] = useState("");
  const memberInterfaceKey = JSON.stringify(members.map(member => ({ id: member.id, template: member.template, values: member.parameterValues })));
  const [resolvedInterfaceState, setResolvedInterfaceState] = useState<{ key: string; interfacesByMember: Record<string, ComponentMember["interfaces"]>; warning: boolean }>({ key: "", interfacesByMember: {}, warning: false });
  const displayMembers = useMemo(() => members.map(member => ({
    ...member,
    interfaces: resolvedInterfaceState.key === memberInterfaceKey ? resolvedInterfaceState.interfacesByMember[member.id] ?? member.interfaces : member.interfaces,
  })), [members, memberInterfaceKey, resolvedInterfaceState]);
  const interfaceSummary = summarizeInterfaces(displayMembers);

  useEffect(() => {
    const controller = new AbortController();
    if (!members.length) {
      setResolvedInterfaceState({ key: memberInterfaceKey, interfacesByMember: {}, warning: false });
      return () => controller.abort();
    }
    const timer = window.setTimeout(() => {
      const interfacesByMember: Record<string, ComponentMember["interfaces"]> = Object.fromEntries(members
        .filter(member => member.template.templateKind === "component")
        .map(member => [member.id, member.interfaces]));
      let cursor = 0;
      let warning = false;
      const resolveNext = async () => {
        while (!controller.signal.aborted) {
          const member = members[cursor++];
          if (!member) return;
          if (member.template.templateKind === "component") continue;
          const selected: PublishedTemplate = {
            templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId,
            version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name,
            createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "",
          };
          try {
            const info = await loadReconstructionInfo(selected, controller.signal, member.parameterValues);
            interfacesByMember[member.id] = interfacesForReconstruction(info);
          } catch {
            if (controller.signal.aborted) return;
            interfacesByMember[member.id] = member.interfaces;
            warning = true;
          }
        }
      };
      void Promise.all(Array.from({ length: Math.min(3, members.length) }, resolveNext)).then(() => {
        if (!controller.signal.aborted) setResolvedInterfaceState({ key: memberInterfaceKey, interfacesByMember, warning });
      });
    }, 200);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [memberInterfaceKey]);

  function addTag() {
    const tag = tagInput.trim();
    if (!tag || definition.searchTags.includes(tag)) return setTagInput("");
    onChange({ searchTags: [...definition.searchTags, tag] });
    setTagInput("");
  }

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    const additions: EvidenceFile[] = [];
    const metadata: ComponentDefinition["evidence"] = [];
    let message = "";
    for (const file of Array.from(files)) {
      if (file.size > embeddedFileLimit) { message = `${file.name} 超过单文件 5 MB 限制`; continue; }
      if (!draftId) { message = "草稿尚未就绪，暂时无法上传文件"; continue; }
      if (definition.evidenceFiles.length + additions.length >= 10 || definition.evidenceFiles.reduce((sum, item) => sum + item.size, 0) + additions.reduce((sum, item) => sum + item.size, 0) + file.size > 15 * 1024 * 1024) {
        message = "每个草稿最多添加 10 个文件，总大小不超过 15 MB";
        break;
      }
      try {
        const id = crypto.randomUUID();
        const query = new URLSearchParams({ fileName: file.name });
        const response = await fetch(`/api/component-drafts/${encodeURIComponent(draftId)}/evidence/${id}?${query}`, {
          method: "PUT", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result?.error?.code || "文件上传失败");
        additions.push(result as EvidenceFile);
        metadata.push({ id, name: file.name, mediaType: file.type || "application/octet-stream", size: file.size, addedAt: new Date().toISOString() });
      } catch (error) {
        message = error instanceof Error ? `上传失败：${error.message}` : "文件上传失败";
      }
    }
    if (additions.length) onChange({ evidenceFiles: [...definition.evidenceFiles, ...additions], evidence: [...definition.evidence, ...metadata] });
    setEvidenceError(message);
    if (fileInput.current) fileInput.current.value = "";
  }

  function updateConnection(which: "internal" | "interComponent", method: ConnectionMethod) {
    const connectionForms = { ...definition.connectionForms, [which]: method };
    const type = method.mode === "custom" ? "自定义" : method.value as ConnectionType;
    if (which === "internal") {
      onChange({ connectionForms, partConnectionType: type, partConnectionCustom: method.mode === "custom" ? method.value : "" });
    } else {
      onChange({ connectionForms, componentConnectionType: type, componentConnectionCustom: method.mode === "custom" ? method.value : "" });
    }
  }

  function removeEvidence(id: string) {
    onChange({
      evidenceFiles: definition.evidenceFiles.filter(file => file.id !== id),
      evidence: definition.evidence.filter(file => file.id !== id),
    });
  }

  const connectionField = (label: string, which: "internal" | "interComponent") => {
    const method = definition.connectionForms[which];
    return (
    <div className="definition-connection">
      <label>{label}输入方式
        <select value={method.mode} onChange={event => {
          const mode = event.target.value as ConnectionMethod["mode"];
          const value = mode === "custom" && method.mode === "preset" ? "" : mode === "preset" && !connectionOptions.includes(method.value as ConnectionType) ? connectionOptions[0] : method.value;
          updateConnection(which, { ...method, mode, value });
        }}>
          <option value="preset">默认选项</option><option value="custom">用户自定义</option>
        </select>
      </label>
      {method.mode === "preset" ? <label>{label}方式
        <select value={method.value} onChange={event => updateConnection(which, { ...method, value: event.target.value })}>
          {connectionOptions.map(option => <option key={option} value={option}>{option}</option>)}
        </select>
      </label> : <label>{label}自定义方式
        <input value={method.value} placeholder="输入连接方式" onChange={event => updateConnection(which, { ...method, value: event.target.value })}/>
      </label>}
      <label>{label}连接说明<textarea value={method.details} placeholder="填写连接位置、紧固件或装配要求" onChange={event => updateConnection(which, { ...method, details: event.target.value })}/></label>
    </div>
    );
  };

  return <div className="definition-form">
    <div className="definition-group">
      <h4>项目身份</h4>
      <label>项目名称<input value={definition.name} onChange={event => onChange({ name: event.target.value })}/></label>
      <label>项目编码<input value={definition.projectCode} placeholder="例如：RACK-2026-001" onChange={event => onChange({ projectCode: event.target.value })}/></label>
      <label>组件编码<input value={definition.code} placeholder="区别于项目编码的组件 ID" onChange={event => onChange({ code: event.target.value })}/></label>
      <div className="definition-grid">
        <label>项目负责人<input value={definition.owner} onChange={event => onChange({ owner: event.target.value })}/></label>
        <label>项目所属组织<input value={definition.organization} onChange={event => onChange({ organization: event.target.value })}/></label>
      </div>
      <label>组件类型<select value={definition.category} onChange={event => onChange({ category: event.target.value })}><option>货架组件</option><option>连接组件</option><option>支撑组件</option></select></label>
    </div>

    <div className="definition-group">
      <h4>连接形式</h4>
      {connectionField("组件内部零部件连接", "internal")}
      {connectionField("不同组件之间连接", "interComponent")}
    </div>

    <div className="definition-group">
      <h4>设计说明</h4>
      <label>设计说明<textarea rows={4} value={definition.description} onChange={event => onChange({ description: event.target.value })}/></label>
      <label>设计约束与可变范围<textarea rows={5} value={definition.designConstraints} placeholder="描述组合限制、干涉风险、允许的尺寸/数量变化及必须满足的条件" onChange={event => onChange({ designConstraints: event.target.value })}/></label>
    </div>

    <section className="definition-subsection">
      <h4>成员参数范围与接口</h4>
      {members.length === 0 ? <p className="empty-text">添加零部件后可查看其参数范围与接口数量</p> : <>
        {resolvedInterfaceState.key !== memberInterfaceKey && <p className="empty-text">正在按当前零件参数更新接口信息…</p>}
        {resolvedInterfaceState.key === memberInterfaceKey && resolvedInterfaceState.warning && <p className="empty-text">部分接口信息暂时无法更新，当前显示最近读取的数据。</p>}
        <ul className="definition-member-list">{displayMembers.map(member => <li key={member.id}>
        <b>{member.role || member.name}</b><span>{member.interfaces.length} 个接口</span>
        {member.parameterDefinitions?.length ? <small>可调范围：{member.parameterDefinitions.map(parameter => `${parameter.label}: ${parameter.minimum ?? "无下限"} ～ ${parameter.maximum ?? "无上限"}${parameter.unit ? ` ${parameter.unit}` : ""}`).join(" · ")}</small> : <small>未定义可调参数范围</small>}
      </li>)}</ul>
        {interfaceSummary.length > 0 && <div className="definition-interface-summary"><b>接口种类汇总 · 零部件之间连接方式：{definition.connectionForms.internal.value}</b>{interfaceSummary.map(item => <small key={item.kind}>接口种类：{item.kind} · 物理尺寸：{item.dimensions} · 接口模式：{item.modes} · 数量：{item.count}</small>)}</div>}
      </>}
    </section>

    <section className="definition-subsection">
      <h4>项目检索标签</h4>
      <div className="definition-tag-entry"><input value={tagInput} placeholder="输入标签后按 Enter" onChange={event => setTagInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }}/><button type="button" className="ghost" onClick={addTag}>添加</button></div>
      <div className="definition-tags">{definition.searchTags.map(tag => <span key={tag}>{tag}<button type="button" aria-label={`移除标签 ${tag}`} onClick={() => onChange({ searchTags: definition.searchTags.filter(item => item !== tag) })}><X size={13}/></button></span>)}</div>
    </section>

    <div className="definition-group">
      <div className="definition-group-title"><h4>证据与参考</h4><button type="button" className="ghost compact" onClick={() => fileInput.current?.click()}><Paperclip size={13}/>添加文件</button></div>
      <input ref={fileInput} className="visually-hidden" type="file" multiple onChange={event => { void addFiles(event.target.files); }}/>
      <p className="field-hint">支持图片和常见文档，单个文件不超过 5 MB；导出组件 JSON 时会嵌入文件内容。</p>
      {evidenceError && <p className="evidence-error" role="alert">{evidenceError}</p>}
      {definition.evidenceFiles.length === 0 && <p className="empty-text">尚未添加证据或参考文件</p>}
      <div className="evidence-list">{definition.evidenceFiles.map(file => <div className="evidence-item" key={file.id}>
        {file.mediaType.startsWith("image/") && draftId && <img className="evidence-thumbnail" src={`/api/component-drafts/${encodeURIComponent(draftId)}/evidence/${encodeURIComponent(file.id)}`} alt=""/>}
        <span className="evidence-icon">{file.mediaType.startsWith("image/") ? <ImageIcon size={13}/> : <FileText size={13}/>}</span>
        <span className="evidence-meta"><b>{file.fileName}</b><small>{file.mediaType} · {formatSize(file.size)}</small></span>
        {(file.dataUrl || draftId) && <a className="evidence-download" href={file.dataUrl || `/api/component-drafts/${encodeURIComponent(draftId || "")}/evidence/${encodeURIComponent(file.id)}`} download={file.fileName} aria-label={`下载 ${file.fileName}`}><Download size={14}/></a>}
        <button type="button" className="icon-button" aria-label={`移除 ${file.fileName}`} title={`移除 ${file.fileName}`} onClick={() => removeEvidence(file.id)}><X size={15}/></button>
      </div>)}</div>
    </div>
  </div>;
}
