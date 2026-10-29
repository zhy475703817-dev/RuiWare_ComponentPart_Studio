import { createComponentDocument } from "../domain/component.ts";
import type { ComponentDocument } from "../domain/component.ts";
import { componentReducer } from "../domain/componentReducer.ts";
import type { ComponentAction } from "../domain/componentReducer.ts";
import { inferEnvelopeDimensions } from "../domain/parameterMetrics.ts";
import { validateComponent } from "../domain/validation.ts";
import type { ValidationCheck } from "../domain/validation.ts";

export type DraftChange = {
  actor: "agent" | "user"; fromRevision: number; revision: number; createdAt: string;
  items: { path: string[]; label: string; kind: "added" | "removed" | "updated"; before: unknown; after: unknown }[];
};
export type DraftEnvelope = { draftId: string; document: ComponentDocument; checks: ValidationCheck[];
  changes?: DraftChange[]; changesTruncated?: boolean; reconciled?: boolean };
export type DraftSummary = { draftId: string; name: string; revision: number; updatedAt: string };
export type DraftApi = {
  list: () => Promise<{ items: DraftSummary[] }>;
  create: () => Promise<DraftEnvelope>;
  get: (id: string, afterRevision?: number) => Promise<DraftEnvelope>;
  apply: (id: string, revision: number, action: ComponentAction) => Promise<DraftEnvelope>;
  export: (id: string) => Promise<unknown>;
};
export type SharedDraftState = {
  draftId: string | null; document: ComponentDocument; checks: ValidationCheck[]; items: DraftSummary[];
  status: "loading" | "ready" | "saving" | "error" | "conflict"; dirty: boolean; error: string | null; notice: string;
  agentChanges: DraftChange[]; changesTruncated: boolean; userPriority: boolean;
};

export function initialSharedState(): SharedDraftState {
  return { draftId: null, document: createComponentDocument(), checks: [], items: [], status: "loading", dirty: false, error: null, notice: "",
    agentChanges: [], changesTruncated: false, userPriority: false };
}

const errorCode = (error: unknown): string => (error as { code?: string })?.code || "";
const errorMessage = (error: unknown): string => errorCode(error) === "REVISION_CONFLICT"
  ? "其他网页也修改了草稿，或变更历史不完整。本页输入已保留，请下载本地副本并核对最新版本。"
  : errorCode(error) === "EDIT_TARGET_REMOVED" ? "Agent 已删除正在编辑的对象或其引用，无法直接合并。本页输入已保留，请下载本地副本后核对差异。"
  : errorCode(error) === "EDIT_STRUCTURE_CONFLICT" ? "Agent 修改了相关结构，无法直接合并。本页输入已保留，请下载本地副本后核对差异。"
  : errorCode(error) === "DRAFT_NOT_FOUND" ? "该草稿已不存在，请选择其他草稿。" : "草稿操作失败。未保存输入已保留，请检查连接后重试。";

const legacyDimensionSpecs = [
  { id: "componentLength", label: "组件总长", min: 1000, max: 6000, fallback: 2400, axis: "length" as const, aliases: ["componentLength", "length", "overallLength", "总长", "长度"] },
  { id: "componentWidth", label: "组件总宽", min: 100, max: 3000, fallback: 600, axis: "width" as const, aliases: ["componentWidth", "width", "overallWidth", "总宽", "宽度"] },
  { id: "componentHeight", label: "组件总高", min: 300, max: 8000, fallback: 1800, axis: "height" as const, aliases: ["componentHeight", "height", "overallHeight", "总高", "高度"] },
] as const;

function normalizeDraftDocument(document: ComponentDocument): ComponentDocument {
  const members = Array.isArray(document.assembly?.members) ? document.assembly.members : [];
  const patterns = Array.isArray(document.patterns) ? document.patterns : [];
  const sourceParameters = Array.isArray(document.parameters) ? document.parameters : [];
  const inferred = inferEnvelopeDimensions(sourceParameters, members, patterns);
  const parameters = [...sourceParameters];
  const dimensionParameters = legacyDimensionSpecs.map(spec => {
    const existing = parameters.find(parameter => spec.aliases.some(alias => parameter.id?.toLowerCase() === alias.toLowerCase() || parameter.label?.toLowerCase() === alias.toLowerCase()));
    const inferredValue = inferred[spec.axis];
    const value = typeof existing?.value === "number" && Number.isFinite(existing.value) ? existing.value : Math.min(spec.max, Math.max(spec.min, inferredValue ?? spec.fallback));
    if (existing) {
      const index = parameters.indexOf(existing);
      parameters[index] = { ...existing, unit: existing.unit || "mm", min: Number.isFinite(existing.min) ? existing.min : spec.min, max: Number.isFinite(existing.max) ? existing.max : spec.max, value };
      return parameters[index];
    }
    const parameter = { id: spec.id, label: spec.label, value, unit: "mm", min: spec.min, max: spec.max };
    parameters.push(parameter);
    return parameter;
  });
  const bindings = Array.isArray(document.bindings) ? [...document.bindings] : [];
  for (const member of members) {
    for (const spec of legacyDimensionSpecs) {
      const definition = member.parameterDefinitions?.find(item => spec.aliases.some(alias => item.id?.toLowerCase() === alias.toLowerCase() || item.label?.toLowerCase() === alias.toLowerCase()) && item.unit === "mm" && ["number", "integer"].includes(item.valueType));
      const parameter = dimensionParameters.find(item => item.id === spec.id || spec.aliases.some(alias => item.id?.toLowerCase() === alias.toLowerCase() || item.label?.toLowerCase() === alias.toLowerCase()));
      const lowerBound = Math.max(parameter?.min ?? Number.POSITIVE_INFINITY, definition?.minimum ?? Number.POSITIVE_INFINITY);
      const upperBound = Math.min(parameter?.max ?? Number.NEGATIVE_INFINITY, definition?.maximum ?? Number.NEGATIVE_INFINITY);
      if (!definition || !parameter || lowerBound > upperBound || bindings.some(binding => binding.parameterId === parameter.id && binding.memberId === member.id && binding.memberParameterId === definition.id)) continue;
      bindings.push({ id: `${member.id}:${parameter.id}:${definition.id}`, parameterId: parameter.id, memberId: member.id, memberParameterId: definition.id });
    }
  }
  return { ...document, parameters, bindings };
}

function draftMigrationActions(document: ComponentDocument): ComponentAction[] {
  const normalized = normalizeDraftDocument(document);
  const parameterIds = new Set(document.parameters.map(parameter => parameter.id));
  const bindingIds = new Set(document.bindings.map(binding => binding.id));
  const valueMigrations = normalized.parameters
    .filter(parameter => {
      const existing = document.parameters.find(item => item.id === parameter.id);
      return existing && existing.value !== parameter.value && parameter.value !== null;
    })
    .map(parameter => ({ type: "parameter.value" as const, id: parameter.id, value: parameter.value }));
  return [
    ...normalized.parameters
      .filter(parameter => !parameterIds.has(parameter.id))
      .map(parameter => ({ type: "parameter.add" as const, parameter })),
    ...valueMigrations,
    ...normalized.bindings
      .filter(binding => !bindingIds.has(binding.id))
      .map(binding => ({ type: "binding.set" as const, id: binding.id, binding })),
  ];
}

function actionIdentity(action: ComponentAction): string {
  return JSON.stringify(action);
}

// The server owns persisted data/checks. The reducer only projects queued input so typing stays responsive.
export class SharedDraftController {
  private api: DraftApi;
  private state = initialSharedState();
  private confirmed: DraftEnvelope | null = null;
  private pending: ComponentAction[] = [];
  private listeners = new Set<() => void>();
  private saving: Promise<void> | null = null;
  private generation = 0;
  private editGeneration = 0;
  private disposed = false;
  private polling = false;
  private preferredId: string | null = null;

  constructor(api: DraftApi) { this.api = api; }
  getSnapshot = (): SharedDraftState => this.state;
  subscribe = (listener: () => void): (() => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  dispose() { this.disposed = true; this.generation++; this.listeners.clear(); }
  private publish(patch: Partial<SharedDraftState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(listener => listener());
  }
  private accept(draft: DraftEnvelope) {
    const migrationActions = draftMigrationActions(draft.document);
    const queuedActions = new Set(this.pending.map(actionIdentity));
    this.pending = [
      ...migrationActions.filter(action => !queuedActions.has(actionIdentity(action))),
      ...this.pending,
    ];
    if (this.confirmed?.draftId === draft.draftId) this.recordChanges(draft);
    else this.publish({ agentChanges: [], changesTruncated: false, userPriority: false });
    this.confirmed = draft;
    let projection = draft.document;
    for (const action of this.pending) {
      const next = componentReducer(projection, action);
      const missingTarget = ((action.type === "member.patch" || action.type === "member.remove") && !projection.assembly.members.some(item => item.id === action.id))
        || (action.type === "parameter.value" && !projection.parameters.some(item => item.id === action.id))
        || (action.type === "rule.expression" && !projection.rules.some(item => item.id === action.id));
      const invalidAddition = ["member.add", "mate.add", "port.add"].includes(action.type) && next === projection;
      if (missingTarget || invalidAddition) {
        // Keep the full visible local document for download; reprojecting would silently drop queued additions.
        this.publish({ status: "conflict", dirty: true, error: "Agent 修改影响了后续操作的对象或引用，无法直接合并。本页完整输入已保留，请下载本地副本后核对差异。" });
        return;
      }
      projection = next;
    }
    this.project({ status: this.pending.length ? "saving" : "ready", error: null, notice: "", items: this.reconcileItems(this.state.items) });
  }
  private recordChanges(draft: DraftEnvelope) {
    const after = this.confirmed?.document.revision ?? draft.document.revision;
    const changes = (draft.changes || []).filter(change => change.actor === "agent" && change.revision > after);
    const combined = [...new Map([...this.state.agentChanges, ...changes].map(change => [change.revision, change])).values()];
    this.publish({ agentChanges: combined.slice(-50),
      changesTruncated: this.state.changesTruncated || !!draft.changesTruncated || combined.length > 50,
      userPriority: this.state.userPriority || !!draft.reconciled });
  }
  dismissAgentChanges() { this.publish({ agentChanges: [], changesTruncated: false, userPriority: false }); }
  private reconcileItems(items: DraftSummary[]): DraftSummary[] {
    const draft = this.confirmed;
    return items.map(item => draft && item.draftId === draft.draftId && item.revision <= draft.document.revision
      ? { ...item, name: draft.document.definition.name, revision: draft.document.revision } : item);
  }
  private project(patch: Partial<SharedDraftState> = {}) {
    if (!this.confirmed) return;
    const document = this.pending.reduce(componentReducer, this.confirmed.document);
    this.publish({ draftId: this.confirmed.draftId, document,
      checks: this.pending.length ? validateComponent(document) : this.confirmed.checks,
      dirty: this.pending.length > 0, ...patch });
  }

  async start(preferredId?: string | null) {
    this.preferredId = preferredId ?? this.preferredId;
    const generation = ++this.generation;
    this.publish({ status: "loading", error: null });
    try {
      const { items } = await this.api.list();
      if (this.disposed || generation !== this.generation) return;
      this.publish({ items });
      const id = this.preferredId || items[0]?.draftId;
      let draft: DraftEnvelope;
      let notice = "";
      try { draft = id ? await this.api.get(id) : await this.api.create(); }
      catch (error) {
        if (errorCode(error) !== "DRAFT_NOT_FOUND") throw error;
        const fallback = items.find(item => item.draftId !== id);
        draft = fallback ? await this.api.get(fallback.draftId) : await this.api.create();
        notice = "原草稿已不存在，已打开可用草稿。";
      }
      if (this.disposed || generation !== this.generation) return;
      this.accept(draft);
      this.beginSave();
      this.publish({ notice });
      await this.refreshList();
    } catch (error) {
      if (generation === this.generation) this.publish({ status: "error", error: errorMessage(error) });
    }
  }

  dispatch(action: ComponentAction): boolean {
    if (!this.confirmed || !["ready", "saving"].includes(this.state.status) || this.disposed) return false;
    if (componentReducer(this.state.document, action) === this.state.document) return true;
    this.pending.push(structuredClone(action));
    this.editGeneration++;
    this.project({ status: "saving", error: null, notice: "" });
    this.beginSave();
    return true;
  }
  private beginSave() {
    if (this.saving || !this.pending.length || this.disposed) return;
    this.saving = this.save().finally(() => { this.saving = null; });
  }
  private async save() {
    while (this.pending.length && this.confirmed && !this.disposed) {
      try {
        const result = await this.api.apply(this.confirmed.draftId, this.confirmed.document.revision, this.pending[0]);
        if (this.disposed) return;
        this.pending.shift();
        this.accept(result);
        if (this.state.status === "conflict") return;
      } catch (error) {
        const current = (error as { current?: DraftEnvelope })?.current;
        if (current?.draftId === this.confirmed.draftId) this.recordChanges(current);
        const conflict = ["REVISION_CONFLICT", "EDIT_TARGET_REMOVED", "EDIT_STRUCTURE_CONFLICT"].includes(errorCode(error));
        this.publish({ status: conflict ? "conflict" : "error", error: errorMessage(error) });
        return;
      }
    }
  }
  async flush() {
    while (this.saving) await this.saving;
    if (this.pending.length || !this.confirmed || ["error", "conflict"].includes(this.state.status)) throw new Error(this.state.error || "草稿尚未就绪");
  }
  async retry() {
    if (this.state.status === "conflict") throw new Error(this.state.error || "草稿修订冲突");
    if (!this.confirmed) return this.start();
    this.publish({ status: "saving", error: null });
    this.beginSave();
    await this.flush();
    if (!this.pending.length) this.project({ status: "ready", error: null });
  }
  async reload() {
    if (!this.confirmed) return this.start();
    if (this.saving) await this.saving;
    const generation = ++this.generation;
    this.publish({ status: "loading" });
    try {
      const draft = await this.api.get(this.confirmed.draftId);
      if (this.disposed || generation !== this.generation) return;
      this.pending = [];
      this.accept(draft);
    } catch (error) { this.publish({ status: "error", error: errorMessage(error) }); }
  }
  async select(id: string) { await this.change(() => this.api.get(id)); }
  async create() { await this.change(() => this.api.create()); }
  private async change(load: () => Promise<DraftEnvelope>) {
    await this.flush();
    const generation = ++this.generation;
    this.publish({ status: "loading", error: null });
    try {
      const draft = await load();
      if (this.disposed || generation !== this.generation) return;
      this.accept(draft);
      await this.refreshList();
    } catch (error) {
      if (generation === this.generation) this.publish({ status: "error", error: errorMessage(error) });
    }
  }
  private async refreshList() {
    try { const { items } = await this.api.list(); this.publish({ items: this.reconcileItems(items) }); }
    catch { this.publish({ notice: "草稿列表刷新失败，稍后自动重试。" }); }
  }
  async poll() {
    if (this.disposed || this.polling) return;
    this.polling = true;
    const generation = this.generation;
    const editGeneration = this.editGeneration;
    try {
      if (this.confirmed && !this.pending.length && this.state.status === "ready") {
        const draft = await this.api.get(this.confirmed.draftId, this.confirmed.document.revision);
        if (!this.disposed && generation === this.generation && editGeneration === this.editGeneration
            && !this.pending.length && this.state.status === "ready" && draft.document.revision >= this.confirmed.document.revision) this.accept(draft);
      }
      if (!this.disposed) await this.refreshList();
    } catch { this.publish({ notice: "暂时无法同步最新草稿，请检查连接；本页输入不会被覆盖。" }); }
    finally { this.polling = false; }
  }
  async export() {
    await this.flush();
    return this.api.export(this.confirmed!.draftId);
  }
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/component-drafts${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal: AbortSignal.timeout(20000),
    headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error("草稿请求失败"), { code: data?.error?.code || "INVALID_REQUEST", current: data?.error?.current });
  return data as T;
}
export const httpDraftApi: DraftApi = {
  list: () => request(""), create: () => request("", {}),
  get: (id, afterRevision) => request(`/${encodeURIComponent(id)}${afterRevision === undefined ? "" : `?afterRevision=${afterRevision}`}`),
  apply: (id, expectedRevision, action) => request(`/${encodeURIComponent(id)}/actions`, { expectedRevision, action }),
  export: id => request(`/${encodeURIComponent(id)}/export`),
};
