export const componentCodePattern = /^[A-Z][A-Z0-9_-]{2,39}$/;

export function normalizeComponentCode(value: string): string {
  return value.toUpperCase();
}

export function isValidComponentCode(value: string): boolean {
  return componentCodePattern.test(value);
}
