// @ts-ignore - process.env is injected by Expo at runtime
declare const process: { env: Record<string, string | undefined> };

import 'react-native-url-polyfill/auto';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Guard against SSR — AsyncStorage uses window which doesn't exist on the server
const isBrowser = typeof window !== 'undefined';

const supabaseUrl = process.env.EXPO_PUBLIC_SUPABASE_URL || '';
const supabaseAnonKey = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '';

export const supabase: SupabaseClient = isBrowser
  ? createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        storage: AsyncStorage,
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: false,
        // Google sign-in returns ?code= on bricks-go://auth-callback (exchangeCodeForSession): PKCE required by the new login service.
        flowType: 'pkce',
        // Keep sessions across the Supabase -> db.brxs.ca URL change (cutover 2026-11-21).
        storageKey: 'sb-rbywjnnctjglhcdemnnj-auth-token',
      },
    })
  : createClient(supabaseUrl, supabaseAnonKey);
