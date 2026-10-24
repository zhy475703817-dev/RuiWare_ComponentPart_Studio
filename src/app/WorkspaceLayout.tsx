import type { ReactNode } from "react";
import { Download } from "lucide-react";
import { STAGES } from "./stages";
import type { StageId } from "./stages";

type Props = {
  stage: StageId;
  onStageChange: (stage: StageId) => void;
  onNew: () => void;
  onExport: () => void;
  library: ReactNode;
  viewport: ReactNode;
  inspector: ReactNode;
  message: string;
  revision: number;
  actionsDisabled?: boolean;
};

export function WorkspaceLayout({ stage, onStageChange, onNew, onExport, library, viewport, inspector, message, revision, actionsDisabled }: Props) {
  return <div className="app">
    <header className="topbar"><div className="brand"><div className="brand-mark">R</div><div><b>组件模板生成器</b><span>Component Template Studio · 第一版</span></div></div><div className="top-actions"><button className="ghost" disabled={actionsDisabled} onClick={onNew}>新建</button><button className="primary" disabled={actionsDisabled} onClick={onExport}><Download size={16}/>导出模板</button></div></header>
    <nav className="stagebar" aria-label="组件模板七阶段">{STAGES.map((item, index) => <button key={item.id} className={item.id === stage ? "active" : ""} aria-current={item.id === stage ? "step" : undefined} onClick={() => onStageChange(item.id)}><span>{index + 1}</span>{item.label}</button>)}</nav>
    <main className="layout"><aside className="sidebar library">{library}</aside>{viewport}<aside className="sidebar inspector">{inspector}</aside></main>
    <footer className="statusbar"><span><i className="status-dot"/>{message}</span><span>当前编辑：组件模板草稿 R{revision}</span></footer>
  </div>;
}
