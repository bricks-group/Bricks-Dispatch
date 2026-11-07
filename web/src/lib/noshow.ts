import { api } from './api';

/** Server readiness for reporting a client no-show (GET /noshow-status). */
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

export class LocationError extends Error {}

/** One GPS fix for check-in. Rejects with plain copy when location is off or denied. */
export function getCheckInFix(): Promise<{ lat: number; lng: number; accuracy_m: number }> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new LocationError(`${LOCATION_REQUIRED_COPY} This browser can’t share location. Try the Bricks app.`));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy_m: Math.round(pos.coords.accuracy || 0),
      }),
      (err) => {
        const why = err.code === err.PERMISSION_DENIED
          ? 'Allow location for this site in your browser settings, then try again.'
          : 'We couldn’t get your location. Move outside or near a window and try again.';
        reject(new LocationError(`${LOCATION_REQUIRED_COPY} ${why}`));
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 30000 },
    );
  });
}

/** Downscale a camera photo to a JPEG under the API's 4 MB limit. */
export async function compressPhoto(file: File, maxSide = 1600, quality = 0.8): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('That photo couldn’t be read. Take it again.'));
      el.src = url;
    });
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext('2d');
    if (!ctx) return file;
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
    return blob || file;
  } finally {
    URL.revokeObjectURL(url);
  }
}

export const noShowApi = {
  status: (jobId: string) => api.get<NoShowStatus>(`/api/vendor-os/jobs/${jobId}/noshow-status`),
  callAttempt: (jobId: string, method: 'dialer' | 'no_number') =>
    api.post<NoShowStatus>(`/api/vendor-os/jobs/${jobId}/call-attempt`, { method }),
  photo: (jobId: string, blob: Blob, takenAt: string) => {
    const form = new FormData();
    form.append('photo', blob, 'property.jpg');
    form.append('taken_at', takenAt);
    return api.postForm<NoShowStatus>(`/api/vendor-os/jobs/${jobId}/noshow-photo`, form);
  },
};

export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
