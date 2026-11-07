// Stand-in for lib/supabase.ts (the vendor's shared app client).
import { state } from './state.mjs';
export const supabaseUrl = 'https://supabase.test';
export const supabaseAnonKey = 'test-anon-key';
export const supabase = {
  auth: {
    async getSession() {
      if (state.vendorGetSessionError) return { data: { session: null }, error: state.vendorGetSessionError };
      return { data: { session: state.vendorSession }, error: null };
    },
    async setSession() {
      state.vendorSetSessionCalls += 1;
      return { data: { session: null }, error: state.vendorSetSessionError };
    },
    async signOut(opts) {
      state.vendorSignOutCalls.push(opts?.scope ?? 'global');
      state.vendorSession = null;
      return { error: null };
    },
    async signInWithOAuth() {
      state.vendorOAuthCalls += 1;
      return { data: { url: 'https://supabase.test/authorize' }, error: null };
    },
    async exchangeCodeForSession() {
      state.vendorExchangeCalls += 1;
      return { data: { session: null }, error: new Error('vendor client must not exchange') };
    },
  },
};
