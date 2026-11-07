/**
 * Browser upload into the existing Virtual Visit intake (/api/capture/visit-video/*), the same
 * resumable part protocol the native app uses (backend/app/routers/capture_visit_video.py):
 * create recording → sign parts in batches → PUT each byte range → commit → complete.
 *
 * Resumable within the page: if the connection drops, the next run asks the server which parts
 * it already has and sends only the rest. The file itself lives in memory, so a page reload
 * loses it (the native app keeps it on disk).
 */
import { API_BASE } from '../api';
import { apiErrorMessage } from '../visit-upload';
import { supabase } from '../supabase';
import type { CoachSummary } from './motion';

const BASE = `${API_BASE}/api/capture/visit-video`;

export interface CaptureMeta {
  guide: string;
  guide_version: number;
  platform: 'web-ios' | 'web-android' | 'web-other';
  device: string;
  device_status: string;
  lens: string;
  locks: Record<string, string>;
  width: number;
  height: number;
  fps: number;
  stabilization: string;
  hdr: string;
  mime: string;
}

export interface UploadJob {
  jobId: string;
  name: string;
  floorIndex: number;
  blob: Blob;
  durationS: number;
  coach: CoachSummary | null;
  capture: CaptureMeta;
  recordingId?: string | null;
}

export type UploadEvent = { sent: number; total: number; recordingId: string };

async function headers(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (session?.access_token) h.Authorization = `Bearer ${session.access_token}`;
  return h;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { ...(await headers()), ...(init.headers as Record<string, string> | undefined) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(apiErrorMessage(body, `Request failed (${res.status})`)) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return body as T;
}

export const visitVideoApi = {
  ensure: (jobId: string) => call('/sessions', { method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
  create: (j: UploadJob, contentType: string) => call<{ recording_id: string; part_size: number; parts_total: number }>('/recordings', {
    method: 'POST',
    body: JSON.stringify({
      job_id: j.jobId, name: j.name, scope: 'floor', floor_index: j.floorIndex,
      size_bytes: j.blob.size, duration_s: Math.round(j.durationS * 100) / 100, rooms: [],
      lens: `${j.capture.lens} · ${j.capture.platform}`.slice(0, 60), coach: j.coach, capture: j.capture,
      width: j.capture.width || undefined, height: j.capture.height || undefined, content_type: contentType,
    }),
  }),
  status: (jobId: string, rid: string) => call<{ committed: number[]; parts_total: number; part_size: number; complete: boolean }>(
    `/recordings/${rid}?job_id=${encodeURIComponent(jobId)}`),
  sign: (jobId: string, rid: string, parts: number[]) => call<{ parts: { part: number; signed_url: string; token?: string | null }[] }>(
    `/recordings/${rid}/sign`, { method: 'POST', body: JSON.stringify({ job_id: jobId, parts }) }),
  commit: (jobId: string, rid: string, part: number, size: number) => call(`/recordings/${rid}/commit`, {
    method: 'POST', body: JSON.stringify({ job_id: jobId, part, size }) }),
  complete: (jobId: string, rid: string) => call(`/recordings/${rid}/complete`, { method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
  submit: (jobId: string) => call<{ ok: boolean }>('/submit', { method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
};

/** Byte range for part n (1-based). Exported for tests. */
export function partRange(n: number, partSize: number, total: number, size: number): { start: number; end: number } {
  const start = (n - 1) * partSize;
  const end = n < total ? start + partSize : size;
  return { start, end };
}

export function contentTypeFor(blob: Blob): string {
  const t = (blob.type || '').toLowerCase();
  if (t.includes('quicktime')) return 'video/quicktime';
  if (t.includes('webm')) return 'video/webm';
  return 'video/mp4';
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Upload or resume. Returns the recording id. Throws on a non-retryable error; the caller keeps
 * the job (with recordingId) and calls again to resume.
 */
export async function uploadVisitVideo(
  job: UploadJob,
  onProgress: (e: UploadEvent) => void,
  shouldStop: () => boolean = () => false,
): Promise<string> {
  await visitVideoApi.ensure(job.jobId);
  const ct = contentTypeFor(job.blob);
  if (!job.recordingId) {
    const created = await visitVideoApi.create(job, ct);
    job.recordingId = created.recording_id;
  }
  const rid = job.recordingId!;
  const st = await visitVideoApi.status(job.jobId, rid);
  const committed = new Set(st.committed);
  const { part_size: partSize, parts_total: total } = st;
  const size = job.blob.size;
  const report = () => onProgress({ sent: Math.min(size, committed.size * partSize), total: size, recordingId: rid });
  report();
  const missing = Array.from({ length: total }, (_, i) => i + 1).filter((p) => !committed.has(p));
  for (let i = 0; i < missing.length; i += 20) {
    if (shouldStop()) return rid;
    const signed = await visitVideoApi.sign(job.jobId, rid, missing.slice(i, i + 20));
    for (const p of signed.parts) {
      if (shouldStop()) return rid;
      const { start, end } = partRange(p.part, partSize, total, size);
      const body = job.blob.slice(start, end);
      let attempt = 0;
      for (;;) {
        try {
          const put = await fetch(p.signed_url, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'x-upsert': 'true' }, body });
          if (!put.ok) {
            const err = new Error(`Part upload failed (${put.status})`) as Error & { retryable?: boolean };
            err.retryable = put.status >= 500 || put.status === 408 || put.status === 429;
            throw err;
          }
          await visitVideoApi.commit(job.jobId, rid, p.part, end - start);
          break;
        } catch (e) {
          attempt += 1;
          const err = e as Error & { retryable?: boolean; status?: number };
          const retryable = err.retryable !== false && (err.status == null || err.status >= 500);
          if (!retryable || attempt >= 4) throw err;
          await sleep(1000 * 2 ** (attempt - 1));
        }
      }
      committed.add(p.part);
      report();
    }
  }
  await visitVideoApi.complete(job.jobId, rid);
  report();
  return rid;
}
