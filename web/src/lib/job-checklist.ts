import { api } from './api';

/** Trade job checklist (cleaner, stager, de-staging, repairs, inspector, measurer). */
export type ChecklistProof = 'tick' | 'photo' | 'inventory' | 'report' | 'findings';

export type ChecklistPhoto = { id: string; uploaded_at?: string | null; url?: string | null };
export type InventoryRow = { item: string; room: string; note?: string };

export type TradeChecklistItem = {
  id: string;
  label: string;
  completed: boolean;
  proof: ChecklistProof;
  photos: ChecklistPhoto[];
  inventory?: InventoryRow[];
  report?: { filename?: string | null; content_type?: string | null; uploaded_at?: string | null; url?: string | null } | null;
  note?: string | null;
  none_found?: boolean;
  photo_required?: boolean;
  /** What's still needed, in plain words, or null when the item is done. */
  missing?: string | null;
};

export type TradeChecklist = {
  job_id: string;
  title: string;
  editable: boolean;
  items: TradeChecklistItem[];
  complete: boolean;
  missing: string[];
  max_upload_bytes: number;
};

export type ChecklistPatch = {
  completed?: boolean;
  note?: string;
  none_found?: boolean;
  inventory?: InventoryRow[];
};

const base = (jobId: string) => `/api/vendor-os/jobs/${jobId}/checklist`;

export const checklistApi = {
  get: (jobId: string) => api.get<TradeChecklist>(base(jobId)),
  patch: (jobId: string, itemId: string, body: ChecklistPatch) =>
    api.patch<TradeChecklist>(`${base(jobId)}/${itemId}`, body),
  addPhoto: (jobId: string, itemId: string, blob: Blob, takenAt?: string) => {
    const form = new FormData();
    form.append('photo', blob, 'photo.jpg');
    if (takenAt) form.append('taken_at', takenAt);
    return api.postForm<TradeChecklist>(`${base(jobId)}/${itemId}/photos`, form);
  },
  removePhoto: (jobId: string, itemId: string, photoId: string) =>
    api.del<TradeChecklist>(`${base(jobId)}/${itemId}/photos/${photoId}`),
  uploadReport: (jobId: string, itemId: string, file: File) => {
    const form = new FormData();
    form.append('report', file, file.name || 'report.pdf');
    return api.postForm<TradeChecklist>(`${base(jobId)}/${itemId}/report`, form);
  },
};

export const CHECKLIST_MAX_BYTES = 4 * 1024 * 1024;
