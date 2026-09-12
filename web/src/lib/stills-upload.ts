import { API_BASE, api } from './api';
import { supabase } from './supabase';

export const STILLS_TYPES = [
  { id: 'hdr', label: 'HDR still' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'twilight', label: 'Twilight' },
  { id: 'staging', label: 'Staging' },
] as const;

export type StillsType = (typeof STILLS_TYPES)[number]['id'];

type SignResp = {
  upload_id: string;
  storage_path: string;
  signed_url: string;
  content_type?: string;
  media_type: string;
};

async function authHeaders(): Promise<HeadersInit> {
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

/** Camera JPEG / drone / reel → Storage via signed PUT (not the 4MB Vercel POST). */
export async function uploadStillFile(jobId: string, file: File, mediaType: StillsType) {
  const signRes = await fetch(`${API_BASE}/api/uploads/sign`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({
      job_id: jobId,
      filename: file.name,
      mime_type: file.type || 'application/octet-stream',
      file_size: file.size,
      media_type: mediaType,
    }),
  });
  const sign = (await signRes.json().catch(() => ({}))) as SignResp & { detail?: string; message?: string };
  if (!signRes.ok || !sign.signed_url) {
    throw new Error(sign.detail || sign.message || 'Could not start stills upload');
  }
  const put = await fetch(sign.signed_url, {
    method: 'PUT',
    headers: { 'Content-Type': sign.content_type || file.type || 'application/octet-stream' },
    body: file,
  });
  if (!put.ok) throw new Error('Stills file upload failed');
  return api.post<{ id: string; filename: string; media_type?: string }>('/api/uploads/complete', {
    job_id: jobId,
    upload_id: sign.upload_id,
    storage_path: sign.storage_path,
    filename: file.name,
    mime_type: file.type || 'application/octet-stream',
    file_size: file.size,
    media_type: mediaType || sign.media_type,
  });
}
