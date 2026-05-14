import { supabase } from './supabase';

const API_BASE = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.219:8002';

export async function actorHeaders(extra: Record<string, string> = {}) {
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = { ...extra };
  
  if (session?.access_token) {
    headers['Authorization'] = `Bearer ${session.access_token}`;
  } else {
    // Fallback if needed, but ideally we should be authenticated
    const actorId = process.env.EXPO_PUBLIC_ACTOR_ID || 'default_user';
    headers['X-User-Id'] = actorId;
  }
  
  return headers;
}

export const api = {
  onboarding: {
    apply: async (data: any) => {
      const resp = await fetch(`${API_BASE}/api/onboarding/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      return resp.json();
    },
    getStatus: async (memberId: string) => {
      const resp = await fetch(`${API_BASE}/api/onboarding/status/${memberId}`);
      return resp.json();
    }
  },
  admin: {
    listApplicants: async (status = 'pending') => {
      const headers = await actorHeaders();
      const resp = await fetch(`${API_BASE}/api/admin/applicants?status=${status}`, {
        headers
      });
      return resp.json();
    },
    updateApplicant: async (id: string, data: any) => {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const resp = await fetch(`${API_BASE}/api/admin/applicants/${id}`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify(data),
      });
      return resp.json();
    }
  }
};
