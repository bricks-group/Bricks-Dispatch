import { state } from './state.mjs';
const original = { access_token: 'test-session', refresh_token: 'test-refresh' };
export const supabase = {
  auth: {
    async getSession() { return { data: { session: original } }; },
    async setSession() { return { data: {}, error: null }; },
    async signInWithOAuth() { return { data: { url: 'https://supabase.test/authorize' }, error: null }; },
    async exchangeCodeForSession(code) {
      state.exchangeCalls.push(code);
      return { data: { session: { provider_token: 'pt', provider_refresh_token: 'prt' } }, error: null };
    },
  },
};
