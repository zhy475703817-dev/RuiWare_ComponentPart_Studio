import { useEffect, useState } from "react";
import { Download, ExternalLink, Paperclip, X } from "lucide-react";
import type { ComponentDefinition, ConnectionMethod, EvidenceAttachment } from "../domain/component";
import { deleteEvidenceFile, readEvidenceFile, saveEvidenceFile } from "../domain/evidenceStore";
import { isValidComponentCode, normalizeComponentCode } from "../domain/componentCode";

type Props = {
  definition: ComponentDefinition;
  codeUnique: boolean;
  onChange: (patch: Partial<ComponentDefinition>) => void;
};

const connectionOptions = ["螺栓连接", "焊接", "铆接", "插接/卡扣", "型材槽连接", "销钉连接"];
const maxFileSize = 20 * 1024 * 1024;
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

export function DefinitionStage({ definition, codeUnique, onChange }: Props) {
  const [tagInput, setTagInput] = useState("");
  const [fileError, setFileError] = useState("");
  const [savingFiles, setSavingFiles] = useState(false);
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});
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
      <label>组件编码<input value={definition.code} aria-invalid={!isValidComponentCode(definition.code) || !codeUnique} onChange={event => onChange({ code: normalizeComponentCode(event.target.value) })}/>
        <small className={!codeUnique ? "definition-code-error" : "definition-code-hint"}>{!isValidComponentCode(definition.code) ? "大写字母开头，3–40 位，仅限大写字母、数字、_、-" : !codeUnique ? "此编码已被其他组件草稿使用" : "格式正确，且当前唯一"}</small>
      </label>
      <label>项目编码<input value={definition.projectCode} placeholder="项目唯一标识" onChange={event => onChange({ projectCode: event.target.value })}/></label>
      <label>组件类型<select value={definition.category} onChange={event => onChange({ category: event.target.value })}><option>货架组件</option><option>连接组件</option><option>支撑组件</option></select></label>
      <label>项目负责人<input value={definition.owner} onChange={event => onChange({ owner: event.target.value })}/></label>
      <label>项目所属组织<input value={definition.organization} onChange={event => onChange({ organization: event.target.value })}/></label>
    </div>

    <label>设计说明<textarea value={definition.description} onChange={event => onChange({ description: event.target.value })}/></label>

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
