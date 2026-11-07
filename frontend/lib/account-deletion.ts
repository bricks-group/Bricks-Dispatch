// In-app account deletion (App Store Guideline 5.1.1(v), Google Play account-deletion policy).
//
// Writes a row to public.account_deletion_requests in the Bricks Supabase project
// (Canada, rbywjnnctjglhcdemnnj) with the signed-in user's own JWT. RLS lets a user
// insert/read only their own request. Ops completes deletion within 30 days and keeps only
// records the law requires (tax/payout records). Migration:
//   supabase/migrations/20261011140000_account_deletion_requests.sql
//
// If the table is not reachable (migration not applied yet, offline), we fall back to a
// pre-filled email to privacy@brxs.ca so the user is never left without a path.
import { Linking, Platform } from 'react-native';
import { supabase } from './supabase';

export const DELETION_INFO_URL = 'https://www.brxs.ca/account-deletion';
export const SUPPORT_EMAIL = 'privacy@brxs.ca';

export type DeletionResult = { ok: true; via: 'request' } | { ok: true; via: 'email' } | { ok: false; error: string };

export async function requestAccountDeletion(app: 'go' | 'os', reason?: string): Promise<DeletionResult> {
  const { data: { session } } = await supabase.auth.getSession();
  const user = session?.user;
  if (!user) return { ok: false, error: 'Sign in again to delete your account.' };

  const { error } = await supabase.from('account_deletion_requests').insert({
    user_id: user.id,
    email: user.email ?? null,
    app,
    platform: Platform.OS,
    reason: reason?.slice(0, 500) || null,
  });
  if (!error) return { ok: true, via: 'request' };

  // Duplicate request: treat as success (one open request per user).
  if ((error as any).code === '23505') return { ok: true, via: 'request' };

  const subject = encodeURIComponent('Delete my Bricks account');
  const body = encodeURIComponent(
    `Please delete my Bricks account.\n\nAccount email: ${user.email ?? ''}\nUser ID: ${user.id}\nApp: ${app}\n`,
  );
  try {
    await Linking.openURL(`mailto:${SUPPORT_EMAIL}?subject=${subject}&body=${body}`);
    return { ok: true, via: 'email' };
  } catch {
    return { ok: false, error: `Could not send the request. Email ${SUPPORT_EMAIL} from your account email.` };
  }
}
