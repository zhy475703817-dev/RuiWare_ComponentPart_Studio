import rules from "./hardwareSelectionRules.json" with { type: "json" };
import type { ComponentMember, ComponentPort, GeometryDesign, HardwareItem, InterfaceHardware, Mate, PartInterface } from "./component.ts";
import { inferHardwareCategory } from "./hardware.ts";
import { collectHardwareNodes } from "./hardwareNodes.ts";

export const HARDWARE_SELECTION_VERSION = rules.version;
export const HARDWARE_SCENARIOS = rules.scenarios;
export type HardwareSelectionStatus = "configured" | "not-required" | "pending";
export type HardwareSelectionIssue = { code: string; message: string };
export type HardwareSelectionResult = { status: HardwareSelectionStatus; issues: HardwareSelectionIssue[] };
const placeholder = new RegExp(rules.placeholderPattern, "i");

export function hardwareScenarios(item: PartInterface) {
  // Type metadata suggests ordering only. Users can select any scenario.
  const suggested = (scenario: typeof HARDWARE_SCENARIOS[number]) => scenario.connectionTypeIds.includes(item.connectionTypeId || "");
  return [...HARDWARE_SCENARIOS].sort((a, b) => Number(suggested(b)) - Number(suggested(a)));
}

export function assessInterfaceHardware(item: PartInterface, config: InterfaceHardware | undefined = item.hardware): HardwareSelectionResult {
  const issues: HardwareSelectionIssue[] = [];
  const issue = (code: string, message: string) => issues.push({ code, message });
  if (!config || config.mode === "auto") {
    issue("AUTOMATIC_ONLY", "尚未完成选型：自动推荐仅供参考，请确认实际连接场景及五金清单。");
    return { status: "pending", issues };
  }
  const scenario = HARDWARE_SCENARIOS.find(value => value.id === config.scenarioId);
  if (!scenario) issue("SCENARIO_REQUIRED", "请选择实际连接场景；旧配置需复核，不能自动认定已完成。");
  else {
    if (config.mode !== scenario.mode) issue("SCENARIO_MODE_MISMATCH", "五金模式与选定连接场景不一致。");
    if (scenario.requiresNote && (!config.note?.trim() || placeholder.test(config.note))) issue("SELECTION_NOTE_REQUIRED", "请说明真实选配依据；无需额外五金时注明本体、已有成员或连接机制。");
  }
  const items = config.items || [];
  if (config.mode === "none") {
    if (items.length) issue("NONE_WITH_ITEMS", "无需额外五金的配置不能同时保留采购清单。");
  } else if (config.mode === "manual") {
    if (!items.length || items.length > 50) issue("HARDWARE_ITEMS_REQUIRED", "手动配置须包含 1–50 行真实五金。");
    for (const item of items) {
      const category = item.category?.trim() || inferHardwareCategory(item.name);
      if (!category || ["其他", "五金", "五金件"].includes(category) || placeholder.test(category)
          || !item.name.trim() || item.name.trim() === "五金件" || placeholder.test(item.name)) {
        issue("HARDWARE_TYPE_REQUIRED", "请明确每一行的实际五金类别和名称，不能使用泛称或占位内容。");
      }
      if (!item.specification.trim() || placeholder.test(item.specification) || ["配套", "按需"].includes(item.specification.trim())) {
        issue("HARDWARE_SPECIFICATION_REQUIRED", `${item.name || "五金件"}：请填写真实规格，待定或配套说明不算完成。`);
      }
      if (!Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 10000) issue("HARDWARE_QUANTITY_INVALID", "五金数量须为 1–10000 的整数。");
    }
    const categories = new Set(items.map(item => item.category?.trim() || inferHardwareCategory(item.name)));
    for (const category of scenario?.requiredCategories || []) if (!categories.has(category)) issue("REQUIRED_HARDWARE_MISSING", `当前连接场景必需的五金类别尚未配置：${category}。`);
  } else issue("HARDWARE_MODE_INVALID", "五金模式无效。");
  return { status: issues.length ? "pending" : config.mode === "none" ? "not-required" : "configured", issues };
}

function kitSignature(items: HardwareItem[]) {
  const grouped = new Map<string, number>();
  for (const item of items) {
    const key = JSON.stringify([item.category?.trim() || inferHardwareCategory(item.name), item.name.trim(), item.specification.trim()]);
    grouped.set(key, (grouped.get(key) || 0) + item.quantity);
  }
  return JSON.stringify([...grouped].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0));
}

export function auditHardwareSelection(members: ComponentMember[], mates: Mate[], geometry?: GeometryDesign | null, ports: ComponentPort[] = []) {
  const { nodes, invalid } = collectHardwareNodes(members, mates, geometry, ports, true);
  // Keep port aliases for inherited declarations, but only connected nodes gate completion.
  const entries = nodes.filter(node => node.connected).map(node => {
    const explicit = node.declarations.filter(endpoint => endpoint.item.hardware && endpoint.item.hardware.mode !== "auto");
    const chosen = explicit.length ? explicit : node.declarations;
    const results = chosen.map(endpoint => assessInterfaceHardware(endpoint.item));
    const issues = results.flatMap(result => result.issues);
    const modes = new Set(chosen.map(endpoint => endpoint.item.hardware?.mode || "auto"));
    if (modes.has("manual") && modes.has("none")) issues.push({ code: "SHARED_SELECTION_CONFLICT", message: "同一共用节点同时声明需要五金与无需五金，请统一节点配置。" });
    const kits = new Set(chosen.filter(endpoint => endpoint.item.hardware?.mode === "manual").map(endpoint => kitSignature(endpoint.item.hardware!.items || [])));
    if (kits.size > 1) issues.push({ code: "SHARED_KIT_CONFLICT", message: "同一共用节点各端的五金类别、规格或数量不一致；不能用去重统计替代选型确认。" });
    const status: HardwareSelectionStatus = issues.length ? "pending" : modes.has("manual") ? "configured" : "not-required";
    return { id: node.id, label: [...new Set(node.endpoints.map(endpoint => endpoint.label))].join(" ↔ "), status,
      connected: node.connected, endpoints: node.endpoints.map(({ path, interfaceId, label }) => ({ path, interfaceId, label })),
      issues: [...new Map(issues.map(issue => [issue.code + issue.message, issue])).values()] };
  });
  for (const item of invalid.filter(problem => problem.connected)) entries.push({ id: item.id, label: item.label, status: "pending", connected: true, endpoints: [],
    issues: [{ code: item.code, message: item.code === "NESTING_INVALID" ? "子组件嵌套无效或超过安全深度，五金检查未完成。" : "已连接接口的来源已失效，无法确认五金。" }] });
  const pending = entries.filter(entry => entry.status === "pending").length;
  return { ruleVersion: rules.version, scope: "connected" as const, total: entries.length,
    configured: entries.filter(entry => entry.status === "configured").length,
    notRequired: entries.filter(entry => entry.status === "not-required").length,
    pending, complete: pending === 0, entries };
}
