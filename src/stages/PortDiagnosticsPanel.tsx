import { useState } from "react";
import type { SharedDraftState } from "../app/sharedDraft";

export function PortDiagnosticsPanel({ shared, lastCommand }: { shared: SharedDraftState; lastCommand: unknown }) {
  const [copied, setCopied] = useState(false);
  const data = { draftId: shared.draftId, revision: shared.document.revision, ports: shared.document.ports, portDiagnostics: shared.portDiagnostics, operations: shared.operations, lastCommand };
  return <section className="external-ports" aria-label="对外接口操作与诊断"><h4>接口操作与诊断 · AI 共用数据</h4>
    <p className="hint">R{shared.document.revision} · mm · XYZ 欧拉角为弧度 · Z 向上。日志包含来源、依据、前后差异与失败问题码。</p>
    <button className="ghost" onClick={() => { void navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => setCopied(true)).catch(() => setCopied(false)); }}>{copied ? "已复制诊断数据" : "复制接口诊断 JSON"}</button>
    {lastCommand !== null && <details open><summary>最近接口命令结果</summary><pre>{JSON.stringify(lastCommand, null, 2)}</pre></details>}
    <details><summary>接口、分层检查与版本依据</summary><pre>{JSON.stringify({ ports: data.ports, portDiagnostics: data.portDiagnostics }, null, 2)}</pre></details>
    <details><summary>最近接口操作记录 · {shared.operations?.length || 0}</summary>{shared.operations?.map(op => <details key={op.id}><summary>{op.status === "ok" ? "✓" : "失败"} R{op.revision} · {op.actor === "agent" ? "AI" : "人工"} · {op.action.type}</summary><p>依据：{op.reason || "未提供"}</p>{op.changes?.map((change, index) => <div key={index}><b>{change.label}</b><pre>{JSON.stringify({ before: change.before, after: change.after }, null, 2)}</pre></div>)}<pre>{JSON.stringify(op, null, 2)}</pre></details>)}</details>
  </section>;
}
