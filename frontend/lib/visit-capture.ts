/**
 * Guided Visit video capture: local recordings + resumable chunked upload.
 *
 * Server: /api/capture/visit-video/* (Dispatch backend/app/routers/capture_visit_video.py),
 * capture_sessions.kind = 'visit'. Not the 360° tour (kind=splat) and not the
 * Polycam / file deliver path (/api/capture/visit/deliver), which stays as-is.
 *
 * Upload: the file is cut into fixed byte ranges (part_size from the server). Each part
 * gets its own signed PUT and is committed after it lands. If the connection drops, the
 * next run asks the server which parts it has and sends only the rest.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import { actorHeaders } from './api';
import type { CoachSummary } from './motion-coach';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
const BASE = `${API}/api/capture/visit-video`;
const STORE_KEY = 'bricks.go.visit-recordings.v1';

export const VISIT_LIMITS = { maxBytes: 4_000_000_000, minBytes: 1_000_000, maxSeconds: 15 * 60, warnSeconds: 13 * 60 } as const;
/** Recording settings. 4K at ~30 Mb/s keeps a 15 min floor under the 4 GB cap (≈3.4 GB). */
export const VISIT_CAMERA = { quality: '2160p' as const, bitrate: 30_000_000, codec: 'hvc1' as const, stabilization: 'standard' as const };
export const BYTES_PER_SECOND_EST = VISIT_CAMERA.bitrate / 8;

export type RoomMark = { name: string; t0: number; t1: number };
export type RecordingScope = 'floor' | 'room';
export type LocalState = 'recorded' | 'uploading' | 'paused' | 'uploaded' | 'failed';

export interface LocalRecording {
  localId: string;
  jobId: string;
  name: string;
  scope: RecordingScope;
  floorIndex: number;
  uri: string;
  sizeBytes: number;
  durationS: number;
  lens?: string | null;
  rooms: RoomMark[];
  coach?: CoachSummary | null;
  createdAt: number;
  serverId?: string | null;
  partSize?: number;
  partsTotal?: number;
  committed: number[];
  state: LocalState;
  error?: string | null;
}

export interface VisitBundle {
  session_id: string | null;
  status: string | null;
  address: string;
  recordings: { id: string; name: string; complete: boolean; parts_committed: number; parts_total: number; duration_s: number; size_bytes: number; scope: string; floor_index: number }[];
  pipeline: { title: string; detail?: string | null; status: string }[];
  limits: { max_bytes: number; max_seconds: number; sign_batch: number };
}

/* ───────────── local store (survives app restarts so uploads can resume) ───────────── */

async function readAll(): Promise<LocalRecording[]> {
  try {
    const raw = await AsyncStorage.getItem(STORE_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

async function writeAll(list: LocalRecording[]) {
  await AsyncStorage.setItem(STORE_KEY, JSON.stringify(list));
}

export async function listLocalRecordings(jobId: string): Promise<LocalRecording[]> {
  return (await readAll()).filter((r) => r.jobId === jobId).sort((a, b) => a.createdAt - b.createdAt);
}

export async function getLocalRecording(localId: string): Promise<LocalRecording | null> {
  return (await readAll()).find((r) => r.localId === localId) ?? null;
}

export async function saveLocalRecording(rec: LocalRecording) {
  const all = await readAll();
  const i = all.findIndex((r) => r.localId === rec.localId);
  if (i >= 0) all[i] = rec; else all.push(rec);
  await writeAll(all);
}

export async function removeLocalRecording(localId: string, deleteFile = true) {
  const all = await readAll();
  const rec = all.find((r) => r.localId === localId);
  await writeAll(all.filter((r) => r.localId !== localId));
  if (deleteFile && rec?.uri && Platform.OS !== 'web') {
    try {
      const { File } = await import('expo-file-system');
      const f = new File(rec.uri);
      if (f.exists) f.delete();
    } catch { /* best effort */ }
  }
}

export function newLocalId() {
  return `v${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

/** Move the camera's cache file into app documents so the OS can't purge it before upload. */
export async function persistVideo(uri: string, localId: string): Promise<{ uri: string; size: number }> {
  const { File, Directory, Paths } = await import('expo-file-system');
  const dir = new Directory(Paths.document, 'visit');
  if (!dir.exists) dir.create({ idempotent: true, intermediates: true });
  const src = new File(uri);
  const ext = (uri.split('.').pop() || 'mp4').toLowerCase().slice(0, 4);
  const dest = new File(dir, `${localId}.${ext}`);
  if (dest.exists) dest.delete();
  src.move(dest);
  return { uri: dest.uri, size: dest.size ?? 0 };
}

/* ───────────── API ───────────── */

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = await actorHeaders({ 'Content-Type': 'application/json' });
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { ...headers, ...(init.headers as any) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof body?.detail === 'string' ? body.detail : body?.detail?.message || body?.message || `Request failed (${res.status})`;
    const err: any = new Error(msg);
    err.status = res.status;
    err.code = body?.detail?.code || body?.code;
    throw err;
  }
  return body as T;
}

export const visitApi = {
  get: (jobId: string) => call<VisitBundle>(`/sessions?job_id=${encodeURIComponent(jobId)}`),
  ensure: (jobId: string) => call<VisitBundle>('/sessions', { method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
  create: (rec: LocalRecording) => call<{ recording_id: string; part_size: number; parts_total: number; sign_batch: number }>('/recordings', {
    method: 'POST',
    body: JSON.stringify({
      job_id: rec.jobId, name: rec.name, scope: rec.scope, floor_index: rec.floorIndex,
      size_bytes: rec.sizeBytes, duration_s: rec.durationS, rooms: rec.rooms, lens: rec.lens, coach: rec.coach,
      content_type: rec.uri.toLowerCase().endsWith('.mov') ? 'video/quicktime' : 'video/mp4',
    }),
  }),
  status: (jobId: string, rid: string) => call<{ committed: number[]; parts_total: number; part_size: number; complete: boolean }>(
    `/recordings/${rid}?job_id=${encodeURIComponent(jobId)}`),
  sign: (jobId: string, rid: string, parts: number[]) => call<{ parts: { part: number; signed_url: string; token?: string | null }[] }>(
    `/recordings/${rid}/sign`, { method: 'POST', body: JSON.stringify({ job_id: jobId, parts }) }),
  commit: (jobId: string, rid: string, part: number, size: number) => call<{ committed_count: number }>(
    `/recordings/${rid}/commit`, { method: 'POST', body: JSON.stringify({ job_id: jobId, part, size }) }),
  complete: (jobId: string, rid: string) => call<{ complete: boolean }>(`/recordings/${rid}/complete`, {
    method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
  discard: (jobId: string, rid: string) => call<{ deleted: boolean }>(`/recordings/${rid}?job_id=${encodeURIComponent(jobId)}`, { method: 'DELETE' }),
  submit: (jobId: string) => call<VisitBundle & { queued: string }>('/submit', { method: 'POST', body: JSON.stringify({ job_id: jobId }) }),
};

/* ───────────── resumable upload ───────────── */

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function putPart(url: string, uri: string, offset: number, length: number): Promise<void> {
  const { File, Paths } = await import('expo-file-system');
  const LegacyFS = await import('expo-file-system/legacy');
  const src = new File(uri);
  const handle = src.open();
  let bytes: Uint8Array;
  try {
    handle.offset = offset;
    bytes = handle.readBytes(length);
  } finally {
    handle.close();
  }
  const tmp = new File(Paths.cache, 'visit-part.bin');
  if (tmp.exists) tmp.delete();
  tmp.create();
  tmp.write(bytes);
  const res = await LegacyFS.uploadAsync(url, tmp.uri, {
    httpMethod: 'PUT',
    uploadType: LegacyFS.FileSystemUploadType.BINARY_CONTENT,
    headers: { 'Content-Type': 'application/octet-stream', 'x-upsert': 'true' },
  });
  if (res.status < 200 || res.status >= 300) {
    const err: any = new Error(`Part upload failed (${res.status})`);
    err.retryable = res.status >= 500 || res.status === 0 || res.status === 408 || res.status === 429;
    throw err;
  }
}

export interface UploadProgress { sent: number; total: number }

/**
 * Upload (or resume) one recording. Persists progress after every part, so killing the
 * app loses at most one part. Returns the updated record; throws on a non-retryable error.
 */
export async function uploadRecording(
  rec: LocalRecording,
  onProgress: (p: UploadProgress, rec: LocalRecording) => void,
  shouldStop: () => boolean = () => false,
): Promise<LocalRecording> {
  let r: LocalRecording = { ...rec, state: 'uploading', error: null };
  await saveLocalRecording(r);
  await visitApi.ensure(r.jobId);
  if (!r.serverId) {
    const created = await visitApi.create(r);
    r = { ...r, serverId: created.recording_id, partSize: created.part_size, partsTotal: created.parts_total, committed: [] };
    await saveLocalRecording(r);
  }
  const rid = r.serverId!;
  const st = await visitApi.status(r.jobId, rid);
  r = { ...r, committed: st.committed, partSize: st.part_size, partsTotal: st.parts_total };
  const partSize = r.partSize!;
  const total = r.partsTotal!;
  const report = () => onProgress({ sent: Math.min(r.sizeBytes, r.committed.length * partSize), total: r.sizeBytes }, r);
  report();

  const missing = Array.from({ length: total }, (_, i) => i + 1).filter((n) => !r.committed.includes(n));
  for (let i = 0; i < missing.length; i += 20) {
    if (shouldStop()) {
      r = { ...r, state: 'paused' };
      await saveLocalRecording(r);
      return r;
    }
    const batch = missing.slice(i, i + 20);
    const signed = await visitApi.sign(r.jobId, rid, batch);
    for (const p of signed.parts) {
      if (shouldStop()) {
        r = { ...r, state: 'paused' };
        await saveLocalRecording(r);
        return r;
      }
      const offset = (p.part - 1) * partSize;
      const length = p.part < total ? partSize : r.sizeBytes - partSize * (total - 1);
      let attempt = 0;
      for (;;) {
        try {
          await putPart(p.signed_url, r.uri, offset, length);
          await visitApi.commit(r.jobId, rid, p.part, length);
          break;
        } catch (e: any) {
          attempt += 1;
          const retryable = e?.retryable !== false && (e?.status == null || e.status >= 500);
          if (!retryable || attempt >= 4) throw e;
          await sleep(1000 * 2 ** (attempt - 1));
        }
      }
      r = { ...r, committed: [...r.committed, p.part].sort((a, b) => a - b) };
      await saveLocalRecording(r);
      report();
    }
  }
  await visitApi.complete(r.jobId, rid);
  r = { ...r, state: 'uploaded', error: null };
  await saveLocalRecording(r);
  report();
  return r;
}

/* ───────────── formatting ───────────── */

export function fmtClock(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export function fmtBytes(b: number) {
  if (b >= 1e9) return `${(b / 1e9).toFixed(2)} GB`;
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`;
  return `${Math.max(0, Math.round(b / 1e3))} KB`;
}

export const ROOM_PRESETS = ['Entrance', 'Living', 'Kitchen', 'Dining', 'Bedroom', 'Primary bedroom', 'Bathroom', 'Office', 'Hall', 'Laundry', 'Basement', 'Other'];
export const PREP_ITEMS = [
  { key: 'lights', title: 'All lights on', body: 'Every room, every lamp.' },
  { key: 'blinds', title: 'Blinds and curtains open', body: 'Even daylight, no dark corners.' },
  { key: 'screens', title: 'TVs and screens off', body: 'Lit screens reconstruct badly. Close laptops.' },
  { key: 'doors', title: 'Inside doors open', body: 'Walk room to room without stopping the video.' },
  { key: 'still', title: 'Nothing moves while recording', body: 'No people, pets or doors moving in shot.' },
] as const;
