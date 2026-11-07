import { state } from './state.mjs';
export function maybeCompleteAuthSession() {}
export async function openAuthSessionAsync() { return state.authResult; }
export async function openBrowserAsync() { return { type: 'opened' }; }
