import { API_BASE, api } from './api';
import { fileTooLargeMessage, isCameraRawName, UPLOAD_MAX_FILE_BYTES } from './bracket-require';
import { supabase } from './supabase';
import { putUpload, tusUpload, type TusTarget, type UploadProgress } from './tus-upload';

export const STILLS_TYPES = [
  { id: 'hdr', label: 'HDR still' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'twilight', label: 'Twilight' },
  { id: 'staging', label: 'Staging' },
  { id: 'floorplan', label: 'Floor plan' },
] as const;

export type StillsType = (typeof STILLS_TYPES)[number]['id'];

/** File picker accept list: camera RAW first, then JPEG/HEIC, video, plans. */
export const STILLS_ACCEPT = [
  '.cr2', '.cr3', '.nef', '.nrw', '.arw', '.raf', '.orf', '.rw2', '.dng', '.pef', '.srw',
  'image/x-canon-cr2', 'image/x-canon-cr3', 'image/x-nikon-nef', 'image/x-sony-arw', 'image/x-fuji-raf',
  'image/x-olympus-orf', 'image/x-panasonic-rw2', 'image/x-adobe-dng', 'image/x-pentax-pef', 'image/x-samsung-srw',
  'image/jpeg', 'image/png', 'image/tiff', 'image/webp', 'image/heic', 'image/heif', 'application/pdf', 'video/*',
  '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.heic', '.heif', '.pdf', '.mp4', '.mov',
].join(',');

type SignResp = {
  upload_id: string;
  storage_path: string;
  signed_url: string;
  content_type?: string;
  media_type: string;
  filename?: string;
  headers?: Record<string, string>;
  tus?: (TusTarget & { min_bytes?: number }) | null;
  max_file_bytes?: number;
  camera_raw?: boolean;
};

export type RawPreview = {
  status: 'ready' | 'needs_review' | 'pending';
  preview_url?: string | null;
  message?: string;
};

export type UploadedStill = {
  id: string;
  filename: string;
  media_type?: string;
  camera_raw?: boolean;
  preview?: RawPreview;
};

export class StillsUploadError extends Error {
  code?: string;
  constructor(message: string, code?: string) {
    super(message);
    this.code = code;
  }
}

async function authHeaders(): Promise<HeadersInit> {
  const { data: { session } } = await supabase.auth.getSession();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

function errMessage(body: { detail?: string | { message?: string; code?: string }; message?: string }, fallback: string) {
  const d = body.detail;
  return (
    (typeof d === 'string' && d) ||
    (d && typeof d === 'object' && typeof d.message === 'string' && d.message) ||
    body.message ||
    fallback
  );
}

const VIDEO_NAME = /\.(mp4|mov|m4v|webm|avi)$/i;

/** Reel / video master — gets the API's larger video cap, not the 50 MB stills one. */
export function isVideoUpload(file: { name: string; type?: string }, mediaType?: string): boolean {
  return mediaType === 'reel' || (file.type || '').startsWith('video/') || VIDEO_NAME.test(file.name);
}

/**
 * Camera RAW / JPEG / drone / reel / twilight / staging / floorplan → storage,
 * direct from the browser (TUS resumable for big files, else one signed PUT).
 * Never through a Vercel function. Always pass the camera filename (stem + ±EV)
 * so backend group_brackets can cluster.
 */
export async function uploadStillFile(
  jobId: string,
  file: File,
  mediaType: StillsType,
  onProgress?: UploadProgress,
): Promise<UploadedStill> {
  // Reels / video masters: the API mints the cap (up to 2 GB when storage
  // allows) and 413s with the exact limit — don't pre-block at the stills 50 MB.
  if (!isVideoUpload(file, mediaType) && file.size > UPLOAD_MAX_FILE_BYTES) {
    throw new StillsUploadError(fileTooLargeMessage(file.name), 'file_too_large');
  }
  const mime = file.type || 'application/octet-stream';
  const signRes = await fetch(`${API_BASE}/api/uploads/sign`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({
      job_id: jobId,
      filename: file.name,
      mime_type: mime,
      file_size: file.size,
      media_type: mediaType,
    }),
  });
  const sign = (await signRes.json().catch(() => ({}))) as SignResp & {
    detail?: string | { message?: string; code?: string };
    message?: string;
  };
  if (!signRes.ok || !sign.signed_url) {
    const code = typeof sign.detail === 'object' ? sign.detail?.code : undefined;
    throw new StillsUploadError(errMessage(sign, 'Could not start the upload'), code);
  }
  if (sign.max_file_bytes && file.size > sign.max_file_bytes) {
    throw new StillsUploadError(fileTooLargeMessage(file.name, sign.max_file_bytes), 'file_too_large');
  }
  const contentType = sign.content_type || mime;
  const useTus = !!sign.tus?.endpoint && file.size >= (sign.tus.min_bytes ?? 6 * 1024 * 1024);
  try {
    if (useTus) {
      await tusUpload(file, sign.tus as TusTarget, onProgress);
    } else {
      await putUpload(sign.signed_url, file, sign.headers || { 'Content-Type': contentType }, onProgress);
    }
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 413) throw new StillsUploadError(fileTooLargeMessage(file.name, sign.max_file_bytes || UPLOAD_MAX_FILE_BYTES), 'file_too_large');
    if (useTus) {
      // TUS refused (old storage host / CORS): one plain signed PUT instead.
      await putUpload(sign.signed_url, file, sign.headers || { 'Content-Type': contentType }, onProgress).catch(
        (err) => {
          const st = (err as { status?: number }).status;
          throw new StillsUploadError(
            st === 413 ? fileTooLargeMessage(file.name, sign.max_file_bytes || UPLOAD_MAX_FILE_BYTES) : `Upload failed for ${file.name}. Try again.`,
          );
        },
      );
    } else {
      throw new StillsUploadError(`Upload failed for ${file.name}. Try again.`);
    }
  }
  const rec = await api.post<UploadedStill>('/api/uploads/complete', {
    job_id: jobId,
    upload_id: sign.upload_id,
    storage_path: sign.storage_path,
    filename: file.name,
    mime_type: mime,
    file_size: file.size,
    media_type: mediaType || sign.media_type,
  });
  const cameraRaw = !!sign.camera_raw || isCameraRawName(file.name);
  return { ...rec, camera_raw: cameraRaw, preview: cameraRaw ? { status: 'pending' } : undefined };
}

/** Background step after a RAW upload: working JPEG + preview made server-side. */
export async function requestRawPreview(jobId: string, uploadId: string): Promise<RawPreview> {
  try {
    const res = await api.post<{ status?: string; preview_url?: string; message?: string; ok?: boolean }>(
      `/api/uploads/${uploadId}/preview`,
      { job_id: jobId },
    );
    if (res.status === 'needs_review') return { status: 'needs_review', message: res.message };
    return { status: 'ready', preview_url: res.preview_url || null };
  } catch {
    // Post-production makes it later anyway; never block the vendor.
    return { status: 'pending' };
  }
}
