import { useEffect, useMemo, useState } from "react";
import { Download, ExternalLink, Paperclip, X } from "lucide-react";
import type { ComponentDefinition, ComponentMember, ConnectionMethod, EvidenceAttachment, PartInterface } from "../domain/component";
import { interfaceGender } from "../domain/interfaceCompatibility";
import { deleteEvidenceFile, readEvidenceFile, saveEvidenceFile } from "../domain/evidenceStore";
import { interfacesForReconstruction, loadReconstructionInfo } from "../library/templatePreview";
import type { PublishedTemplate } from "../library/publishedLibrary";

type Props = {
  definition: ComponentDefinition;
  members: ComponentMember[];
  selectedMemberId: string | null;
  hoveredMemberId: string | null;
  compatibleMemberIds: Set<string>;
  onSelectMember: (id: string) => void;
  onHoverMember: (id: string | null) => void;
  onChange: (patch: Partial<ComponentDefinition>) => void;
};

const connectionOptions = ["螺栓连接", "焊接", "铆接", "插接/卡扣", "型材槽连接", "销钉连接"];
const maxFileSize = 20 * 1024 * 1024;
const interfaceTypeLabels: Record<string, string> = { locating: "定位", connecting: "连接", supporting: "支撑", adjustable: "可调", processDatum: "工艺基准", other: "其他", plane: "平面", axis: "轴线", point: "点位" };
const locatingTypeLabels: Record<string, string> = { planeContact: "面接触", axisCoincident: "同轴", pinHole: "销孔定位", edgeStop: "边挡", slotAdjustable: "长孔可调", keyedAntiError: "键防错" };
const declarationModeLabels: Record<string, string> = { staticGeometry: "固定几何", featureDerived: "特征解析" };
const regionModeLabels: Record<string, string> = { rectangle: "矩形范围" };

function summarizeInterfaces(members: ComponentMember[]) {
  const groups = new Map<string, { count: number; dimensions: Set<string>; modes: Set<string>; genders: Set<string>; methods: Set<string> }>();
  for (const member of members) for (const item of member.interfaces) {
      const kind = item.interfaceType || item.type;
      const group = groups.get(kind) || { count: 0, dimensions: new Set<string>(), modes: new Set<string>(), genders: new Set<string>(), methods: new Set<string>() };
      group.count += 1;
      const region = item.region;
      const dimensions = region?.uSpan !== undefined && region.vSpan !== undefined
        ? `${region.uSpan} × ${region.vSpan} mm`
        : region?.uSpanExpression || region?.vSpanExpression
          ? `U ${region.uSpanExpression || "?"} × V ${region.vSpanExpression || "?"}`
          : "未声明";
      group.dimensions.add(dimensions);
      const size = item.interfaceSize;
      if (size && [size.diameter, size.width, size.height, size.depth].some(value => value !== undefined)) {
        const physical = [size.diameter !== undefined ? `直径 ${size.diameter}` : "", size.width !== undefined ? `宽 ${size.width}` : "", size.height !== undefined ? `高 ${size.height}` : "", size.depth !== undefined ? `深 ${size.depth}` : ""].filter(Boolean).join(" × ");
        if (physical) group.dimensions.add(`${physical}${size.unit ? ` ${size.unit}` : " mm"}`);
      }
      group.genders.add(interfaceGender(item) === "male" ? "公" : interfaceGender(item) === "female" ? "母" : "中性/未声明");
      if (item.connectionMethod) group.methods.add(item.connectionMethod);
      if (item.locatingType) group.modes.add(locatingTypeLabels[item.locatingType] || item.locatingType);
      if (item.declarationMode) group.modes.add(declarationModeLabels[item.declarationMode] || item.declarationMode);
      if (region?.mode) group.modes.add(regionModeLabels[region.mode] || region.mode);
      groups.set(kind, group);
  }
  return [...groups].map(([kind, group]) => ({
    kind: interfaceTypeLabels[kind] || kind,
    count: group.count,
    dimensions: [...group.dimensions].join(" / "),
    modes: [...group.modes, ...[...group.genders].map(gender => `公母：${gender}`), ...[...group.methods].map(method => `连接：${method}`)].join("、") || "未声明",
  }));
}

function describeInterfaceDimensions(item: PartInterface): string {
  const size = item.interfaceSize;
  if (size && [size.diameter, size.width, size.height, size.depth].some(value => value !== undefined)) {
    const physical = [
      size.diameter !== undefined ? `直径 ${size.diameter}` : "",
      size.width !== undefined ? `宽 ${size.width}` : "",
      size.height !== undefined ? `高 ${size.height}` : "",
      size.depth !== undefined ? `深 ${size.depth}` : "",
    ].filter(Boolean).join(" × ");
    return `${physical}${size.unit ? ` ${size.unit}` : " mm"}`;
  }
  const region = item.region;
  if (region?.uSpan !== undefined && region.vSpan !== undefined) return `${region.uSpan} × ${region.vSpan} mm`;
  if (region?.uSpanExpression || region?.vSpanExpression) return `U ${region.uSpanExpression || "?"} × V ${region.vSpanExpression || "?"}`;
  return "未声明";
}

function memberInterfaceDimensions(member: ComponentMember): string {
  return [...new Set(member.interfaces.map(describeInterfaceDimensions))].join("、") || "未声明";
}

function ConnectionEditor({ label, method, onChange }: { label: string; method: ConnectionMethod; onChange: (method: ConnectionMethod) => void }) {
  return <div className="definition-connection">
    <label>{label}连接方式
      <select value={method.mode} onChange={event => {
        const mode = event.target.value as ConnectionMethod["mode"];
        onChange({ ...method, mode, value: mode === "preset" && !connectionOptions.includes(method.value) ? connectionOptions[0] : method.value });
      }}>
        <option value="preset">默认选项</option><option value="custom">用户自定义</option>
      </select>
    </label>
    {method.mode === "preset" ? <label>{label}方式
      <select value={method.value} onChange={event => onChange({ ...method, value: event.target.value })}>
        {connectionOptions.map(option => <option key={option}>{option}</option>)}
      </select>
    </label> : <label>{label}自定义方式
      <input value={method.value} placeholder="输入连接方式" onChange={event => onChange({ ...method, value: event.target.value })}/>
    </label>}
    <label>{label}连接说明<textarea value={method.details} placeholder="填写连接位置、紧固件或装配要求" onChange={event => onChange({ ...method, details: event.target.value })}/></label>
  </div>;
}

export function DefinitionStage({ definition, members, selectedMemberId, hoveredMemberId, compatibleMemberIds, onSelectMember, onHoverMember, onChange }: Props) {
  const [tagInput, setTagInput] = useState("");
  const [fileError, setFileError] = useState("");
  const [savingFiles, setSavingFiles] = useState(false);
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});
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

  useEffect(() => {
    let cancelled = false;
    const urls: Record<string, string> = {};
    void Promise.all(definition.evidence.map(async item => {
      try {
        const file = await readEvidenceFile(item.id);
        if (file) urls[item.id] = URL.createObjectURL(file);
      } catch { /* The metadata remains available if local file access is blocked. */ }
    })).then(() => { if (!cancelled) setFileUrls(urls); else Object.values(urls).forEach(URL.revokeObjectURL); });
    return () => { cancelled = true; Object.values(urls).forEach(URL.revokeObjectURL); };
  }, [definition.evidence]);

  function addTag() {
    const tag = tagInput.trim();
    if (!tag || definition.searchTags.includes(tag)) return setTagInput("");
    onChange({ searchTags: [...definition.searchTags, tag] });
    setTagInput("");
  }

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    setSavingFiles(true);
    setFileError("");
    const added: EvidenceAttachment[] = [];
    try {
      for (const file of Array.from(files)) {
        if (file.size > maxFileSize) throw new Error(`${file.name} 超过单文件 20 MB 限制`);
        const id = crypto.randomUUID();
        await saveEvidenceFile(id, file);
        added.push({ id, name: file.name, mediaType: file.type || "application/octet-stream", size: file.size, addedAt: new Date().toISOString() });
      }
      onChange({ evidence: [...definition.evidence, ...added] });
    } catch (error) {
      await Promise.all(added.map(item => deleteEvidenceFile(item.id).catch(() => undefined)));
      setFileError(error instanceof Error ? error.message : "附件保存失败，请重试");
    } finally {
      setSavingFiles(false);
    }
  }

  return <div className="definition-form">
    <div className="definition-grid">
      <label>组件名称<input value={definition.name} onChange={event => onChange({ name: event.target.value })}/></label>
      <label>组件编码<input value={definition.code} onChange={event => onChange({ code: event.target.value })}/></label>
      <label>项目编码<input value={definition.projectCode} placeholder="项目唯一标识" onChange={event => onChange({ projectCode: event.target.value })}/></label>
      <label>组件类型<select value={definition.category} onChange={event => onChange({ category: event.target.value })}><option>货架组件</option><option>连接组件</option><option>支撑组件</option></select></label>
      <label>项目负责人<input value={definition.owner} onChange={event => onChange({ owner: event.target.value })}/></label>
      <label>项目所属组织<input value={definition.organization} onChange={event => onChange({ organization: event.target.value })}/></label>
    </div>

    <label>设计说明<textarea value={definition.description} onChange={event => onChange({ description: event.target.value })}/></label>

    <section className="definition-subsection">
      <h4>成员参数范围与接口</h4>
      {members.length === 0 ? <p className="empty-text">添加零部件后可查看其参数范围与接口数量</p> : <>
        {resolvedInterfaceState.key !== memberInterfaceKey && <p className="empty-text">正在按当前零件参数更新接口信息…</p>}
        {resolvedInterfaceState.key === memberInterfaceKey && resolvedInterfaceState.warning && <p className="empty-text">部分接口信息暂时无法更新，当前显示最近读取的数据。</p>}
        <ul className="definition-member-list">{displayMembers.map(member => <li key={member.id} className={`${selectedMemberId === member.id ? "selected" : ""} ${hoveredMemberId === member.id ? "hovered" : ""} ${compatibleMemberIds.has(member.id) ? "compatible-candidate" : ""}`} role="button" tabIndex={0} aria-pressed={selectedMemberId === member.id} onClick={() => onSelectMember(member.id)} onMouseEnter={() => onHoverMember(member.id)} onMouseLeave={() => onHoverMember(null)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelectMember(member.id); } }}>
        <b>{member.role || member.name}</b><span>{member.interfaces.length} 个接口</span>
        <small className="definition-member-interface-size">接口尺寸：{memberInterfaceDimensions(member)}</small>
        {member.parameterDefinitions?.length ? <small>可调范围：{member.parameterDefinitions.map(parameter => `${parameter.label}: ${parameter.minimum ?? "无下限"} ～ ${parameter.maximum ?? "无上限"}${parameter.unit ? ` ${parameter.unit}` : ""}`).join(" · ")}</small> : <small>未定义可调参数范围</small>}
      </li>)}</ul>
        {interfaceSummary.length > 0 && <div className="definition-interface-summary"><b>接口种类汇总 · 零部件之间连接方式：{definition.connectionForms.internal.value}</b>{interfaceSummary.map(item => <div className="definition-interface-item" key={item.kind}><b>{item.kind} · {item.count} 个</b><small>物理尺寸：{item.dimensions}</small><small>接口模式：{item.modes}</small></div>)}</div>}
      </>}
    </section>

    <section className="definition-subsection">
      <h4>项目检索标签</h4>
      <div className="definition-tag-entry"><input value={tagInput} placeholder="输入标签后按 Enter" onChange={event => setTagInput(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }}/><button type="button" className="ghost" onClick={addTag}>添加</button></div>
      <div className="definition-tags">{definition.searchTags.map(tag => <span key={tag}>{tag}<button type="button" aria-label={`移除标签 ${tag}`} onClick={() => onChange({ searchTags: definition.searchTags.filter(item => item !== tag) })}><X size={13}/></button></span>)}</div>
    </section>

    <section className="definition-subsection">
      <h4>组件连接形式</h4>
      <ConnectionEditor label="零部件之间" method={definition.connectionForms.internal} onChange={method => onChange({ connectionForms: { ...definition.connectionForms, internal: method } })}/>
      <ConnectionEditor label="组件单元之间" method={definition.connectionForms.interComponent} onChange={method => onChange({ connectionForms: { ...definition.connectionForms, interComponent: method } })}/>
    </section>

    <label>设计约束与可变范围<textarea className="definition-long-text" value={definition.designConstraints} placeholder="说明组合时的干涉、载荷、尺寸边界、安装空间及可调范围" onChange={event => onChange({ designConstraints: event.target.value })}/></label>

    <section className="definition-subsection">
      <div className="definition-evidence-heading"><h4>证据与参考</h4><label className="ghost definition-file-picker"><Paperclip size={14}/>{savingFiles ? "保存中" : "添加文件"}<input type="file" multiple disabled={savingFiles} onChange={event => { void addFiles(event.target.files); event.currentTarget.value = ""; }}/></label></div>
      <p className="draft-save-status">图片和文件保存在此浏览器，并随组件导出包提供。</p>
      {fileError && <p className="definition-file-error" role="alert">{fileError}</p>}
      {definition.evidence.length === 0 ? <p className="empty-text">尚未添加证据或参考文件</p> : <ul className="definition-evidence-list">
        {definition.evidence.map(item => <li key={item.id}>
          {item.mediaType.startsWith("image/") && fileUrls[item.id] ? <a className="definition-image-preview" href={fileUrls[item.id]} target="_blank" rel="noreferrer"><img src={fileUrls[item.id]} alt={item.name}/></a> : <Paperclip size={16}/>}
          <span className="definition-evidence-name" title={item.name}>{item.name}<small>{formatFileSize(item.size)}{!fileUrls[item.id] && item.mediaType.startsWith("image/") ? " · 本地文件不可用" : ""}</small></span>
          {fileUrls[item.id] && <a className="definition-evidence-action" href={fileUrls[item.id]} target="_blank" rel="noreferrer" aria-label={`打开 ${item.name}`} title="打开"><ExternalLink size={14}/></a>}
          {fileUrls[item.id] && <a className="definition-evidence-action" href={fileUrls[item.id]} download={item.name} aria-label={`下载 ${item.name}`} title="下载"><Download size={14}/></a>}
          <button type="button" className="definition-evidence-action" aria-label={`移除 ${item.name}`} title="移除" onClick={() => {
            void deleteEvidenceFile(item.id).catch(() => undefined);
            onChange({ evidence: definition.evidence.filter(file => file.id !== item.id) });
          }}><X size={14}/></button>
        </li>)}
      </ul>}
    </section>
  </div>;
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
