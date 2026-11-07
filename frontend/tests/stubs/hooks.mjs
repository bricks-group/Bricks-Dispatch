// Test-only module resolution: swap native/Expo modules for in-memory stubs so
// calendarConnect.ts can run under node:test, and resolve extensionless TS imports.
const STUBS = {
  'expo-web-browser': 'expo-web-browser.mjs',
  'expo-linking': 'expo-linking.mjs',
  './supabase': 'supabase.mjs',
  './api': 'api.mjs',
};
export async function resolve(specifier, context, next) {
  const stub = STUBS[specifier];
  const parent = context.parentURL || '';
  const stubbedParent = parent.includes('/lib/calendarConnect.ts') || parent.includes('/lib/calendarAuthClient.ts');
  if (stub && (!specifier.startsWith('.') || stubbedParent)) {
    return { url: new URL(stub, import.meta.url).href, shortCircuit: true };
  }
  if (specifier.startsWith('.') && !/\.[cm]?[jt]sx?$/.test(specifier) && context.parentURL?.endsWith('.ts')) {
    return next(`${specifier}.ts`, context);
  }
  return next(specifier, context);
}
