const actorId = process.env.EXPO_PUBLIC_ACTOR_ID || 'default_user';
const API_BASE = process.env.EXPO_PUBLIC_API_URL || 'http://10.0.0.206:8000';

export function actorHeaders(extra: Record<string, string> = {}) {
  return {
    'X-User-Id': actorId,
    ...extra,
  };
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
      const resp = await fetch(`${API_BASE}/api/admin/applicants?status=${status}`, {
        headers: actorHeaders()
      });
      return resp.json();
    },
    updateApplicant: async (id: string, data: any) => {
      const resp = await fetch(`${API_BASE}/api/admin/applicants/${id}`, {
        method: 'PATCH',
        headers: { 
          'Content-Type': 'application/json',
          ...actorHeaders()
        },
        body: JSON.stringify(data),
      });
      return resp.json();
    }
  }
};
