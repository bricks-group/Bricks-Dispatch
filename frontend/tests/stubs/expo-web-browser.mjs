import { state } from './state.mjs';
export function maybeCompleteAuthSession() {}
export async function openAuthSessionAsync(url, redirect) {
  state.openAuthCalls += 1;
  state.lastAuthUrl = url;
  return state.authResult(url, redirect);
}
export async function openBrowserAsync() { return { type: 'opened' }; }
