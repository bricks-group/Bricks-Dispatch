// @ts-ignore - process.env is injected by Expo at runtime
declare const process: { env: Record<string, string | undefined> };

import 'react-native-url-polyfill/auto';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { supabaseAuthOptions, SSR_AUTH_OPTIONS } from './supabaseAuthOptions';

// Guard against SSR — AsyncStorage uses window which doesn't exist on the server
const isBrowser = typeof window !== 'undefined';

const supabaseUrl = process.env.EXPO_PUBLIC_SUPABASE_URL || '';
const supabaseAnonKey = process.env.EXPO_PUBLIC_SUPABASE_ANON_KEY || '';

// PKCE (see supabaseAuthOptions.ts): native Calendar Connect and Google sign-in
// exchange the ?code= returned to bricks-go://auth-callback.
export const supabase: SupabaseClient = isBrowser
  ? createClient(supabaseUrl, supabaseAnonKey, { auth: supabaseAuthOptions(AsyncStorage) })
  : createClient(supabaseUrl, supabaseAnonKey, { auth: SSR_AUTH_OPTIONS });
