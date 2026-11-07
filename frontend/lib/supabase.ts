// @ts-ignore - process.env is injected by Expo at runtime
declare const process: { env: Record<string, string | undefined> };

import 'react-native-url-polyfill/auto';
import { Platform } from 'react-native';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import AsyncStorage from '@react-native-async-storage/async-storage';

const supabaseUrl = process.env.EXPO_PUBLIC_SUPABASE_URL || '';
const supabaseAnonKey = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '';

// PKCE so Google sign-in and calendar connect can exchange ?code=.
// detectSessionInUrl stays off: the app reads the auth-session URL itself.
// auth-js defaults to implicit, which returns a hash the phone browser drops.
// Production redirect that must be allow-listed in Supabase Auth:
//   bricks-go://auth-callback
const authOptions = {
  storage: AsyncStorage,
  autoRefreshToken: true,
  persistSession: true,
  detectSessionInUrl: false,
  flowType: 'pkce' as const,
};

const isWebSSR = Platform.OS === 'web' && typeof window === 'undefined';

export const supabase: SupabaseClient = isWebSSR
  ? createClient(supabaseUrl, supabaseAnonKey)
  : createClient(supabaseUrl, supabaseAnonKey, { auth: authOptions });
