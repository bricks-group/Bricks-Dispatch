import type { InsuranceStatus, InsuranceReview } from './insurance';
import { supabase } from './supabase';
import { fetchOAuthProviderEnabled, GOOGLE_UNAVAILABLE_MESSAGE } from './web-account';

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
    const body = await res.json().catch(() => ({} as { detail?: unknown; error?: unknown; retry_after?: unknown }));
    const code = typeof body.error === 'string' ? body.error : undefined;
    const retryRaw = body.retry_after ?? res.headers.get('Retry-After');
    const retryNum = Number(retryRaw);
    const retryAfter = Number.isFinite(retryNum) ? retryNum : undefined;
    if (res.status === 429 || code === 'rate_limited') {
      const wait = retryAfter != null ? ` Try again in ${retryAfter}s.` : '';
      throw new ApiError(`Too many requests.${wait}`, {
        status: 429,
        code: 'rate_limited',
        retryAfter,
      });
    }
    const detail = body.detail;
    const detailMsg = detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string'
      ? (detail as { message: string }).message
      : '';
    const message = typeof detail === 'string' && detail
      ? detail
      : detailMsg || `Request failed with ${res.status}`;
    throw new ApiError(message, { status: res.status, code });
  }
  return res.json();
}


export class ApiError extends Error {
  status: number;
  code?: string;
  retryAfter?: number;
  constructor(message: string, init: { status: number; code?: string; retryAfter?: number }) {
    super(message);
    this.name = 'ApiError';
    this.status = init.status;
    this.code = init.code;
    this.retryAfter = init.retryAfter;
  }
}

/** 429 / rate_limited is not a logged-out session. Callers must keep the token and retry. */
export function isRateLimited(err: unknown): boolean {
  const e = err as { status?: number; code?: string; message?: string } | null;
  if (!e) return false;
  if (e.status === 429 || e.code === 'rate_limited') return true;
  const msg = `${e.message || ''}`.toLowerCase();
  return msg.includes('rate_limited') || msg.includes('too many requests');
}

export function retryAfterSeconds(err: unknown): number | undefined {
  const n = (err as { retryAfter?: number } | null)?.retryAfter;
  return typeof n === 'number' && Number.isFinite(n) ? n : undefined;
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
  /** Server: a contractor in GO who never accepted the IC agreement and privacy policy. */
  needs_terms?: boolean;
  portfolio_samples?: Array<{ media_type: string; url?: string; link?: string; label?: string }>;
  /** Onboarding checklist (approved vendors who are not live yet). */
  onboarding?: OnboardingState | null;
  /** True once every onboarding step is done (or grandfathered). Jobs come automatically after that. */
  onboarding_complete?: boolean;
};

export type OnboardingStepKey = 'signed_in' | 'payouts' | 'availability' | 'guide' | 'agreement';

export type OnboardingStep = {
  key: OnboardingStepKey;
  title: string;
  detail: string;
  done: boolean;
  required: boolean;
};

export type OnboardingState = {
  state: 'applicant' | 'onboarding' | 'live' | 'paused' | 'rejected';
  complete: boolean;
  completed_at?: string | null;
  all_steps_done: boolean;
  steps: OnboardingStep[];
  done: number;
  total: number;
};

/** True while an approved vendor still has onboarding steps to finish. */
export function isOnboarding(m: AuthMember | null | undefined): boolean {
  return !!m && m.onboarding_complete === false && !!m.onboarding && m.onboarding.state === 'onboarding';
}

export const getOnboarding = () => api.get<OnboardingState>('/api/vendor-os/onboarding');
export const acknowledgeGuide = () => api.post<OnboardingState>('/api/vendor-os/onboarding/guide');
export const confirmAvailability = () => api.post<OnboardingState>('/api/vendor-os/onboarding/availability');
export const startPayoutSetup = (returnUrl: string) =>
  api.post<{ url: string }>('/api/vendor-os/stripe-connect/onboard', { return_url: returnUrl });

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
  insurance?: InsuranceStatus | null;
  insurance_review?: InsuranceReview;
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
  const body = await res.json().catch(() => ({} as { detail?: unknown; error?: unknown; retry_after?: unknown }));
  if (!res.ok) {
    const code = typeof body.error === 'string' ? body.error : undefined;
    const retryNum = Number(body.retry_after ?? res.headers.get('Retry-After'));
    const retryAfter = Number.isFinite(retryNum) ? retryNum : undefined;
    if (res.status === 429 || code === 'rate_limited') {
      const wait = retryAfter != null ? ` Try again in ${retryAfter}s.` : '';
      throw new ApiError(`Too many requests.${wait}`, { status: 429, code: 'rate_limited', retryAfter });
    }
    const detail = body.detail ?? body;
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

export function authCallbackUrl(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return `${window.location.origin}/auth/callback`;
  }
  return 'https://go.brxs.ca/auth/callback';
}

/** True when Canada Auth REST returns Fair Use 402 (settings / authorize blocked). */
export async function isAuthRestricted(): Promise<boolean> {
  const url = import.meta.env.VITE_SUPABASE_URL || '';
  const key = import.meta.env.VITE_SUPABASE_ANON_KEY || '';
  if (!url || !key) return false;
  try {
    const res = await fetch(`${url}/auth/v1/settings`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    if (res.status === 402) return true;
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      return isFairUseAuthError({ message: body, status: res.status });
    }
    return false;
  } catch {
    return false;
  }
}

/** Google sign-in for GO web. Basic login scopes only — not calendar. */
export async function loginWithGoogle(): Promise<void> {
  if (await isAuthRestricted()) {
    throw new Error(
      'Auth is restricted (Fair Use / 402). Google sign-in is unavailable until Auth unlocks.',
    );
  }
  // A disabled provider makes Supabase answer /authorize with raw JSON (400
  // "provider is not enabled"); check first so the vendor gets a real message.
  const googleOn = await fetchOAuthProviderEnabled(
    'google',
    import.meta.env.VITE_SUPABASE_URL || '',
    import.meta.env.VITE_SUPABASE_ANON_KEY || '',
  );
  if (googleOn === false) throw new Error(GOOGLE_UNAVAILABLE_MESSAGE);
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: authCallbackUrl(),
      skipBrowserRedirect: true,
      // Default Google scopes: openid email profile. Do NOT request calendar here.
      queryParams: { access_type: 'offline', prompt: 'select_account' },
    },
  });
  if (error) throw error;
  if (!data?.url) {
    throw new Error('Google sign-in did not return a redirect URL.');
  }
  window.location.assign(data.url);
}

/** /vendor-os/auth/me found no vendor_members row for this Auth user (403 or masked 401). */
export function isVendorMemberMissing(err: unknown): boolean {
  const e = err as { code?: string; message?: string } | null;
  if (!e) return false;
  if (e.code === 'vendor_member_not_found') return true;
  return `${e.message || ''}`.toLowerCase().includes('vendor member not found');
}

/** Phone-OTP Auth user with no confirmed email yet (Supabase getUser, not the cached JWT). */
export async function needsEmailFirst(): Promise<boolean> {
  try {
    const { data } = await supabase.auth.getUser();
    const u = data?.user;
    return !!u && !u.email && !!u.phone;
  } catch {
    return false;
  }
}

/** Phone-OTP Auth user (email may or may not be confirmed). */
export async function isPhoneUser(): Promise<boolean> {
  try {
    const { data } = await supabase.auth.getUser();
    return !!data?.user?.phone;
  } catch {
    return false;
  }
}

export async function me(): Promise<AuthMember> {
  return api.get<AuthMember>('/api/vendor-os/auth/me');
}

/** Record the contractor agreement and privacy policy as read and accepted. */
export async function acceptTerms(icAgreementVersion: string, read: string[]) {
  return api.post<{ terms_accepted_at: string; ic_agreement_version: string; needs_terms: boolean }>(
    '/api/vendor-os/profile/accept-terms',
    { ic_agreement_version: icAgreementVersion, read },
  );
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
  /** 'Shoot checklist' for media jobs, 'Job checklist' for other trades (set by the API). */
  checklist_title?: string | null;
  /** True for non-media trade jobs (set by the API): their checklist is ticked by the vendor, with proof. */
  trade_job?: boolean;
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
