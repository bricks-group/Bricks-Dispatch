// Shared, resettable test state. Every stub can be told to fail.
export const state = globalThis.__calendarTestState ??= {};
export function reset() {
  Object.assign(state, {
    redirect: 'bricks-go://auth-callback',
    // Function returning the auth-session result (may be async / deferred).
    authResult: async () => ({ type: 'success', url: 'bricks-go://auth-callback?code=good' }),
    openAuthCalls: 0,
    // Vendor's own session on the shared app client.
    vendorSession: { access_token: 'vendor-access', refresh_token: 'vendor-refresh', user: { id: 'vendor-1' } },
    vendorGetSessionError: null,
    vendorSetSessionError: null,
    vendorSetSessionCalls: 0,
    vendorSignOutCalls: [],
    vendorExchangeCalls: 0,
    vendorOAuthCalls: 0,
    // Simulates something replacing the vendor session during connect.
    onExchange: null,
    // Isolated client's token endpoint.
    exchangeMode: 'ok', // 'ok' | 'error' | 'no_refresh'
    exchangeBodies: [],
    logoutCalls: 0,
    // Exchanger used by login tests.
    loginExchangeCalls: [],
    loginExchangeError: null,
    loginExchangeThrows: false,
    fetchCalls: [],
  });
}
reset();
