import { API_BASE } from './api';
import { supabase } from './supabase';

/** Virtual Visit bridge files (owned GPU cook later). */
export const VISIT_EXTS = ['.ply', '.splat', '.spz'] as const;

const VISIT_BUCKET = 'brxs-deliverables';

export type VisitDeliverResult = {
  ok: true;
  mode: 'url' | 'file';
  job_id?: string;
  output?: string;
};

export type VisitUploadPhase = 'signing' | 'uploading' | 'delivering';

function isVisitFilename(name: string): boolean {
  const lower = (name || '').trim().toLowerCase();
  return VISIT_EXTS.some((ext) => lower.endsWith(ext));
}

/** FastAPI detail may be string | {message} | validation array. */
export function apiErrorMessage(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object') return fallback;
  const d = (body as { detail?: unknown; message?: unknown }).detail;
  if (typeof d === 'string' && d.trim()) return d;
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    const msg = (d as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  if (Array.isArray(d) && d.length) {
    const first = d[0];
    if (typeof first === 'string') return first;
    if (first && typeof first === 'object' && typeof (first as { msg?: unknown }).msg === 'string') {
      return String((first as { msg: string }).msg);
    }
  }
  const m = (body as { message?: unknown }).message;
  if (typeof m === 'string' && m.trim()) return m;
  return fallback;
}

async function authHeaders(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = { ...extra };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

/** Paste-Polycam path — same /visit/deliver finalize as file. */
export async function deliverVisitUrl(
  jobId: string,
  polycamUrl: string,
  opts?: { title?: string },
): Promise<VisitDeliverResult> {
  const url = (polycamUrl || '').trim();
  if (!jobId) throw new Error('Missing job');
  if (url.length < 8) throw new Error('Paste a Polycam / share URL');
  const delRes = await fetch(`${API_BASE}/api/capture/visit/deliver`, {
    method: 'POST',
    headers: await authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      job_id: jobId,
      polycam_url: url,
      title: opts?.title || 'Virtual Visit',
    }),
  });
  const del = await delRes.json().catch(() => ({}));
  if (!delRes.ok) throw new Error(apiErrorMessage(del, 'Could not deliver visit'));
  return { ok: true, mode: 'url', job_id: del.job_id || jobId, output: del.output };
}

/**
 * .ply / .splat / .spz → /visit/sign → Storage (token PUT or signed URL) → /visit/deliver.
 * Same finalize/media_delivery bridge as Polycam URL (visit_bridge + kind=visit).
 */
export async function deliverVisitFile(
  jobId: string,
  file: File,
  opts?: { title?: string; onPhase?: (phase: VisitUploadPhase) => void },
): Promise<VisitDeliverResult> {
  if (!jobId) throw new Error('Missing job');
  if (!file) throw new Error('Choose a .ply, .splat, or .spz file');
  if (!isVisitFilename(file.name)) {
    throw new Error('Only .ply, .splat, or .spz files are supported');
  }

  opts?.onPhase?.('signing');
  const signRes = await fetch(`${API_BASE}/api/capture/visit/sign`, {
    method: 'POST',
    headers: await authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ job_id: jobId, filename: file.name }),
  });
  const sign = await signRes.json().catch(() => ({}));
  if (!signRes.ok || !sign.storage_path) {
    throw new Error(apiErrorMessage(sign, 'Could not start visit upload'));
  }

  const storagePath: string = sign.storage_path;
  const path: string = sign.path || storagePath;
  const token: string | undefined = sign.token || undefined;
  const signedUrl: string | undefined = sign.signed_url || undefined;
  const bucket: string = sign.bucket || VISIT_BUCKET;
  const contentType = sign.content_type || file.type || 'application/octet-stream';

  opts?.onPhase?.('uploading');
  let uploaded = false;
  // Prefer Supabase uploadToSignedUrl when token present (capture-frame parity).
  // Fair-Use HMAC proxy returns token=null — plain PUT to signed_url.
  if (token && path) {
    const bytes = await file.arrayBuffer();
    const { error: upErr } = await supabase.storage
      .from(bucket)
      .uploadToSignedUrl(path, token, bytes, { contentType });
    if (!upErr) uploaded = true;
    else if (!signedUrl) throw new Error(upErr.message || 'Visit file upload failed');
  }
  if (!uploaded) {
    if (!signedUrl) throw new Error('No signed upload URL');
    const put = await fetch(signedUrl, {
      method: 'PUT',
      headers: { 'Content-Type': contentType },
      body: file,
    });
    if (!put.ok) throw new Error(`Visit file upload failed (${put.status})`);
  }

  opts?.onPhase?.('delivering');
  const delRes = await fetch(`${API_BASE}/api/capture/visit/deliver`, {
    method: 'POST',
    headers: await authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      job_id: jobId,
      storage_path: storagePath,
      title: opts?.title || 'Virtual Visit',
    }),
  });
  const del = await delRes.json().catch(() => ({}));
  if (!delRes.ok) throw new Error(apiErrorMessage(del, 'Could not deliver visit'));
  return { ok: true, mode: 'file', job_id: del.job_id || jobId, output: del.output };
}

export function formatVisitFileLabel(file: File): string {
  const mb = file.size / (1024 * 1024);
  const size = mb >= 0.1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`;
  return `${file.name} · ${size}`;
}
