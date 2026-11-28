import { createComponentDocument, hasInterface } from "../domain/component.ts";
import type { ComponentDocument, EvidenceFile } from "../domain/component.ts";
import { componentReducer } from "../domain/componentReducer.ts";
import type { ComponentAction } from "../domain/componentReducer.ts";
import { captureModel } from "../domain/assemblyGroups.ts";
import { inferEnvelopeDimensions } from "../domain/parameterMetrics.ts";
import { validateComponent } from "../domain/validation.ts";
import type { ValidationCheck } from "../domain/validation.ts";

export type DraftChange = {
  actor: "agent" | "user"; fromRevision: number; revision: number; createdAt: string;
  items: { path: string[]; label: string; kind: "added" | "removed" | "updated"; before: unknown; after: unknown }[];
};
export type DraftEnvelope = { draftId: string; document: ComponentDocument; checks: ValidationCheck[];
  changes?: DraftChange[]; changesTruncated?: boolean; reconciled?: boolean };
export type DraftSummary = { draftId: string; name: string; code: string; revision: number; updatedAt: string };
export type DraftApi = {
  list: () => Promise<{ items: DraftSummary[] }>;
  create: () => Promise<DraftEnvelope>;
  get: (id: string, afterRevision?: number) => Promise<DraftEnvelope>;
  apply: (id: string, revision: number, action: ComponentAction) => Promise<DraftEnvelope>;
  applyBatch: (id: string, revision: number, actions: ComponentAction[]) => Promise<DraftEnvelope>;
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
  : errorCode(error) === "GEOMETRY_RESIZE_REQUIRED" ? "该尺寸由几何联动管理，请在参数阶段预览并应用目标尺寸。"
  : errorCode(error) === "EDIT_TARGET_REMOVED" ? "Agent 已删除正在编辑的对象或其引用，无法直接合并。本页输入已保留，请下载本地副本后核对差异。"
  : errorCode(error) === "EDIT_STRUCTURE_CONFLICT" ? "Agent 修改了相关结构，无法直接合并。本页输入已保留，请下载本地副本后核对差异。"
  : errorCode(error) === "INVALID_PORT_NAME" ? "对外接口名称不能为空。"
  : errorCode(error) === "INVALID_SLOT_NAME" ? "槽位名称不能为空。"
  : errorCode(error) === "INVALID_SLOT_REFERENCE" ? "槽位接口引用无效，请重新选择基准接口或占用接口。"
  : errorCode(error) === "INCOMPATIBLE_SLOT_INTERFACE" ? "占用接口与槽位基准接口不兼容。"
  : errorCode(error) === "DUPLICATE_SLOT_INTERFACE" ? "该接口已被其他槽位占用。"
  : errorCode(error) === "INVALID_INTERFACE_TYPE" ? "接口类型无效，请重新选择或输入类型名称。"
  : errorCode(error) === "INTERFACE_TYPE_LIMIT" ? "自定义接口类型已达到数量上限。"
  : errorCode(error) === "INCOMPATIBLE_INTERFACE" ? "两个接口不兼容，请检查接口类型、几何形状、尺寸和连接方式。"
  : errorCode(error) === "INTERFACE_REQUIRES_REVIEW" ? "接口缺少完整兼容契约，暂不能连接。请先补充接口类型、尺寸或连接方式。"
  : errorCode(error) === "INTERFACE_CAPACITY" || errorCode(error) === "ENDPOINT_ALREADY_CONSUMED" ? "该接口已达到可连接次数上限。"
  : errorCode(error) === "DUPLICATE_CONNECTION" ? "该连接已存在。"
  : errorCode(error) === "INVALID_REFERENCE" ? "所选接口已变化，请重新选择。"
  : errorCode(error) === "DRAFT_NOT_FOUND" ? "该草稿已不存在，请选择其他草稿。" : "草稿操作失败。未保存输入已保留，请检查连接后重试。";

// These errors mean the server understood and rejected the requested edit.
// They should not freeze the whole editor or leave a doomed action at the
// front of the queue; transport failures and revision conflicts still keep
// the pending input for an explicit retry/reload.
const rejectedEditCodes = new Set([
  "GEOMETRY_RESIZE_REQUIRED",
  "INCOMPATIBLE_INTERFACE", "INTERFACE_REQUIRES_REVIEW", "ENDPOINT_ALREADY_CONSUMED", "DUPLICATE_CONNECTION", "INVALID_REFERENCE",
  "DUPLICATE_ID", "DUPLICATE_PORT", "INTERFACE_SNAPSHOT_MISMATCH",
  "INVALID_PORT_NAME", "INVALID_SLOT_NAME", "INVALID_SLOT_REFERENCE",
  "INCOMPATIBLE_SLOT_INTERFACE", "DUPLICATE_SLOT_INTERFACE", "INVALID_INTERFACE_TYPE", "INTERFACE_TYPE_LIMIT",
]);

const legacyDimensionSpecs = [
  { id: "componentLength", label: "组件总长", min: 1000, max: 6000, fallback: 2400, axis: "length" as const, aliases: ["componentLength", "length", "overallLength", "总长", "长度"] },
  { id: "componentWidth", label: "组件总宽", min: 100, max: 3000, fallback: 600, axis: "width" as const, aliases: ["componentWidth", "width", "overallWidth", "总宽", "宽度"] },
  { id: "componentHeight", label: "组件总高", min: 300, max: 8000, fallback: 1800, axis: "height" as const, aliases: ["componentHeight", "height", "overallHeight", "总高", "高度"] },
] as const;

function normalizeDraftDocument(document: ComponentDocument): ComponentDocument {
  if (document.geometryDesign) return document;
  const interfaceTypes = Array.isArray(document.interfaceTypes) ? document.interfaceTypes : [];
  const members = Array.isArray(document.assembly?.members) ? document.assembly.members : [];
  const patterns = Array.isArray(document.patterns) ? document.patterns : [];
  const slots = (Array.isArray(document.slots) ? document.slots : []).map(slot => ({
    ...slot,
    baseInterface: slot.baseInterface ?? null,
    interfaceId: slot.interfaceId ?? null,
  }));
  const sourceParameters = Array.isArray(document.parameters) ? document.parameters : [];
  const inferred = inferEnvelopeDimensions(sourceParameters, members, patterns);
  const parameters = [...sourceParameters];
  legacyDimensionSpecs.map(spec => {
    const existing = parameters.find(parameter => spec.aliases.some(alias => parameter.id?.toLowerCase().includes(alias.toLowerCase()) || parameter.label?.toLowerCase().includes(alias.toLowerCase())));
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
  return { ...document, parameters, bindings, slots, interfaceTypes };
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

type PendingEdit = {
  actions: ComponentAction[];
  batch?: boolean;
  modelBase?: string;
  ancillaryBase?: string;
  onSaved?: (draft: DraftEnvelope) => void;
  resolve?: (draft: DraftEnvelope) => void;
  reject?: (error: unknown) => void;
};

function stableKey(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => item !== null && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
}

function ancillaryKey(document: ComponentDocument): string {
  return stableKey([document.parameters, document.interfaceTypes ?? []]);
}

function modelBase(document: ComponentDocument, actions: ComponentAction[]): Partial<PendingEdit> {
  if (!actions.some(action => action.type === "model.restore")) return {};
  return { modelBase: stableKey(captureModel(document)),
    ...(actions.some(action => action.type === "model.restore" && (action.model.parameters || action.model.interfaceTypes)) ? { ancillaryBase: ancillaryKey(document) } : {}) };
}

function projectEdit(document: ComponentDocument, edit: PendingEdit): ComponentDocument {
  if (edit.modelBase !== undefined && edit.modelBase !== stableKey(captureModel(document))) {
    throw new Error("模型快照依赖的编辑已变化，请保留本地副本后核对。");
  }
  if (edit.ancillaryBase !== undefined && edit.ancillaryBase !== ancillaryKey(document)) {
    throw new Error("撤销依赖的参数或接口类型已变化，请核对后重试。");
  }
  let result = document;
  for (const action of edit.actions) {
    const next = componentReducer(result, action);
    const missingTarget = ((action.type === "member.patch" || action.type === "member.remove") && !result.assembly.members.some(item => item.id === action.id))
      || ((action.type === "interface.type.set" || action.type === "interface.connectable.set") && !result.assembly.members.some(member => member.id === action.memberId && member.interfaces.some(item => item.id === action.interfaceId)))
      || (action.type === "interface.hardware.set" && !result.assembly.members.some(member => member.id === action.memberId && member.interfaces.some(item => item.id === action.interfaceId)))
      || ((action.type === "port.remove" || action.type === "port.rename") && !result.ports.some(item => item.id === action.id))
      || ((action.type === "slot.patch" || action.type === "slot.remove") && !result.slots.some(item => item.id === action.id))
      || (action.type === "slot.patch" && (
        (action.patch.baseInterface != null && !hasInterface(result, action.patch.baseInterface))
        || (action.patch.memberId != null && !result.assembly.members.some(member => member.id === action.patch.memberId))
        || (action.patch.interfaceId != null && !hasInterface(result, {
          memberId: action.patch.memberId ?? result.slots.find(slot => slot.id === action.id)?.memberId ?? "",
          interfaceId: action.patch.interfaceId,
        }))))
      || (action.type === "parameter.value" && !result.parameters.some(item => item.id === action.id))
      || (action.type === "rule.expression" && !result.rules.some(item => item.id === action.id))
      || (action.type === "rule.remove" && !result.rules.some(item => item.id === action.id))
      || (action.type === "binding.set" && action.binding !== null && (
        !result.assembly.members.some(member => member.id === action.binding!.memberId)
        || !result.parameters.some(parameter => parameter.id === action.binding!.parameterId)))
      || (action.type === "pattern.add" && (
        !action.pattern.memberIds.every(id => result.assembly.members.some(member => member.id === id))
        || !result.parameters.some(parameter => parameter.id === action.pattern.countParameterId)
        || !result.parameters.some(parameter => parameter.id === action.pattern.spacingParameterId)));
    const invalidAddition = ["member.add", "mate.add", "port.add", "slot.add", "rule.add"].includes(action.type) && next === result;
    if (missingTarget || invalidAddition) throw new Error("排队操作的对象或引用已变化，无法直接合并。");
    result = next;
  }
  return result === document ? document : { ...result, revision: document.revision + 1 };
}

// The server owns persisted data/checks. The reducer only projects queued input so typing stays responsive.
export class SharedDraftController {
  private api: DraftApi;
  private state = initialSharedState();
  private confirmed: DraftEnvelope | null = null;
  private pending: PendingEdit[] = [];
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
  dispose() {
    this.disposed = true; this.generation++; this.listeners.clear();
    this.rejectPending(new Error("编辑控制器已释放"));
    this.pending.forEach(edit => { edit.onSaved = undefined; });
  }
  private rejectPending(error: unknown) {
    for (const edit of this.pending) { edit.reject?.(error); edit.reject = undefined; edit.resolve = undefined; }
  }
  private publish(patch: Partial<SharedDraftState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...patch };
    this.listeners.forEach(listener => listener());
  }
  private accept(draft: DraftEnvelope) {
    draft = { ...draft, document: normalizeDraftDocument(draft.document) };
    const migrationActions = draftMigrationActions(draft.document);
    const queuedActions = new Set(this.pending.flatMap(edit => edit.actions.map(actionIdentity)));
    this.pending = [
      ...migrationActions.filter(action => !queuedActions.has(actionIdentity(action))).map(action => ({ actions: [action] })),
      ...this.pending,
    ];
    if (this.confirmed?.draftId === draft.draftId) this.recordChanges(draft);
    else this.publish({ agentChanges: [], changesTruncated: false, userPriority: false });
    this.confirmed = draft;
    try {
      this.pending.reduce(projectEdit, draft.document);
    } catch (error) {
      // Keep the full visible document so recovery never loses queued additions.
      this.rejectPending(error);
      this.publish({ status: "conflict", dirty: true, error: "Agent 修改影响了后续操作的对象或引用，无法直接合并。本页完整输入已保留，请下载本地副本后核对差异。" });
      return;
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
    const document = this.pending.reduce(projectEdit, this.confirmed.document);
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
    if (componentReducer(this.state.document, action) === this.state.document) return action.type !== "mate.add";
    this.pending.push({ actions: [structuredClone(action)], ...modelBase(this.state.document, [action]) });
    this.editGeneration++;
    this.project({ status: "saving", error: null, notice: "" });
    this.beginSave();
    return true;
  }
  commit(actions: ComponentAction[], onSaved?: (draft: DraftEnvelope) => void): Promise<DraftEnvelope> {
    if (!this.confirmed || !["ready", "saving"].includes(this.state.status) || this.disposed) return Promise.reject(new Error("草稿暂不可编辑"));
    if (!actions.length || actions.length > 100) return Promise.reject(new Error("一次操作需要 1 至 100 个动作"));
    const copied = structuredClone(actions);
    try { projectEdit(this.state.document, { actions: copied }); }
    catch (error) { return Promise.reject(error); }
    return new Promise((resolve, reject) => {
      this.pending.push({ actions: copied, batch: true, resolve, reject, onSaved,
        ...modelBase(this.state.document, copied) });
      this.editGeneration++;
      this.project({ status: "saving", error: null, notice: "" });
      this.beginSave();
    });
  }
  appendEvidence(draftId: string, additions: EvidenceFile[]): boolean {
    if (this.state.draftId !== draftId || this.disposed) return false;
    const definition = this.state.document.definition;
    const existing = new Set(definition.evidenceFiles.map(file => file.id));
    const files = additions.filter(file => !existing.has(file.id));
    if (!files.length) return true;
    if (definition.evidenceFiles.length + files.length > 10
        || [...definition.evidenceFiles, ...files].reduce((sum, file) => sum + file.size, 0) > 15 * 1024 * 1024) return false;
    const addedAt = new Date().toISOString();
    return this.dispatch({ type: "definition.patch", patch: {
      evidenceFiles: [...definition.evidenceFiles, ...files],
      evidence: [...definition.evidence, ...files.map(file => ({ id: file.id, name: file.fileName, mediaType: file.mediaType, size: file.size, addedAt }))],
    } });
  }
  private beginSave() {
    if (this.saving || !this.pending.length || this.disposed) return;
    this.saving = this.save().finally(() => {
      this.saving = null;
      if (this.state.status === "saving") this.beginSave();
    });
  }
  private async save() {
    while (this.pending.length && this.confirmed && !this.disposed) {
      const edit = this.pending[0];
      try {
        const result = edit.batch
          ? await this.api.applyBatch(this.confirmed.draftId, this.confirmed.document.revision, edit.actions)
          : await this.api.apply(this.confirmed.draftId, this.confirmed.document.revision, edit.actions[0]);
        if (this.disposed) return;
        this.pending.shift();
        this.accept(result);
        edit.onSaved?.(result);
        edit.resolve?.(result);
        if (this.state.status === "conflict") return;
      } catch (error) {
        edit.reject?.(error); edit.reject = undefined; edit.resolve = undefined;
        const current = (error as { current?: DraftEnvelope })?.current;
        if (current?.draftId === this.confirmed.draftId) this.recordChanges(current);
        const status = (error as { status?: number }).status;
        const retryable = status === 429 || (status !== undefined && status >= 500);
        const rejected = rejectedEditCodes.has(errorCode(error)) || (edit.batch && (
          status === 400 || status === 422 || ["INVALID_BINDING_RANGE", "COMPONENT_ITEM_NOT_FOUND"].includes(errorCode(error))));
        if (!retryable && rejected) {
          this.pending.shift();
          this.accept(this.confirmed);
          if (this.state.status === "conflict") return;
          this.publish({ notice: errorMessage(error) });
          continue;
        }
        const conflict = ["REVISION_CONFLICT", "EDIT_TARGET_REMOVED", "EDIT_STRUCTURE_CONFLICT"].includes(errorCode(error));
        this.rejectPending(error);
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
      this.rejectPending(new Error("已放弃未保存的操作"));
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
  if (!response.ok) throw Object.assign(new Error("草稿请求失败"), { status: response.status, code: data?.error?.code || "INVALID_REQUEST", current: data?.error?.current });
  return data as T;
}
export const httpDraftApi: DraftApi = {
  list: () => request(""), create: () => request("", {}),
  get: (id, afterRevision) => request(`/${encodeURIComponent(id)}${afterRevision === undefined ? "" : `?afterRevision=${afterRevision}`}`),
  apply: (id, expectedRevision, action) => request(`/${encodeURIComponent(id)}/actions`, { expectedRevision, action }),
  applyBatch: (id, expectedRevision, actions) => request(`/${encodeURIComponent(id)}/actions/batch`, { expectedRevision, actions }),
  export: id => request(`/${encodeURIComponent(id)}/export`),
};
