import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { supabaseAnonKey, supabaseUrl } from './supabase';
import { calendarOAuthAuthOptions } from './supabaseAuthOptions';

let attempt = 0;

/** A fresh, in-memory Supabase client for one Google Calendar connect attempt. */
export function createCalendarOAuthClient(): SupabaseClient {
  attempt += 1;
  return createClient(supabaseUrl, supabaseAnonKey, {
    auth: calendarOAuthAuthOptions(`bricks-go-calendar-oauth-${attempt}`),
  });
}
