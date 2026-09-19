import { supabase } from './supabase';

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'https://dispatch-api.brxs.ca';
const TOKEN_KEY = 'bricks_dispatch_token';

type RequestOptions = RequestInit & { query?: Record<string, string | number | undefined> };

function buildUrl(path: string, query?: RequestOptions['query']) {
  const url = new URL(`${API_BASE}${path}`);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  
  // Get token from Supabase
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token || localStorage.getItem(TOKEN_KEY);
  
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (options.body instanceof FormData) {
    headers.delete('Content-Type');
  } else if (!headers.has('Content-Type') && options.body) {
    headers.set('Content-Type', 'application/json');
  }

  const res = await fetch(buildUrl(path, options.query), {
    ...options,
    headers,
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed with ${res.status}`);
  }
  return res.json();
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { method: 'GET', query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  postForm: <T>(path: string, body: FormData) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export type AuthMember = {
  id: string;
  name: string;
  email: string;
  role: string;
  title?: string;
  status: 'available' | 'busy' | 'offline';
  onboarding_status?: string;
  is_active?: boolean;
  member_role?: string;
  terms_accepted_at?: string | null;
  application_submitted?: boolean;
  app_access?: boolean;
  portfolio_samples?: Array<{ media_type: string; url?: string; link?: string; label?: string }>;
};

export function hasAppAccess(m: AuthMember | null | undefined): boolean {
  if (!m) return false;
  if (typeof m.app_access === 'boolean') return m.app_access;
  const role = `${m.role || m.title || ''}`.toLowerCase();
  if (role.includes('owner')) return true;
  const status = (m.onboarding_status || '').toLowerCase();
  return status === 'approved' && m.is_active !== false;
}

export type VendorProfile = {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: string;
  title: string;
  availability: boolean;
  skills: string[];
  service_areas: string[];
  home_city?: string;
  tier: number;
  tier_label: string;
  drone_certified: boolean;
  rpas_certificate_url?: string | null;
  rpas_certificate_level?: string | null;
  rpas_certificate_number?: string | null;
  onboarding_status: string;
  assignable: boolean;
  assignable_reason?: string | null;
  payout_model: 'owner_full' | 'ic_rate_card';
  payout_label: string;
  show_platform_cut: boolean;
  avatar_url?: string | null;
};

export async function login(email: string): Promise<void> {
  const { error } = await supabase.auth.signInWithOtp({ email });
  if (error) throw error;
}


function isFairUseAuthError(err: unknown): boolean {
  const anyErr = err as { message?: string; status?: number; code?: string; statusCode?: number } | null;
  const msg = `${anyErr?.message || err || ''}`.toLowerCase();
  const status = anyErr?.status ?? anyErr?.statusCode;
  if (status === 402) return true;
  return (
    msg.includes('402')
    || msg.includes('exceed_storage')
    || msg.includes('storage_size_quota')
    || msg.includes('fair-use')
    || msg.includes('fair use')
    || msg.includes('fair_use')
    || msg.includes('restricted')
    || msg.includes('over_quota')
    || msg.includes('quota exceeded')
  );
}

function parseApiErrorDetail(detail: unknown, fallback: string): string {
  if (detail == null) return fallback;
  if (typeof detail === 'string') return detail;
  if (typeof detail === 'object') {
    const d = detail as { message?: string; code?: string; detail?: unknown };
    if (typeof d.message === 'string' && d.message) return d.message;
    if (typeof d.detail === 'string') return d.detail;
    if (d.detail && typeof d.detail === 'object' && typeof (d.detail as { message?: string }).message === 'string') {
      return (d.detail as { message: string }).message;
    }
  }
  return fallback;
}

async function fairUsePasswordLogin(email: string, password: string): Promise<AuthMember> {
  const res = await fetch(`${API_BASE}/api/vendor-os/auth/fair-use-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail ?? body;
    throw new Error(parseApiErrorDetail(detail, `Login failed (${res.status})`));
  }
  const access = (body as { access_token?: string }).access_token;
  const refresh = (body as { refresh_token?: string }).refresh_token || access;
  if (!access) throw new Error('Fair-Use login returned no token');
  localStorage.setItem(TOKEN_KEY, access);
  const { error } = await supabase.auth.setSession({
    access_token: access,
    refresh_token: refresh || access,
  });
  if (error) {
    // Session may still work via TOKEN_KEY for API calls even if setSession complains.
    console.warn('setSession after fair-use login:', error.message);
  }
  return me();
}

/** Password sign-in with Fair-Use Postgres fallback when Auth API is 402. */
export async function loginWithPassword(email: string, password: string): Promise<AuthMember> {
  const trimmed = email.trim().toLowerCase();
  if (!trimmed || !password) {
    throw new Error('Email and password are required');
  }
  try {
    const { data, error } = await supabase.auth.signInWithPassword({
      email: trimmed,
      password,
    });
    if (error) throw error;
    if (data.session?.access_token) {
      localStorage.setItem(TOKEN_KEY, data.session.access_token);
    }
    if (!data.session) throw new Error('No session');
    return me();
  } catch (err) {
    if (isFairUseAuthError(err)) {
      return fairUsePasswordLogin(trimmed, password);
    }
    // Auth completely blocked / network — try Fair-Use path once.
    const msg = `${(err as Error)?.message || err || ''}`.toLowerCase();
    if (
      msg.includes('fetch')
      || msg.includes('network')
      || msg.includes('failed to fetch')
      || msg.includes('auth')
    ) {
      try {
        return await fairUsePasswordLogin(trimmed, password);
      } catch (fairErr) {
        // Prefer the Fair-Use error if Auth was clearly blocked; else original.
        if (isFairUseAuthError(err)) throw fairErr;
        throw fairErr;
      }
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
}


export async function verifyOtp(email: string, token: string): Promise<AuthMember> {
  const { data, error } = await supabase.auth.verifyOtp({
    email,
    token,
    type: 'magiclink'
  });
  if (error) throw error;
  if (!data.session) throw new Error('No session');
  
  // Also call our backend to ensure member is synced and get extra info if needed
  return me();
}

/** Google sign-in for GO web. Basic login scopes only — not calendar. */
export function authCallbackUrl(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return `${window.location.origin}/auth/callback`;
  }
  return 'https://go.brxs.ca/auth/callback';
}

export async function loginWithGoogle(): Promise<void> {
  const { error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: authCallbackUrl(),
      // Default Google scopes: openid email profile. Do NOT request calendar here.
      queryParams: { access_type: 'offline', prompt: 'select_account' },
    },
  });
  if (error) throw error;
}

export async function me(): Promise<AuthMember> {
  return api.get<AuthMember>('/api/vendor-os/auth/me');
}

export async function logout() {
  await supabase.auth.signOut();
  localStorage.removeItem(TOKEN_KEY);
}

// ... rest of types ...
export type TeamMember = {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: string;
  title?: string;
  skills?: string[];
  status: 'available' | 'busy' | 'offline';
  is_active: boolean;
  assigned_jobs: number;
  assignable?: boolean;
  assignable_reason?: string | null;
  rpas_certificate_level?: string | null;
  payout_label?: string;
};

export type PaginatedTeamResponse = {
  items: TeamMember[];
  total: number;
  page: number;
  page_size: number;
};

export type ListingHit = {
  id: string;
  address: string;
  city?: string;
  deal_type?: string | null;
};

export type Job = {
  id: string;
  address: string;
  city?: string;
  state?: string | null;
  timezone?: string | null;
  zip_code?: string | null;
  postal_code?: string | null;
  time_window_start: string;
  time_window_end: string;
  preferred_window?: string | null;
  confirmed_arrive_at?: string | null;
  status: string;
  assignee_id?: string | null;
  assignee_name?: string;
  scheduled_date: string;
  transaction_id?: string | null;
  listing_address?: string | null;
  listing?: ListingHit | null;
  payout_display_cad?: number;
  payout_model?: string;
  deduction?: number;
  uploaded_count?: number;
  job_type?: string;
  package_id?: string | null;
  package_name?: string | null;
  add_ons?: unknown;
  checklist?: unknown;
  client_instructions?: string | null;
  property_details?: string | null;
  capture?: { tour?: boolean; floorplan?: boolean; visit?: boolean } | null;
};

export type PaginatedJobsResponse = {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
};
