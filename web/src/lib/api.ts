export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const ACTOR_ID = import.meta.env.VITE_ACTOR_ID || 'default_user';

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
  headers.set('X-User-Id', ACTOR_ID);
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

export type Job = {
  id: string;
  address: string;
  time_window_start: string;
  time_window_end: string;
  status: 'assigned' | 'in_progress' | 'completed';
  assignee_id: string;
  scheduled_date: string;
};
