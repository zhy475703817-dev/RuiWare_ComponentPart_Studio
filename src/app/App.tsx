import { Trash2 } from "lucide-react";
import { PublishedTemplateLibrary } from "../library/PublishedTemplateLibrary";
import { ComponentViewport } from "../viewport/ComponentViewport";
import { DefinitionStage } from "../stages/DefinitionStage";
import { AssemblyStage } from "../stages/AssemblyStage";
import { InterfacesStage } from "../stages/InterfacesStage";
import { ParametersStage } from "../stages/ParametersStage";
import { RulesStage } from "../stages/RulesStage";
import { ValidationStage } from "../stages/ValidationStage";
import { PublishStage } from "../stages/PublishStage";
import { WorkspaceLayout } from "./WorkspaceLayout";
import { useComponentWorkspace } from "./useComponentWorkspace";
import { AgentChanges } from "./AgentChanges";
import { STAGES } from "./stages";
import "../styles/workspace.css";

export function App() {
  const workspace = useComponentWorkspace();
  const { document, dispatch, stage, checks } = workspace;
  const { members, mates } = document.assembly;

  function renderStage() {
    switch (stage) {
      case "definition": return <DefinitionStage definition={document.definition} draftId={workspace.shared.draftId} onChange={patch => dispatch({ type: "definition.patch", patch })}/>;
      case "assembly": return <AssemblyStage mates={mates} onConnect={workspace.connectFirstInterfaces} onInherit={workspace.inheritInterface}/>;
      case "interfaces": return <InterfacesStage ports={document.ports} slots={document.slots}/>;
      case "parameters": return <ParametersStage parameters={document.parameters} bindings={document.bindings} onChange={(id, value) => dispatch({ type: "parameter.value", id, value })}/>;
      case "rules": return <RulesStage rules={document.rules} onChange={(id, expression) => dispatch({ type: "rule.expression", id, expression })}/>;
      case "validation": return <ValidationStage checks={checks} revision={document.revision} onValidate={() => workspace.setMessage(checks.some(check => check.status === "fail") ? "基础数据检查未通过，请处理失败项" : "基础数据检查通过；装配求解和工程规则执行仍待接入")}/>;
      case "publish": return <PublishStage checks={checks} revision={document.revision} onExport={workspace.exportDraft}/>;
    }
  }

  return <WorkspaceLayout stage={stage} onStageChange={workspace.setStage} onNew={workspace.newDraft} onExport={workspace.exportDraft} message={workspace.message} revision={document.revision}
    library={<>
      <PublishedTemplateLibrary onPreview={workspace.setPreviewTemplate} onAdd={workspace.addPublishedTemplate} memberPublicationIds={members.map(member => member.template.publicationId)} selectionResetToken={workspace.selectionResetToken}/>
      <div className="panel-title member-title"><span>组件成员</span><small>{members.length}</small></div>
      {members.length === 0 && <p className="hint">暂无成员，点击发布库模板旁的加号加入组件</p>}
      {members.map(member => <div className={`member-row ${workspace.selectedMemberId === member.id ? "selected" : ""}`} key={member.id}>
        <button className="member-select" onClick={() => workspace.setSelectedMemberId(member.id)}><span>{member.role}<small>{member.code} · V{member.template.version}</small></span></button>
        <button className="member-remove" onClick={() => workspace.removeMember(member.id)} aria-label={`删除成员 ${member.role}`}><Trash2 size={14}/></button>
      </div>)}
    </>}
    viewport={<ComponentViewport members={members} mateCount={mates.length} previewTemplate={workspace.previewTemplate}/>}
    actionsDisabled={!workspace.editable}
    inspector={<>
      <section className="inspector-section shared-draft" aria-label="共享草稿">
        <h3>共享草稿</h3>
        <label>当前草稿<select value={workspace.shared.draftId || ""} disabled={!workspace.editable} onChange={event => { void workspace.selectDraft(event.target.value); }}>
          {!workspace.shared.draftId && <option value="">正在读取…</option>}
          {workspace.shared.draftId && !workspace.shared.items.some(item => item.draftId === workspace.shared.draftId) && <option value={workspace.shared.draftId}>{document.definition.name}</option>}
          {workspace.shared.items.map(item => <option key={item.draftId} value={item.draftId}>{item.name || "未命名组件"} · R{item.revision} · {item.draftId.slice(0, 8)}</option>)}
        </select></label>
        <div className="draft-save-status" role="status">{workspace.shared.dirty ? "本页有未保存修改" : workspace.shared.draftId ? `已保存 · R${document.revision} · 网页与 AI 共用` : "正在连接草稿服务"}</div>
        <p className="draft-save-status">同时修改时，你的输入优先；Agent 修改会显示差异。</p>
        <button type="button" className="ghost" disabled={!workspace.shared.draftId} onClick={() => { void workspace.copyDraftLink(); }}>复制草稿链接</button>
        {workspace.shared.error && <div className="draft-sync-error" role="alert"><p>{workspace.shared.error}</p>
          {workspace.shared.status !== "conflict" && <button className="ghost" onClick={workspace.retrySave}>重试</button>}
          {workspace.shared.dirty && <button className="ghost" onClick={workspace.downloadLocalDraft}>下载本地未保存副本</button>}
          {workspace.shared.draftId && <button className="ghost" onClick={workspace.reloadDraft}>{workspace.shared.dirty ? "放弃未保存内容并重新读取" : "重新读取"}</button>}
        </div>}
      </section>
      <AgentChanges shared={workspace.shared} onDismiss={workspace.dismissAgentChanges}/>
      <section className="inspector-section" aria-label="当前阶段面板"><h3>当前阶段 · {STAGES.find(item => item.id === stage)?.label}</h3><fieldset className="stage-fields" disabled={!workspace.editable}>{renderStage()}</fieldset>{stage !== "validation" && stage !== "publish" && <div className="next-hint" role="status">{workspace.message}</div>}</section>
      <section className="inspector-section"><h3>数据摘要</h3><div className="stats"><div><b>{members.length}</b><span>成员</span></div><div><b>{mates.length}</b><span>内部连接</span></div><div><b>{document.ports.length}</b><span>组件接口</span></div></div></section>
    </>}
  />;
}
