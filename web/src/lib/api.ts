import { supabase } from './supabase';

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
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
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export type AuthMember = {
  id: string;
  name: string;
  email: string;
  role: string;
  title?: string;
  status: 'available' | 'busy' | 'offline';
};

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
  time_window_start: string;
  time_window_end: string;
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
};

export type PaginatedJobsResponse = {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
};
