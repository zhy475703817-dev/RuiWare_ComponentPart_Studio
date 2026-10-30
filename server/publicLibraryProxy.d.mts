import type { Connect } from 'vite';
export function createPublicLibraryMiddleware(options: { apiUrl: string; apiKey: string }): Connect.NextHandleFunction;
