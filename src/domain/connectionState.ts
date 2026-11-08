import type { ComponentDocument } from "./component.ts";

/** Poll responses recreate objects; only actual assembly inputs invalidate a solve. */
export function connectionStateKey(draftId: string | null, document: ComponentDocument): string {
  return JSON.stringify([draftId, document.assembly, document.parameters, document.bindings]);
}
