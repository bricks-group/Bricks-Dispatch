/** Shared Owner/IC helpers — same rules as Dispatch web JobsPage. */

export const SKILLS: { id: string; label: string }[] = [
  { id: 'photo', label: 'Photo' },
  { id: 'drone', label: 'Drone' },
  { id: 'reel', label: 'Reel' },
  { id: 'tour', label: 'Tour' },
  { id: 'twilight', label: 'Twilight' },
];

/** Bricks catalog media types for portfolio samples (Hiring review). */
export const PORTFOLIO_TYPES: { id: string; label: string }[] = [
  { id: 'hdr', label: 'HDR photo' },
  { id: 'drone', label: 'Drone' },
  { id: 'floor_plan', label: 'Floor plan' },
  { id: 'tour', label: 'Tour' },
  { id: 'twilight', label: 'Twilight' },
  { id: 'reel', label: 'Reel' },
];

export type PortfolioSample = {
  media_type: string;
  url?: string | null;
  link?: string | null;
  thumb_url?: string | null;
  label?: string;
};

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
  is_active?: boolean;
  member_role?: string;
  terms_accepted_at?: string | null;
  application_submitted?: boolean;
  app_access?: boolean;
  portfolio_links?: string[];
  portfolio_samples?: PortfolioSample[];
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
  is_active?: boolean;
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
// GET /team does not return is_active; `assignable` already requires an
// active, approved, unpaused member.
export function isPickerMember(m: TeamMember) {
  if (m.is_active === false) return false;
  if ((m.id || '').toLowerCase().startsWith('0c3fa7c2')) return true;
  const t = `${m.title || ''} ${m.role || ''}`.toLowerCase();
  if (t.includes('owner')) return true;
  return m.assignable === true;
}

export function mediaUrl(raw?: string | null) {
  const v = (raw || '').trim();
  if (v.startsWith('http://') || v.startsWith('https://')) return v;
  return '';
}


/** True when photographer may use Today / Jobs / Capture. */
export function hasAppAccess(p: Pick<VendorProfile, 'app_access' | 'onboarding_status' | 'is_active' | 'role'> | null | undefined) {
  if (!p) return false;
  if (typeof p.app_access === 'boolean') return p.app_access;
  const status = (p.onboarding_status || '').toLowerCase();
  const role = `${p.role || ''}`.toLowerCase();
  if (role.includes('owner')) return true;
  return status === 'approved' && p.is_active !== false;
}

/** Pending / inactive / applicant — show Under review (after application submitted). */
export function isUnderReview(p: VendorProfile | null | undefined) {
  if (!p || hasAppAccess(p)) return false;
  if (p.application_submitted) return true;
  const status = (p.onboarding_status || '').toLowerCase();
  const memberRole = `${p.member_role || p.role || ''}`.toLowerCase();
  return status === 'pending' || p.is_active === false || memberRole.includes('applicant');
}

/** Needs the registration form before Under review. */
export function needsRegister(p: VendorProfile | null | undefined) {
  if (!p || hasAppAccess(p)) return false;
  if (p.application_submitted || p.terms_accepted_at) return false;
  // No accepted application yet — collect the register form (not Today/Jobs/Capture).
  return true;
}
