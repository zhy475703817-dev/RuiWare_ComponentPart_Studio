import type { ComponentDocument } from "./component.ts";

export function columnFrameMembers(document: ComponentDocument) {
  const groups = { columns: [] as string[], horizontal: [] as string[], diagonal: [] as string[] };
  for (const member of document.assembly.members) {
    if (member.template.templateKind === "component") continue;
    const text = `${member.role} ${member.name}`.toLowerCase();
    const interfaces = member.interfaces.map(i => i.id.toLowerCase());
    if (/立柱|upright/.test(text) || interfaces.some(i => i.startsWith("upright."))) groups.columns.push(member.id);
    else if (/横撑|crossbrace/.test(text) || interfaces.some(i => i.startsWith("crossbrace."))) groups.horizontal.push(member.id);
    else if (/斜撑|diagonal/.test(text) || interfaces.some(i => i.startsWith("diagonalbrace."))) groups.diagonal.push(member.id);
  }
  return /柱片/.test(`${document.definition.name} ${document.definition.category}`)
    || groups.columns.length >= 2 && groups.horizontal.length > 0 && groups.diagonal.length > 0 ? groups : null;
}

export function hasBuiltInBraces(document: ComponentDocument): boolean {
  const groups = columnFrameMembers(document);
  if (!groups) return true;
  const rules = document.braceGenerationRules || [];
  if (!rules.length) return false;
  const columns = new Set(rules.flatMap(r => [r.leftColumnMemberId, r.rightColumnMemberId]));
  const braces = new Set(rules.flatMap(r => [r.horizontalMemberId, r.diagonalMemberId]));
  const ids = new Set(rules.map(r => r.id));
  document.assembly.members.forEach(m => { if (m.generatedBy && ids.has(m.generatedBy)) braces.add(m.id); });
  return groups.columns.every(id => columns.has(id))
    && [...groups.horizontal, ...groups.diagonal].every(id => braces.has(id))
    && rules.every(r => [r.leftColumnMemberId, r.rightColumnMemberId].every(id =>
      document.bindings.some(b => b.memberId === id && b.parameterId === r.heightParameterId)));
}
