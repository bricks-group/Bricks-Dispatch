export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const TOKEN_KEY = 'vendor_os_token';

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
  const token = getToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (!headers.has('Content-Type') && options.body && !(options.body instanceof FormData)) {
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
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export type AuthMember = {
  id: string;
  name: string;
  email: string;
  role: string;
  status: 'available' | 'busy' | 'offline';
};

export async function login(email: string, name?: string): Promise<AuthMember> {
  const res = await fetch(`${API_BASE}/api/vendor-os/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, name }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || 'Login failed');
  }
  const data = await res.json();
  setToken(data.token);
  return data.member;
}

export async function me(): Promise<AuthMember> {
  return api.get<AuthMember>('/api/vendor-os/auth/me');
}

export async function logout() {
  try {
    await api.post('/api/vendor-os/auth/logout');
  } finally {
    clearToken();
  }
}

export type TeamMember = {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: string;
  status: 'available' | 'busy' | 'offline';
  is_active: boolean;
  assigned_jobs: number;
};

export type PaginatedTeamResponse = {
  items: TeamMember[];
  total: number;
  page: number;
  page_size: number;
};

export type Job = {
  id: string;
  address: string;
  time_window_start: string;
  time_window_end: string;
  status: 'assigned' | 'in_progress' | 'completed';
  assignee_id: string;
  assignee_name?: string;
  scheduled_date: string;
};

export type PaginatedJobsResponse = {
  items: Job[];
  total: number;
  page: number;
  page_size: number;
};
