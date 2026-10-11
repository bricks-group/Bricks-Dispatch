/**
 * GO web parity helpers for things the native GO app does:
 *  - in-app account deletion request (public.account_deletion_requests, RLS: own row only)
 *  - Google provider probe so a disabled provider never lands the vendor on a raw
 *    Supabase JSON error page
 * No React here so node --test can exercise it with a mocked client.
 */

export const SUPPORT_EMAIL = 'help@brxs.ca';
export const DELETION_INFO_URL = 'https://www.brxs.ca/account-deletion';
export const GOOGLE_UNAVAILABLE_MESSAGE =
  'Google sign-in is not switched on for Bricks yet. Use email and password, or email me a code.';

type FetchLike = (url: string, init?: { headers?: Record<string, string> }) => Promise<{ ok: boolean; json: () => Promise<unknown> }>;

/** true/false from GET /auth/v1/settings external[provider]; null when unknown. */
export async function fetchOAuthProviderEnabled(
  provider: 'google' | 'apple',
  supabaseUrl: string,
  anonKey: string,
  fetchImpl: FetchLike | null = typeof fetch !== 'undefined' ? (fetch as unknown as FetchLike) : null,
): Promise<boolean | null> {
  if (!supabaseUrl || !anonKey || !fetchImpl) return null;
  try {
    const res = await fetchImpl(`${supabaseUrl.replace(/\/$/, '')}/auth/v1/settings`, { headers: { apikey: anonKey } });
    if (!res || !res.ok) return null;
    const body = (await res.json()) as { external?: Record<string, unknown> | null } | null;
    if (!body || typeof body.external !== 'object' || body.external === null) return null;
    return body.external[provider] === true;
  } catch {
    return null;
  }
}

export function isProviderDisabledError(err: { message?: string; code?: string } | null | undefined): boolean {
  const s = `${err?.code || ''} ${err?.message || ''}`.toLowerCase();
  return s.includes('provider is not enabled') || s.includes('unsupported provider');
}

export type DeletionResult =
  | { ok: true; via: 'request'; already?: boolean }
  | { ok: false; error: string; mailto: string };

export function deletionMailto(email: string | null | undefined, userId: string | null | undefined, app = 'go'): string {
  const subject = encodeURIComponent('Delete my Bricks account');
  const body = encodeURIComponent(
    `Please delete my Bricks account.\n\nAccount email: ${email || ''}\nUser ID: ${userId || ''}\nApp: ${app} (web)\n`,
  );
  return `mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`;
}

type InsertClient = {
  from: (t: string) => { insert: (row: Record<string, unknown>) => PromiseLike<{ error: { code?: string; message?: string } | null }> };
};

/** Same row the native apps write, tagged app='go', platform='web' so ops can see where it came from. */
export async function requestAccountDeletion(
  client: InsertClient,
  user: { id?: string | null; email?: string | null } | null | undefined,
  reason?: string,
): Promise<DeletionResult> {
  if (!user?.id) return { ok: false, error: 'Sign in again to delete your account.', mailto: deletionMailto(null, null) };
  const mailto = deletionMailto(user.email, user.id);
  try {
    const { error } = await client.from('account_deletion_requests').insert({
      user_id: user.id,
      email: user.email ?? null,
      app: 'go',
      platform: 'web',
      reason: (reason || '').trim().slice(0, 500) || null,
    });
    if (!error) return { ok: true, via: 'request' };
    if (error.code === '23505') return { ok: true, via: 'request', already: true };
    return { ok: false, error: `Could not send the request (${error.message || error.code || 'error'}). Email ${SUPPORT_EMAIL} from your account email instead.`, mailto };
  } catch {
    return { ok: false, error: `Could not reach Bricks. Email ${SUPPORT_EMAIL} from your account email instead.`, mailto };
  }
}
