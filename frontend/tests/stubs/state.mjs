export const state = globalThis.__calendarTestState ??= {
  redirect: 'bricks-go://auth-callback',
  authResult: null,
  exchangeCalls: [],
  fetchCalls: [],
};
export function reset() {
  state.redirect = 'bricks-go://auth-callback';
  state.authResult = null;
  state.exchangeCalls = [];
  state.fetchCalls = [];
}
