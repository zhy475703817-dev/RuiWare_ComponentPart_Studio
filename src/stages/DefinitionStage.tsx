import { useRef } from "react";
import { useState } from "react";
import { Download, FileText, Image as ImageIcon, Paperclip, X } from "lucide-react";
import type { ComponentDefinition, ConnectionType, EvidenceFile } from "../domain/component";

type Props = { definition: ComponentDefinition; onChange: (patch: Partial<ComponentDefinition>) => void; draftId?: string | null };

const connectionOptions: ConnectionType[] = ["螺栓连接", "焊接", "卡扣连接", "插接", "铆接", "胶接", "自定义"];
const embeddedFileLimit = 5 * 1024 * 1024;

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function DefinitionStage({ definition, onChange, draftId }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [evidenceError, setEvidenceError] = useState("");

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    const additions: EvidenceFile[] = [];
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
      } catch (error) {
        message = error instanceof Error ? `上传失败：${error.message}` : "文件上传失败";
      }
    }
    if (additions.length) onChange({ evidenceFiles: [...definition.evidenceFiles, ...additions] });
    setEvidenceError(message);
    if (fileInput.current) fileInput.current.value = "";
  }

  function updateTags(value: string) {
    onChange({ searchTags: value.split(/[，,\n]/).map(item => item.trim()).filter(Boolean).slice(0, 100) });
  }

  function updateConnection(key: "partConnectionType" | "componentConnectionType", value: ConnectionType) {
    onChange({ [key]: value });
  }

  function removeEvidence(id: string) {
    onChange({ evidenceFiles: definition.evidenceFiles.filter(file => file.id !== id) });
  }

  const connectionField = (label: string, typeKey: "partConnectionType" | "componentConnectionType", customKey: "partConnectionCustom" | "componentConnectionCustom") => (
    <div className="definition-connection">
      <label>{label}
        <select value={definition[typeKey]} onChange={event => updateConnection(typeKey, event.target.value as ConnectionType)}>
          {connectionOptions.map(option => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
      {definition[typeKey] === "自定义" && <label>自定义说明
        <input value={definition[customKey]} placeholder="请描述连接件、受力方式或接口约定" onChange={event => onChange({ [customKey]: event.target.value })}/>
      </label>}
    </div>
  );

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
      <label>项目检索标签<textarea rows={2} value={definition.searchTags.join("，")} placeholder="多个标签用逗号或换行分隔" onChange={event => updateTags(event.target.value)}/></label>
      <label>组件类型<select value={definition.category} onChange={event => onChange({ category: event.target.value })}><option>货架组件</option><option>连接组件</option><option>支撑组件</option></select></label>
    </div>

    <div className="definition-group">
      <h4>连接形式</h4>
      {connectionField("组件内部零部件连接", "partConnectionType", "partConnectionCustom")}
      {connectionField("不同组件之间连接", "componentConnectionType", "componentConnectionCustom")}
    </div>

    <div className="definition-group">
      <h4>设计说明</h4>
      <label>设计说明<textarea rows={4} value={definition.description} onChange={event => onChange({ description: event.target.value })}/></label>
      <label>设计约束与可变范围<textarea rows={5} value={definition.designConstraints} placeholder="描述组合限制、干涉风险、允许的尺寸/数量变化及必须满足的条件" onChange={event => onChange({ designConstraints: event.target.value })}/></label>
    </div>

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
