// Capture-flow API — Matterport-style 360 stations.
// JPEG bytes never go through POST /api/uploads (professional stills).
// Pan burst / equirect PUTs land at capture/{session}/stations/{n}/.

import { actorHeaders } from './api';
import { supabase } from './supabase';

const API_BASE = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
const CAPTURE_BUCKET = 'brxs-deliverables';

export type CaptureKind = 'splat' | 'floorplan';

export interface CaptureRoom {
  id?: string;
  name: string;
  tone: 'ok' | 'warn' | 'bad';
  n: number;
}

export interface PipelineStage {
  title: string;
  detail: string;
  status: 'done' | 'active' | 'todo';
}

export interface CaptureSession {
  sessionId?: string | null;
  currentRoomId?: string | null;
  address: string;
  meta: string;
  kind: CaptureKind;
  status?: string | null;
  rooms: CaptureRoom[];
  pipeline: PipelineStage[];
  frameCount: number;
  guidance?: { room: string; message: string };
  estimatedDeliveryHours?: number | null;
  outputStoragePath?: string | null;
  stationCount?: number;
  currentStationIndex?: number;
  stations?: CaptureStation[];
}

export interface CaptureStation {
  station_index: number;
  room?: string;
  panCount?: number;
  hasEquirect?: boolean;
  heading?: number | null;
  content_type?: string;
}

export type CaptureRole = 'pan' | 'equirect' | 'usdz' | 'roomplan';

export interface StationUploadOpts {
  roomId?: string | null;
  kind?: CaptureKind;
  stationIndex?: number;
  yaw?: number | null;
  heading?: number | null;
  poseX?: number | null;
  poseY?: number | null;
  role?: CaptureRole;
  contentType?: string;
}

function _emptySession(kind: CaptureKind = 'splat'): CaptureSession {
  return {
    sessionId: null,
    currentRoomId: null,
    address: 'Property',
    meta: '',
    kind,
    rooms: [],
    pipeline: [],
    frameCount: 0,
    guidance: undefined,
    estimatedDeliveryHours: undefined,
  };
}

function _asKind(raw: any): CaptureKind {
  const k = String(raw ?? 'splat').toLowerCase().replace(/[-_]/g, '');
  return k === 'floorplan' || k === 'floorplans' ? 'floorplan' : 'splat';
}

function _asSession(raw: any, fallbackKind: CaptureKind = 'splat'): CaptureSession {
  if (!raw || typeof raw !== 'object') return _emptySession(fallbackKind);
  const rooms: CaptureRoom[] = Array.isArray(raw.rooms)
    ? raw.rooms.map((r: any) => ({
        id: r.id,
        name: r.name ?? 'Room',
        tone: (r.tone ?? 'bad') as CaptureRoom['tone'],
        n: Number(r.n ?? r.frames ?? 0) || 0,
      }))
    : [];
  const pipeline: PipelineStage[] = Array.isArray(raw.pipeline)
    ? raw.pipeline.map((p: any) => ({
        title: p.title ?? '',
        detail: p.detail ?? '',
        status: (p.status ?? 'todo') as PipelineStage['status'],
      }))
    : [];
  return {
    sessionId: raw.sessionId ?? raw.session_id ?? null,
    currentRoomId: raw.currentRoomId ?? raw.current_room_id ?? null,
    address: raw.address || 'Property',
    meta: raw.meta || '',
    kind: _asKind(raw.kind ?? fallbackKind),
    status: raw.status ?? null,
    rooms,
    pipeline,
    frameCount: Number(raw.frameCount ?? raw.frame_count ?? 0) || 0,
    guidance: raw.guidance ?? undefined,
    estimatedDeliveryHours: raw.estimatedDeliveryHours ?? undefined,
    outputStoragePath: raw.outputStoragePath ?? raw.output_storage_path ?? null,
    stationCount: Number(raw.stationCount ?? raw.station_count ?? 0) || 0,
    currentStationIndex: Number(raw.currentStationIndex ?? raw.current_station_index ?? 0) || 0,
    stations: Array.isArray(raw.stations) ? raw.stations : [],
  };
}

async function _readError(res: Response): Promise<string> {
  try {
    const j = await res.json();
    if (typeof j?.detail === 'string') return j.detail;
    if (j?.detail?.message) return j.detail.message;
    if (j?.message) return j.message;
    if (j?.error) return j.error;
    return `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

export async function getCaptureSession(jobId: string, kind: CaptureKind = 'splat'): Promise<CaptureSession> {
  if (!jobId) return _emptySession(kind);
  try {
    const headers = await actorHeaders();
    const res = await fetch(
      `${API_BASE}/api/capture/sessions?job_id=${encodeURIComponent(jobId)}&kind=${encodeURIComponent(kind)}`,
      { headers },
    );
    if (!res.ok) return _emptySession(kind);
    return _asSession(await res.json(), kind);
  } catch {
    return _emptySession(kind);
  }
}

export async function ensureCaptureSession(jobId: string, kind: CaptureKind = 'splat'): Promise<CaptureSession> {
  if (!jobId) return _emptySession(kind);
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    const res = await fetch(`${API_BASE}/api/capture/sessions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ job_id: jobId, kind }),
    });
    if (!res.ok) return _emptySession(kind);
    return _asSession(await res.json(), kind);
  } catch {
    return _emptySession(kind);
  }
}

export async function addCaptureRoom(
  jobId: string,
  name: string,
  kind: CaptureKind = 'splat',
): Promise<{ ok: boolean; error?: string; session?: CaptureSession }> {
  if (!jobId) return { ok: false, error: 'Missing job' };
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    const res = await fetch(`${API_BASE}/api/capture/rooms`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ job_id: jobId, kind, name }),
    });
    if (!res.ok) return { ok: false, error: await _readError(res) };
    return { ok: true, session: _asSession(await res.json(), kind) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

export async function submitCaptureSession(
  jobId: string,
  kind: CaptureKind = 'splat',
): Promise<{ ok: boolean; error?: string; session?: CaptureSession }> {
  if (!jobId) return { ok: false, error: 'Missing job' };
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    const res = await fetch(`${API_BASE}/api/capture/submit`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ job_id: jobId, kind }),
    });
    if (!res.ok) return { ok: false, error: await _readError(res) };
    const body = await res.json();
    return { ok: true, session: _asSession(body.session ?? body, kind) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

export async function openCaptureStation(
  jobId: string,
  kind: CaptureKind = 'splat',
  opts?: { roomId?: string | null; room?: string; heading?: number | null; increment?: boolean },
): Promise<{ ok: boolean; error?: string; session?: CaptureSession }> {
  if (!jobId) return { ok: false, error: 'Missing job' };
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    const res = await fetch(`${API_BASE}/api/capture/stations`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        job_id: jobId,
        kind,
        room_id: opts?.roomId ?? undefined,
        room: opts?.room,
        heading: opts?.heading ?? undefined,
        new: opts?.increment ?? false,
      }),
    });
    if (!res.ok) return { ok: false, error: await _readError(res) };
    return { ok: true, session: _asSession(await res.json(), kind) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

export async function uploadCaptureFrame(
  jobId: string,
  fileUri: string,
  roomId?: string | null,
  kind: CaptureKind = 'splat',
  opts?: StationUploadOpts,
): Promise<{ ok: boolean; error?: string; session?: CaptureSession; frameCount?: number }> {
  if (!jobId) return { ok: false, error: 'Missing job' };
  if (!fileUri) return { ok: false, error: 'Missing photo' };
  const role: CaptureRole = opts?.role ?? 'pan';
  const contentType = opts?.contentType ?? (role === 'equirect' || role === 'pan' ? 'image/jpeg' : undefined);
  try {
    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    const signRes = await fetch(`${API_BASE}/api/capture/frames/sign`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        job_id: jobId,
        room_id: opts?.roomId ?? roomId ?? undefined,
        kind: opts?.kind ?? kind,
        station_index: opts?.stationIndex,
        yaw: opts?.yaw,
        heading: opts?.heading,
        pose_x: opts?.poseX,
        pose_y: opts?.poseY,
        role,
        content_type: contentType,
      }),
    });
    if (!signRes.ok) return { ok: false, error: await _readError(signRes) };
    const signed = await signRes.json();
    const path: string = signed.path;
    const token: string | undefined = signed.token;
    const signedUrl: string | undefined = signed.signed_url;
    const bucket: string = signed.bucket || CAPTURE_BUCKET;

    const fileRes = await fetch(fileUri);
    const bytes = await fileRes.arrayBuffer();

    let uploaded = false;
    if (token && path) {
      const { error: upErr } = await supabase.storage
        .from(bucket)
        .uploadToSignedUrl(path, token, bytes, { contentType: 'image/jpeg' });
      if (!upErr) uploaded = true;
      else if (!signedUrl) return { ok: false, error: upErr.message };
    }
    if (!uploaded) {
      if (!signedUrl) return { ok: false, error: 'No signed upload URL' };
      const put = await fetch(signedUrl, {
        method: 'PUT',
        headers: { 'Content-Type': 'image/jpeg' },
        body: bytes,
      });
      if (!put.ok) return { ok: false, error: `Storage PUT failed (${put.status})` };
    }

    const commitRes = await fetch(`${API_BASE}/api/capture/frames/commit`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        job_id: jobId,
        session_id: signed.session_id,
        room_id: signed.room_id ?? roomId ?? null,
        storage_path: path,
        kind: opts?.kind ?? kind,
        station_index: signed.station_index ?? opts?.stationIndex,
        yaw: opts?.yaw,
        heading: opts?.heading,
        pose_x: opts?.poseX,
        pose_y: opts?.poseY,
        role: signed.role ?? role,
        content_type: signed.content_type ?? contentType,
      }),
    });
    if (!commitRes.ok) return { ok: false, error: await _readError(commitRes) };
    const session = _asSession(await commitRes.json(), kind);
    return { ok: true, session, frameCount: session.frameCount };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
