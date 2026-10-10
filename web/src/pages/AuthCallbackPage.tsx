import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { isPhoneUser, isVendorMemberMissing, me } from '../lib/api';
import { supabase } from '../lib/supabase';

function oauthErrorFromLocation(): string | null {
  const search = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const code = search.get('error') || hash.get('error');
  if (!code) return null;
  const description = search.get('error_description') || hash.get('error_description');
  return description || code;
}

/** Consume PKCE ?code= or implicit #access_token after Google sign-in. */
async function consumeUrlSession() {
  const { data: { session: existing } } = await supabase.auth.getSession();
  if (existing) return existing;

  const search = new URLSearchParams(window.location.search);
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));

  const pkceCode = search.get('code');
  if (pkceCode) {
    const { data, error } = await supabase.auth.exchangeCodeForSession(pkceCode);
    if (!error && data?.session) return data.session;
  }

  const access_token = hash.get('access_token');
  const refresh_token = hash.get('refresh_token');
  if (access_token && refresh_token) {
    const { data, error } = await supabase.auth.setSession({ access_token, refresh_token });
    if (!error && data?.session) return data.session;
  }

  return null;
}

/**
 * Lands here after Google OAuth for sign-in (go.brxs.ca/auth/callback).
 * Not /auth/google-calendar — that path requests calendar scopes.
 * Session is the same Supabase JWT the rest of GO web already sends to /api/vendor-os/*.
 */
export function AuthCallbackPage() {
  const hasProcessed = useRef(false);
  const navigate = useNavigate();
  const [status, setStatus] = useState('Finishing sign-in…');

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    let cancelled = false;
    let subscription: { unsubscribe: () => void } | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fail = (reason: string) => {
      if (cancelled) return;
      navigate(`/login?error=${encodeURIComponent(reason || 'Sign-in failed')}`, { replace: true });
    };

    const succeed = async (session: { access_token?: string } | null) => {
      if (cancelled || !session?.access_token) return false;
      try {
        await me();
        if (cancelled) return true;
        navigate('/', { replace: true });
        return true;
      } catch (e) {
        // Phone-OTP account (e.g. after the add-email confirmation link): GO shows
        // Add your email / Not on the team instead of a Google-specific error.
        if (isVendorMemberMissing(e) && await isPhoneUser()) {
          if (!cancelled) navigate('/', { replace: true });
          return true;
        }
        const msg = (e as Error).message || 'Could not load your account.';
        // Honest: Google worked but vendor member row missing / forbidden.
        fail(msg.includes('Vendor member')
          ? 'Google signed you in, but this email is not on the GO team yet. Ask an owner to add you.'
          : msg);
        return true;
      }
    };

    (async () => {
      const oauthError = oauthErrorFromLocation();
      if (oauthError) {
        fail(oauthError);
        return;
      }

      const session = await consumeUrlSession();
      if (await succeed(session)) return;

      const { data } = supabase.auth.onAuthStateChange(async (_event, nextSession) => {
        if (nextSession) {
          if (subscription) subscription.unsubscribe();
          if (timer) clearTimeout(timer);
          await succeed(nextSession);
        }
      });
      subscription = data.subscription;

      timer = setTimeout(() => fail('Sign-in timed out. Try again.'), 8000);
      setStatus('Waiting for Google…');
    })();

    return () => {
      cancelled = true;
      if (subscription) subscription.unsubscribe();
      if (timer) clearTimeout(timer);
    };
  }, [navigate]);

  return (
    <p style={{ padding: 28, color: 'var(--c-ink-mute)' }}>{status}</p>
  );
}
