/**
 * Minimal TUS 1.0 client (create + PATCH chunks + resume) for direct browser →
 * Supabase Storage uploads with a signed upload token (x-signature). Files go
 * straight to storage, never through a Vercel function. Supabase requires
 * 6 MB chunks. A dropped connection resumes from the server's offset.
 */

export type TusTarget = {
  endpoint: string;
  headers?: Record<string, string>;
  metadata?: Record<string, string>;
  chunk_size?: number;
};

export type UploadProgress = (sent: number, total: number) => void;

const TUS_VERSION = '1.0.0';
const RETRY_DELAYS_MS = [0, 1000, 3000, 6000, 10000];

function b64(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin);
}

function encodeMetadata(meta: Record<string, string>): string {
  return Object.entries(meta)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k} ${b64(String(v))}`)
    .join(',');
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class UploadError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

async function createUpload(target: TusTarget, file: Blob): Promise<string> {
  const res = await fetch(target.endpoint, {
    method: 'POST',
    headers: {
      ...(target.headers || {}),
      'Tus-Resumable': TUS_VERSION,
      'Upload-Length': String(file.size),
      'Upload-Metadata': encodeMetadata(target.metadata || {}),
    },
  });
  if (res.status !== 201) {
    const text = await res.text().catch(() => '');
    throw new UploadError(text || `Upload could not start (${res.status})`, res.status);
  }
  const loc = res.headers.get('Location');
  if (!loc) throw new UploadError('Upload could not start (no location)');
  return new URL(loc, target.endpoint).toString();
}

async function serverOffset(url: string, target: TusTarget): Promise<number> {
  const res = await fetch(url, {
    method: 'HEAD',
    headers: { ...(target.headers || {}), 'Tus-Resumable': TUS_VERSION },
  });
  if (!res.ok) throw new UploadError(`Upload lost (${res.status})`, res.status);
  return Number(res.headers.get('Upload-Offset') || 0);
}

export async function tusUpload(file: Blob, target: TusTarget, onProgress?: UploadProgress): Promise<void> {
  const chunk = target.chunk_size || 6 * 1024 * 1024;
  let url = '';
  let offset = 0;
  let attempt = 0;
  while (offset < file.size || !url) {
    try {
      if (!url) {
        url = await createUpload(target, file);
        offset = 0;
        if (file.size === 0) return;
      }
      const end = Math.min(offset + chunk, file.size);
      const res = await fetch(url, {
        method: 'PATCH',
        headers: {
          ...(target.headers || {}),
          'Tus-Resumable': TUS_VERSION,
          'Upload-Offset': String(offset),
          'Content-Type': 'application/offset+octet-stream',
        },
        body: file.slice(offset, end),
      });
      if (res.status === 409 || res.status === 423) {
        offset = await serverOffset(url, target);
        continue;
      }
      if (res.status !== 204 && res.status !== 200) {
        const text = await res.text().catch(() => '');
        throw new UploadError(text || `Upload failed (${res.status})`, res.status);
      }
      offset = Number(res.headers.get('Upload-Offset') || end);
      attempt = 0;
      onProgress?.(offset, file.size);
    } catch (e) {
      const status = (e as UploadError).status;
      // 4xx other than conflict/timeouts: do not retry (bad token, too large…).
      if (status && status >= 400 && status < 500 && ![408, 409, 423, 429].includes(status)) throw e;
      attempt += 1;
      if (attempt >= RETRY_DELAYS_MS.length) throw e;
      await sleep(RETRY_DELAYS_MS[attempt]);
      if (url) {
        try {
          offset = await serverOffset(url, target);
        } catch {
          url = '';
        }
      }
    }
  }
}

/** One signed PUT with progress (XHR gives upload progress; fetch does not). */
export function putUpload(
  url: string,
  file: Blob,
  headers: Record<string, string>,
  onProgress?: UploadProgress,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url);
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress?.(e.loaded, e.total); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new UploadError(xhr.responseText || `Upload failed (${xhr.status})`, xhr.status));
    };
    xhr.onerror = () => reject(new UploadError('Network error during upload'));
    xhr.send(file);
  });
}
