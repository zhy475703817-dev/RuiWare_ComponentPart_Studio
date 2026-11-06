import { Settings2 } from "lucide-react";
import type { ComponentRule } from "../domain/component";

export function RulesStage({ rules, onChange }: { rules: ComponentRule[]; onChange: (id: string, expression: string) => void }) {
  return <>
    {rules.map(rule => <label key={rule.id}>{rule.label || ((rule.kind || "constraint") === "composition" ? "组件间构成规则" : "工程限制")}<textarea value={rule.expression} onChange={event => onChange(rule.id, event.target.value)}/></label>)}
    <div className="rule-line"><Settings2 size={14}/>当前仅保存规则说明，规则执行尚未接入</div>
  </>;
}
