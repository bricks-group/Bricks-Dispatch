import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || ''
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || ''

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn('Supabase URL or Anon Key missing in environment variables')
}

/** PKCE so /auth/callback can exchange ?code= for a session (Google login + calendar). */
export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
    detectSessionInUrl: true,
    flowType: 'pkce',
    // Keep sessions across the Supabase -> db.brxs.ca URL change (cutover 2026-11-21).
    storageKey: 'sb-rbywjnnctjglhcdemnnj-auth-token',
  },
})
