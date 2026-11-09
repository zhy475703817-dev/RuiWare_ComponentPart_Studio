export const STAGES = [
  { id: "definition", label: "定义" },
  { id: "assembly", label: "装配" },
  { id: "interfaces", label: "接口与槽位" },
  { id: "parameters", label: "参数" },
  { id: "rules", label: "规则" },
  { id: "validation", label: "验证" },
  { id: "publish", label: "发布" },
] as const;
export type StageId = typeof STAGES[number]["id"];
