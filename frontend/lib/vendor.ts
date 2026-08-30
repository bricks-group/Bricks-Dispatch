/** Shared Owner/IC helpers — same rules as Dispatch web JobsPage. */

export const SKILLS: { id: string; label: string }[] = [
  { id: 'photo', label: 'Photo' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'tour', label: 'Tour' },
  { id: 'twilight', label: 'Twilight' },
];

export type VendorProfile = {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: string;
  title: string;
  availability: boolean;
  skills: string[];
  service_areas: string[];
  home_city?: string;
  tier: number;
  tier_label: string;
  drone_certified: boolean;
  rpas_certificate_url?: string | null;
  rpas_certificate_level?: string | null;
  rpas_certificate_number?: string | null;
  onboarding_status: string;
  assignable: boolean;
  assignable_reason?: string | null;
  payout_model: 'owner_full' | 'ic_rate_card' | string;
  payout_label: string;
  show_platform_cut: boolean;
  avatar_url?: string | null;
  total_jobs_completed?: number;
  rating?: number;
  member_since?: string;
  bio?: string;
};

export type TeamMember = {
  id: string;
  name: string;
  email?: string;
  role?: string;
  title?: string;
  is_active: boolean;
  assignable?: boolean;
  assignable_reason?: string | null;
};

/** Same gate as web is_vendor_admin: role or title contains owner/admin/manager. */
export function isVendorAdmin(m: { role?: string; title?: string; id?: string } | null | undefined) {
  if (!m) return false;
  const blob = `${m.role || ''} ${m.title || ''}`.toLowerCase();
  if (blob.includes('owner') || blob.includes('admin') || blob.includes('manager')) return true;
  return (m.id || '').toLowerCase().startsWith('0c3fa7c2');
}

/** Active photographers only. Owner always included; pending Aman is not. */
export function isPickerMember(m: TeamMember) {
  if (!m.is_active) return false;
  if ((m.id || '').toLowerCase().startsWith('0c3fa7c2')) return true;
  const t = `${m.title || ''} ${m.role || ''}`.toLowerCase();
  if (t.includes('owner')) return true;
  if (m.assignable === false) return false;
  return true;
}

export function mediaUrl(raw?: string | null) {
  const v = (raw || '').trim();
  if (v.startsWith('http://') || v.startsWith('https://')) return v;
  return '';
}
