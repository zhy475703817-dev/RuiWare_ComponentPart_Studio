import type { SharedDraftState } from "./sharedDraft";

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "无";
  if (value === "") return "（空）";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function AgentChanges({ shared, onDismiss }: { shared: SharedDraftState; onDismiss: () => void }) {
  if (!shared.agentChanges.length) return null;
  return <section className="agent-changes" aria-label="Agent 修改差异">
    <div className="agent-change-heading"><strong role="status">{shared.status === "conflict" ? "Agent 修改待核对" : "Agent 修改已同步"}</strong>
      <button type="button" className="ghost" onClick={onDismiss} aria-label="关闭 Agent 修改提示">关闭</button></div>
    <p>{shared.dirty ? "本页未保存输入已保留，请核对下列差异。" : shared.userPriority ? "已优先保存你的修改；Agent 对其他字段的修改已保留。" : "以下为 Agent 的修改记录，当前值以工作台为准。"}</p>
    {shared.changesTruncated && <p>仅展示最近的修改记录，更早的差异未在此展开。</p>}
    <div className="agent-change-list">{shared.agentChanges.map(change => <details key={change.revision} open>
      <summary>Agent · R{change.fromRevision} → R{change.revision}<time dateTime={change.createdAt}>{new Date(change.createdAt).toLocaleTimeString("zh-CN")}</time></summary>
      {change.items.map((item, index) => <div className="agent-change-item" key={index}>
        <b>{item.label}</b><small>{item.kind === "added" ? "新增" : item.kind === "removed" ? "删除" : "修改"}</small>
        <div className="agent-change-values"><div><span>修改前</span><pre>{formatValue(item.before)}</pre></div>
          <div><span>修改后</span><pre>{formatValue(item.after)}</pre></div></div>
      </div>)}
    </details>)}</div>
  </section>;
}
