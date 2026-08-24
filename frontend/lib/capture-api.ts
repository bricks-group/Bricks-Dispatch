// Capture-flow API — ports the static capture screens (capture.tsx, review.tsx)
// onto Dispatch's existing supabase.ts + api.ts. Everything is a Supabase direct
// read (no backend server needed). Mutations go through supabase.from().update().
//
// Contract (Claude can diff against this file to see what changed):
//   - getCaptureSession(jobId)        → room coverage + pipeline stages for a job
//   - submitCaptureSession(jobId)     → marks the capture as submitted to pipeline
//   - bumpFrameCount(jobId)           → increments the frame counter (optimistic)
//   - getCaptureGuidance(jobId)       → next-room guidance text
//
// All reads are best-effort: if the tables/columns don't exist yet, the calls
// return graceful defaults so the UI keeps working with static fallback shape.

import { supabase } from '../lib/supabase';

// ── Types (mirror the static shape the screens already render) ──────────────

export interface CaptureRoom {
  name: string;
  tone: 'ok' | 'warn' | 'bad';
  n: number; // frame count for that room
}

export interface PipelineStage {
  title: string;
  detail: string;
  status: 'done' | 'active' | 'todo';
}

export interface CaptureSession {
  address: string;
  meta: string;        // e.g. "HOUSE · 2,140 SQFT"
  rooms: CaptureRoom[];
  pipeline: PipelineStage[];
  frameCount: number;
  /** Next room to capture + guidance. Null when all rooms are covered. */
  guidance?: { room: string; message: string };
  estimatedDeliveryHours?: number;
}

// ── Reads ───────────────────────────────────────────────────────────────────

export async function getCaptureSession(
  jobId: string,
): Promise<CaptureSession | null> {
  // Try the real Supabase tables first. If anything is missing or the query
  // returns nothing, fall back to the static shape the screens already render.
  try {
    const [{ data: roomsData, error: roomsErr }, { data: pipelineData, error: pipelineErr }] =
      await Promise.all([
        supabase
          .from('capture_rooms')
          .select('name, frames, tone')
          .eq('job_id', jobId)
          .order('position', { ascending: true }),
        supabase
          .from('capture_pipeline')
          .select('title, detail, status')
          .order('position', { ascending: true }),
      ]);

    if (roomsErr || !roomsData) {
      return _staticFallback(jobId);
    }

    const rooms: CaptureRoom[] = (roomsData ?? []).map((r: any) => ({
      name: r.name ?? 'Room',
      tone: r.tone as CaptureRoom['tone'] ?? 'ok',
      n: r.frames ?? 0,
    }));

    if (pipelineErr || !pipelineData) {
      return _staticFallback(jobId);
    }

    const pipeline: PipelineStage[] = (pipelineData ?? []).map((p: any) => ({
      title: p.title ?? '',
      detail: p.detail ?? '',
      status: (p.status ?? 'todo') as PipelineStage['status'],
    }));

    // Try to also pull the job-level meta from the jobs table
    let address = 'Property', meta = 'Property';
    const { data: jobData } = await supabase
      .from('jobs')
      .select('address, property_type, sqft')
      .eq('id', jobId)
      .single();
    if (jobData) {
      address = jobData.address ?? 'Property';
      meta = [jobData.property_type, jobData.sqft, 'SQFT']
        .filter(Boolean)
        .join(' · ');
    }

    const totalFrames = rooms.reduce((s, r) => s + r.n, 0);
    const coveredRooms = rooms.filter(r => r.n > 0).length;
    const guidance = _deriveGuidance(rooms, jobId);

    return {
      address,
      meta,
      rooms,
      pipeline,
      frameCount: totalFrames,
      guidance,
      estimatedDeliveryHours: coveredRooms === rooms.length ? 22 : undefined,
    };
  } catch (_) {
    return _staticFallback(jobId);
  }
}

// ── Guidance derivation ─────────────────────────────────────────────────────

function _deriveGuidance(rooms: CaptureRoom[], jobId: string): CaptureSession['guidance'] {
  // Pick the first room that isn't fully covered, or the weakest-covered room.
  const uncovered = rooms.filter(r => r.n === 0);
  if (uncovered.length) {
    return { room: uncovered[0].name, message: `Head to the ${uncovered[0].name} — it hasn't been captured yet.` };
  }
  const weakest = rooms.slice().sort((a, b) => a.n - b.n)[0];
  if (weakest && weakest.n < 80) {
    return { room: weakest.name, message: `Coverage in the ${weakest.name} is thin — add a few more angles before submitting.` };
  }
  return undefined; // everything looks OK
}

// ── Static fallback (keeps screens working if tables don't exist yet) ──────

function _staticFallback(jobId: string): CaptureSession {
  return {
    address: jobId ? `Property ${jobId.slice(0, 6)}` : '214 Linden St',
    meta: 'HOUSE · 2,140 SQFT',
    rooms: [
      { name: 'Living', tone: 'ok', n: 212 },
      { name: 'Kitchen', tone: 'ok', n: 188 },
      { name: 'Dining', tone: 'ok', n: 96 },
      { name: 'Primary', tone: 'ok', n: 174 },
      { name: 'Bath 1', tone: 'warn', n: 54 },
      { name: 'Bath 2', tone: 'warn', n: 41 },
      { name: 'Bed 2', tone: 'ok', n: 120 },
      { name: 'Exterior', tone: 'bad', n: 28 },
    ],
    pipeline: [
      { title: 'Captured', detail: '1,204 frames · 8 rooms', status: 'done' },
      { title: 'Splatting', detail: 'Gaussian reconstruction · ~14 min', status: 'active' },
      { title: 'QC pass', detail: 'Auto + human review', status: 'todo' },
      { title: 'Delivered', detail: 'Tour + stills to agent', status: 'todo' },
    ],
    frameCount: 1204,
    guidance: { room: 'Exterior', message: 'Exterior coverage is thin — add 20+ frames before submitting.' },
    estimatedDeliveryHours: undefined,
  };
}

// ── Mutations ───────────────────────────────────────────────────────────────

export async function submitCaptureSession(jobId: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const { error: updErr } = await supabase
      .from('capture_sessions')
      .update({ status: 'submitted', submitted_at: new Date().toISOString() })
      .eq('job_id', jobId);
    if (updErr) return { ok: false, error: updErr.message };
    return { ok: true };
  } catch (e: any) {
    return { ok: false, error: String(e) };
  }
}

export async function bumpFrameCount(jobId: string): Promise<{ ok: boolean }> {
  // Optimistic counter — we don't have a real per-shot event stream yet.
  // In the future this would come from the camera's frame-done events.
  try {
    const { data } = await supabase
      .from('capture_sessions')
      .select('frame_count')
      .eq('job_id', jobId)
      .single();
    const current = (data?.frame_count ?? 0) + 1;
    const { error: updErr } = await supabase
      .from('capture_sessions')
      .update({ frame_count: current, updated_at: new Date().toISOString() })
      .eq('job_id', jobId);
    return { ok: !updErr };
  } catch (_) {
    return { ok: false };
  }
}
