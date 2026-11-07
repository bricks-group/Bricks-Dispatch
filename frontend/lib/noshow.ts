/**
 * Client no-show flow for a job: GPS check-in, wait countdown, Call client logging,
 * property photo, and the server's readiness to report. The API enforces every gate;
 * this only mirrors it so the screen can say what's missing.
 *
 * Uses only native modules already in the store binary (expo-location, expo-image-picker).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Linking } from 'react-native';
import * as Location from 'expo-location';
import * as ImagePicker from 'expo-image-picker';
import { actorHeaders } from './api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export type NoShowStatus = {
  checked_in: boolean;
  checked_in_at: string | null;
  booked_start_at: string | null;
  wait_started_at: string | null;
  ready_at: string | null;
  expires_at: string | null;
  waited: boolean;
  has_photo: boolean;
  has_call: boolean;
  expired: boolean;
  can_report: boolean;
  missing: string[];
  wait_minutes: number;
  checkin_radius_m: number;
  check_in_distance_m: number | null;
  call_method: string | null;
  photo_uploaded_at: string | null;
  now: string;
  client_phone?: string | null;
};

export const LOCATION_REQUIRED_COPY =
  'Location is required to check in. It is only used to confirm you’re at the property.';

export const NOSHOW_RULE_COPY = (mins = 20) =>
  `If the client doesn’t show, you’re paid half the job’s payout. The ${mins}-minute wait starts at check-in or the booked start, whichever is later. Take a photo and try to call the client while you wait.`;

export const NOSHOW_REVIEW_COPY =
  'Bricks reviews the report within 2 business days. You can appeal within 7 days by emailing help@brxs.ca.';

async function readError(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({} as any));
  const d = body?.detail;
  if (d && typeof d === 'object' && typeof d.message === 'string') return d.message;
  if (typeof d === 'string' && d) return d;
  return `Something went wrong (${res.status}). Try again.`;
}

async function call<T>(path: string, init: RequestInit = {}, json = true): Promise<T> {
  const headers = await actorHeaders(json && init.body ? { 'Content-Type': 'application/json' } : undefined);
  const res = await fetch(`${API}${path}`, { ...init, headers: { ...(headers as any), ...(init.headers as any) } });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

/** One GPS fix. Throws plain copy when location is off or denied. */
export async function getCheckInFix(): Promise<{ lat: number; lng: number; accuracy_m: number }> {
  let perm: Location.LocationPermissionResponse;
  try {
    perm = await Location.requestForegroundPermissionsAsync();
  } catch {
    throw new Error(`${LOCATION_REQUIRED_COPY} Turn on location for Bricks and try again.`);
  }
  if (perm.status !== 'granted') {
    throw new Error(`${LOCATION_REQUIRED_COPY} Allow location for Bricks in Settings, then try again.`);
  }
  try {
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
    return {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      accuracy_m: Math.round(pos.coords.accuracy || 0),
    };
  } catch {
    throw new Error('We couldn’t get your location. Move outside or near a window and try again.');
  }
}

export function formatCountdown(secs: number): string {
  const s = Math.max(0, Math.ceil(secs));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** No-show state for one job. `status` is the job status so it re-reads after check-in. */
export function useNoShow(jobId: string | undefined, jobStatus: string | undefined) {
  const [ns, setNs] = useState<NoShowStatus | null>(null);
  const [skewMs, setSkewMs] = useState(0);
  const [nowMs, setNowMs] = useState(Date.now());
  const [busy, setBusy] = useState<'' | 'checkin' | 'call' | 'photo' | 'report'>('');

  const apply = useCallback((next: NoShowStatus) => {
    setNs((prev) => ({ ...next, client_phone: next.client_phone ?? prev?.client_phone ?? null }));
    const serverNow = Date.parse(next.now);
    if (Number.isFinite(serverNow)) setSkewMs(serverNow - Date.now());
  }, []);

  const refresh = useCallback(async () => {
    if (!jobId) return;
    try { apply(await call<NoShowStatus>(`/api/vendor-os/jobs/${jobId}/noshow-status`)); } catch { /* keep last */ }
  }, [apply, jobId]);

  useEffect(() => {
    if (jobStatus && ['assigned', 'in_progress'].includes(jobStatus)) void refresh();
  }, [jobId, jobStatus, refresh]);

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const readyMs = ns?.ready_at ? Date.parse(ns.ready_at) : NaN;
  const leftSecs = Number.isFinite(readyMs) ? (readyMs - (nowMs + skewMs)) / 1000 : NaN;
  const waitDone = !!ns?.waited || (Number.isFinite(leftSecs) && leftSecs <= 0);
  const flipped = useRef(false);
  useEffect(() => {
    if (ns && !ns.waited && waitDone && !flipped.current) { flipped.current = true; void refresh(); }
    if (!waitDone) flipped.current = false;
  }, [ns, waitDone, refresh]);

  /** GPS check-in. Resolves with the API's response (status etc.). */
  const checkIn = useCallback(async () => {
    if (!jobId) throw new Error('No job.');
    setBusy('checkin');
    try {
      const fix = await getCheckInFix();
      const out = await call<any>(`/api/vendor-os/jobs/${jobId}/check-in`, {
        method: 'POST', body: JSON.stringify(fix),
      });
      if (out?.noshow) apply(out.noshow);
      void refresh();
      return out;
    } finally { setBusy(''); }
  }, [apply, jobId, refresh]);

  const callClient = useCallback(async () => {
    if (!jobId) return;
    setBusy('call');
    try {
      const phone = ns?.client_phone || null;
      apply(await call<NoShowStatus>(`/api/vendor-os/jobs/${jobId}/call-attempt`, {
        method: 'POST', body: JSON.stringify({ method: phone ? 'dialer' : 'no_number' }),
      }));
      if (phone) await Linking.openURL(`tel:${phone.replace(/[^\d+]/g, '')}`).catch(() => {});
    } finally { setBusy(''); }
  }, [apply, jobId, ns?.client_phone]);

  /** Opens the camera. Returns false when the vendor cancels. */
  const takePhoto = useCallback(async (): Promise<boolean> => {
    if (!jobId) return false;
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (perm.status !== 'granted') {
      throw new Error('Allow camera access for Bricks in Settings to take the photo.');
    }
    const shot = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.4, allowsEditing: false, exif: false });
    if (shot.canceled || !shot.assets?.length) return false;
    const takenAt = new Date().toISOString();
    const asset = shot.assets[0];
    setBusy('photo');
    try {
      const form = new FormData();
      form.append('photo', { uri: asset.uri, name: 'property.jpg', type: asset.mimeType || 'image/jpeg' } as any);
      form.append('taken_at', takenAt);
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/vendor-os/jobs/${jobId}/noshow-photo`, { method: 'POST', headers, body: form });
      if (!res.ok) throw new Error(await readError(res));
      apply(await res.json());
      return true;
    } finally { setBusy(''); }
  }, [apply, jobId]);

  const report = useCallback(async (notes: string) => {
    if (!jobId) throw new Error('No job.');
    setBusy('report');
    try {
      const body: { notes?: string } = {};
      if (notes.trim()) body.notes = notes.trim();
      return await call<any>(`/api/vendor-os/jobs/${jobId}/agent-no-show`, { method: 'POST', body: JSON.stringify(body) });
    } catch (e) {
      void refresh();
      throw e;
    } finally { setBusy(''); }
  }, [jobId, refresh]);

  const missing = (ns?.missing || []).filter((m) => !(waitDone && m.startsWith('Wait ')));

  return { ns, leftSecs, waitDone, busy, missing, refresh, checkIn, callClient, takePhoto, report };
}
