import { Copy, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { PublishedTemplateLibrary } from "../library/PublishedTemplateLibrary";
import type { PublishedTemplate } from "../library/publishedLibrary";
import type { InterfaceSelection, ResolvedPartInterface } from "../domain/component";
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
import { compatibleMemberIds } from "../domain/interfaceCompatibility";
import { hasInterfaceSelection, sameInterfaceSelection, supportsInterfacePreview } from "../domain/interfaceGeometry";
import { loadReconstructionInfo } from "../library/templatePreview";
import "../styles/workspace.css";
import "../styles/definition-stage.css";
import "../styles/pattern-stage.css";

export function App() {
  const [draftLink, setDraftLink] = useState("");
  const [dragTemplate, setDragTemplate] = useState<PublishedTemplate | null>(null);
  const [hoveredMemberId, setHoveredMemberId] = useState<string | null>(null);
  const [panelSelectionToken, setPanelSelectionToken] = useState(0);
  const [selectedInterface, setSelectedInterface] = useState<InterfaceSelection | null>(null);
  const [interfacePreview, setInterfacePreview] = useState<{ selection: InterfaceSelection; status: "loading" | "ready" | "error"; resolvedInterfaces?: ResolvedPartInterface[] } | undefined>();
  const interfacePreviewAbort = useRef<AbortController | null>(null);
  const workspace = useComponentWorkspace();
  const { document, dispatch, stage, checks } = workspace;
  const { members, mates } = document.assembly;
  const compatibleIds = compatibleMemberIds(members, workspace.selectedMemberId);
  const clearInterfacePreview = () => { interfacePreviewAbort.current?.abort(); interfacePreviewAbort.current = null; setSelectedInterface(null); setInterfacePreview(undefined); };
  const selectPanelMember = (id: string) => { workspace.setSelectedMemberId(id); clearInterfacePreview(); setPanelSelectionToken(token => token + 1); };
  const selectInterface = (selection: InterfaceSelection) => {
    interfacePreviewAbort.current?.abort();
    interfacePreviewAbort.current = null;
    setSelectedInterface(selection);
    workspace.setSelectedMemberId(selection.memberId);
    setPanelSelectionToken(token => token + 1);
    const member = members.find(item => item.id === selection.memberId);
    const item = member?.interfaces.find(interfaceItem => interfaceItem.id === selection.interfaceId);
    if (!member || !item) { setInterfacePreview(undefined); return; }
    if (supportsInterfacePreview(item, member.resolvedInterfaces)) {
      setInterfacePreview({ selection, status: "ready", resolvedInterfaces: member.resolvedInterfaces });
      return;
    }
    if (member.template.templateKind === "component") {
      setInterfacePreview({ selection, status: "error" });
      return;
    }

    const controller = new AbortController();
    interfacePreviewAbort.current = controller;
    setInterfacePreview({ selection, status: "loading" });
    const template: PublishedTemplate = {
      templateKind: "part", publicationId: member.template.publicationId, templateId: member.template.templateId,
      version: member.template.version, sha256: member.template.sha256, code: member.code, name: member.name,
      createdAt: new Date(0).toISOString(), size: 0, downloadUrl: "",
    };
    void loadReconstructionInfo(template, controller.signal, member.parameterValues).then(info => {
      if (controller.signal.aborted) return;
      setInterfacePreview(current => sameInterfaceSelection(current?.selection || null, selection)
        ? { selection, status: "ready", resolvedInterfaces: info.resolvedInterfaces }
        : current);
    }).catch(() => {
      if (controller.signal.aborted) return;
      setInterfacePreview(current => sameInterfaceSelection(current?.selection || null, selection)
        ? { selection, status: "error" }
        : current);
    });
  };
  const selectViewportMember = (id: string | null) => { workspace.setSelectedMemberId(id); clearInterfacePreview(); setPanelSelectionToken(token => token + 1); };

  useEffect(() => {
    if (selectedInterface && !hasInterfaceSelection(members, selectedInterface)) clearInterfacePreview();
  }, [members, selectedInterface]);

  useEffect(() => { clearInterfacePreview(); }, [workspace.shared.draftId]);
  useEffect(() => () => interfacePreviewAbort.current?.abort(), []);

  function renderStage() {
    switch (stage) {
      case "definition": return <DefinitionStage definition={document.definition} members={members} selectedMemberId={workspace.selectedMemberId} hoveredMemberId={hoveredMemberId} compatibleMemberIds={compatibleIds} onSelectMember={selectPanelMember} onHoverMember={setHoveredMemberId} onChange={patch => dispatch({ type: "definition.patch", patch })}/>;
      case "assembly": return <AssemblyStage mates={mates} onConnect={workspace.connectFirstInterfaces} onInherit={workspace.inheritInterface}/>;
      case "interfaces": return <InterfacesStage members={members} ports={document.ports} slots={document.slots} selectedInterface={selectedInterface} interfacePreview={interfacePreview} onSelectInterface={selectInterface}/>;
      case "parameters": return <ParametersStage parameters={document.parameters} bindings={document.bindings} patterns={document.patterns} rules={document.rules} members={members} definitionName={document.definition.name} definitionCode={document.definition.code} onChange={(id, value) => dispatch({ type: "parameter.value", id, value })} onBindingChange={(id, binding) => dispatch({ type: "binding.set", id, binding })} onAddParameter={parameter => dispatch({ type: "parameter.add", parameter })} onRemoveParameter={id => dispatch({ type: "parameter.remove", id })} onAddPattern={pattern => dispatch({ type: "pattern.add", pattern })} onRemovePattern={id => dispatch({ type: "pattern.remove", id })} onAddRule={rule => dispatch({ type: "rule.add", rule })} onRemoveRule={id => dispatch({ type: "rule.remove", id })}/>;
      case "rules": return <RulesStage rules={document.rules} onChange={(id, expression) => dispatch({ type: "rule.expression", id, expression })}/>;
      case "validation": return <ValidationStage checks={checks} revision={document.revision} onValidate={() => workspace.setMessage(checks.some(check => check.status === "fail") ? "基础数据检查未通过，请处理失败项" : "基础数据检查通过；装配求解和工程规则执行仍待接入")}/>;
      case "publish": return <PublishStage checks={checks} revision={document.revision} onExport={workspace.exportDraft} onPublish={workspace.publishDraft} publishing={workspace.publishing}/>;
    }
  }

  return <WorkspaceLayout stage={stage} onStageChange={workspace.setStage} onNew={workspace.newDraft} onExport={workspace.exportDraft} message={workspace.message} revision={document.revision}
    library={<>
      <PublishedTemplateLibrary onPreview={workspace.setPreviewTemplate} onAdd={workspace.addPublishedTemplate} onDragTemplate={setDragTemplate} memberPublicationIds={members.map(member => member.template.publicationId)} selectionResetToken={workspace.selectionResetToken} refreshToken={workspace.libraryRefreshToken} addDisabled={!workspace.editable}/>
      <div className="panel-title member-title"><span>组件成员</span><small>{members.length}</small></div>
      {members.length === 0 && <p className="hint">暂无成员，点击发布库模板旁的加号或从发布详情加入组件。</p>}
      {members.map(member => <div className={`member-row ${workspace.selectedMemberId === member.id ? "selected" : ""} ${hoveredMemberId === member.id ? "hovered" : ""} ${compatibleIds.has(member.id) ? "compatible-candidate" : ""}`} key={member.id} onMouseEnter={() => setHoveredMemberId(member.id)} onMouseLeave={() => setHoveredMemberId(null)} title={member.name}>
        <button className="member-select" onClick={() => selectPanelMember(member.id)}><span>{member.role}<small>{member.code} · V{member.template.version}</small></span></button>
        <button className="member-remove" onClick={() => workspace.copyMember(member.id)} aria-label={`复制成员 ${member.role}`} title="复制成员"><Copy size={14}/></button>
        <button className="member-remove" onClick={() => workspace.removeMember(member.id)} aria-label={`删除成员 ${member.role}`}><Trash2 size={14}/></button>
      </div>)}
    </>}
    viewport={<ComponentViewport
      members={members}
       parameters={document.parameters}
       bindings={document.bindings}
       patterns={document.patterns}
      mateCount={mates.length}
      previewTemplate={workspace.previewTemplate}
      selectedMemberId={workspace.selectedMemberId}
      selectedInterface={selectedInterface}
      selectedInterfaceResolved={sameInterfaceSelection(interfacePreview?.selection || null, selectedInterface) && interfacePreview?.status === "ready" ? interfacePreview.resolvedInterfaces : undefined}
      panelSelectionToken={panelSelectionToken}
      onSelectMember={selectViewportMember}
      onHoverMember={setHoveredMemberId}
      compatibleMemberIds={compatibleIds}
      onMoveMember={workspace.moveMember}
      onCopyMember={workspace.copyMember}
      onRemoveMember={workspace.removeMember}
      onDropTemplate={() => {
        if (dragTemplate) void workspace.addPublishedTemplate(dragTemplate);
        setDragTemplate(null);
      }}
    />}
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
        <form className="draft-link-entry" onSubmit={event => { event.preventDefault(); void workspace.openDraftLink(draftLink); }}>
          <label>粘贴草稿链接<input value={draftLink} onChange={event => setDraftLink(event.target.value)} placeholder="https://…?draft=…"/></label>
          <button type="submit" className="ghost" disabled={!draftLink.trim() || !workspace.editable}>打开</button>
        </form>
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
