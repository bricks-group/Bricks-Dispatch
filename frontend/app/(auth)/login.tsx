import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { useRouter } from 'expo-router';
import { supabase } from '../../lib/supabase';
import { AUTH_CALLBACK_MESSAGES, parseAuthCallback } from '../../lib/oauthCallback';
import { color, type as T, space, radius, layout } from '../../theme';
import { Field, Button, Notice, Divider, TextLink } from '../../components/ui';
import BricksWordmark from '../../components/BricksWordmark';
import { EnterFadeDown } from '../../components/motion';
import { APPLE_SIGN_IN_ENABLED, AppleAuthentication, appleSignInAvailable, signInWithApple } from '../../lib/apple-auth';

WebBrowser.maybeCompleteAuthSession();

export default function LoginScreen() {
  const router = useRouter();
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [appleReady, setAppleReady] = useState(false);
  // App Store 4.8: on iOS, Google sign-in is only offered alongside Sign in with Apple.
  const showGoogle = Platform.OS !== 'ios' || APPLE_SIGN_IN_ENABLED;

  useEffect(() => { appleSignInAvailable().then(setAppleReady); }, []);

  const handleApple = async () => {
    setError('');
    try {
      const ok = await signInWithApple();
      if (ok) router.replace('/(tabs)');
    } catch (e: any) {
      setError(e?.message || 'Apple sign-in failed');
    }
  };

  const isFairUseAuthError = (authError: any) => {
    const msg = `${authError?.message || ''} ${authError?.status || ''}`.toLowerCase();
    return (
      msg.includes('402')
      || msg.includes('exceed_storage')
      || msg.includes('fair-use')
      || msg.includes('fair use')
      || msg.includes('restricted')
      || authError?.status === 402
    );
  };

  const applyFairUseSession = async (body: any, verb: string) => {
    const access = body.access_token;
    const refresh = body.refresh_token || access;
    if (!access) throw new Error(`Fair-Use ${verb} returned no token`);
    const { error: sessionError } = await supabase.auth.setSession({
      access_token: access,
      refresh_token: refresh,
    });
    if (sessionError) throw sessionError;
  };

  const fairUseDetail = (body: any, status: number, fallback: string) => {
    const detail = body?.detail;
    if (typeof detail === 'string') return detail;
    return detail?.message || body?.message || `${fallback} (${status})`;
  };

  const handleLogin = async () => {
    if (!email.trim() || !password) {
      setError('Enter your email and password');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const trimmed = email.trim().toLowerCase();
      const { error: authError } = await supabase.auth.signInWithPassword({
        email: trimmed,
        password,
      });
      if (authError) {
        if (!isFairUseAuthError(authError)) throw authError;
        const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
        const res = await fetch(`${API}/api/vendor-os/auth/fair-use-login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: trimmed, password }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(fairUseDetail(body, res.status, 'Login failed'));
        await applyFairUseSession(body, 'login');
      }
      router.replace('/(tabs)');
    } catch (e: any) {
      setError(e.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handleSignup = async () => {
    if (!email.trim() || !password) {
      setError('Enter your email and password');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const trimmed = email.trim().toLowerCase();
      const display = name.trim();
      const { data, error: authError } = await supabase.auth.signUp({
        email: trimmed,
        password,
        options: display ? { data: { full_name: display } } : undefined,
      });
      if (authError) {
        if (!isFairUseAuthError(authError)) throw authError;
        const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
        const res = await fetch(`${API}/api/vendor-os/auth/fair-use-signup`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: trimmed, password, name: display }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(fairUseDetail(body, res.status, 'Sign up failed'));
        await applyFairUseSession(body, 'signup');
      } else if (data?.session) {
        // immediate session (email confirm off)
      } else {
        // No session yet — try password sign-in (or Fair-Use path)
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email: trimmed,
          password,
        });
        if (signInError) {
          if (!isFairUseAuthError(signInError)) {
            throw new Error('Account created — check your email to confirm, then sign in.');
          }
          const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
          const res = await fetch(`${API}/api/vendor-os/auth/fair-use-login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: trimmed, password }),
          });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(fairUseDetail(body, res.status, 'Sign in after signup failed'));
          await applyFairUseSession(body, 'login');
        }
      }
      router.replace('/(tabs)');
    } catch (e: any) {
      setError(e.message || 'Sign up failed');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    setError('');
    setGoogleLoading(true);
    try {
      const redirectTo = Linking.createURL('auth-callback');
      const { data, error: oauthError } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo, skipBrowserRedirect: true },
      });
      if (oauthError || !data?.url) throw oauthError ?? new Error('No OAuth URL');
      const result = await WebBrowser.openAuthSessionAsync(data.url, redirectTo);
      if (result.type === 'success' && result.url) {
        // Exchange only a code returned on exactly the redirect we opened.
        const callback = parseAuthCallback(result.url, redirectTo);
        if (!callback.ok) {
          setError(AUTH_CALLBACK_MESSAGES[callback.reason]);
          return;
        }
        const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(callback.code);
        if (exchangeError) {
          setError('Google sign-in didn’t finish. Please try again.');
          return;
        }
        router.replace('/(tabs)');
      }
    } catch (e: any) {
      setError(e.message || 'Google sign-in failed');
    } finally {
      setGoogleLoading(false);
    }
  };

  const busy = loading || googleLoading;

  return (
    <SafeAreaView style={s.safe}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={s.flex}>
        <ScrollView contentContainerStyle={s.scroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <EnterFadeDown style={s.flex}>
            <View style={s.wordmarkWrap}>
              <BricksWordmark size={1.0} color={color.accent} pressable />
            </View>

            <Text style={[T.display, { color: color.text }]} accessibilityRole="header">
              {mode === 'signin' ? 'Sign in' : 'Create your account'}
            </Text>
            <Text style={[T.body, s.sub]}>
              {mode === 'signin' ? 'Your jobs, uploads and payouts.' : 'Shoot, upload, get paid.'}
            </Text>

            <View style={s.form}>
              {!!error && <Notice tone="bad">{error}</Notice>}

              {mode === 'signup' && (
                <Field
                  label="Name"
                  value={name}
                  onChangeText={setName}
                  placeholder="Your name"
                  autoCapitalize="words"
                  autoCorrect={false}
                  textContentType="name"
                />
              )}
              <Field
                label="Email"
                value={email}
                onChangeText={setEmail}
                placeholder="you@example.com"
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                textContentType="emailAddress"
              />
              <Field
                label="Password"
                value={password}
                onChangeText={setPassword}
                placeholder={mode === 'signin' ? 'Your password' : 'At least 8 characters'}
                secureTextEntry
                autoCapitalize="none"
                textContentType={mode === 'signin' ? 'password' : 'newPassword'}
              />

              <Button
                title={mode === 'signin' ? 'Sign in' : 'Create account'}
                onPress={mode === 'signin' ? handleLogin : handleSignup}
                loading={loading}
                disabled={busy}
                style={s.primary}
              />

              {(appleReady || showGoogle) ? (
                <View style={s.divider}>
                  <Divider style={s.flex} />
                  <Text style={[T.caption, { color: color.textFaint }]}>or</Text>
                  <Divider style={s.flex} />
                </View>
              ) : null}

              {appleReady ? (
                <AppleAuthentication.AppleAuthenticationButton
                  buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
                  buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.WHITE}
                  cornerRadius={radius.sm}
                  style={s.apple}
                  onPress={handleApple}
                />
              ) : null}

              {showGoogle ? (
                <Button variant="secondary" title="Continue with Google" onPress={handleGoogle} loading={googleLoading} disabled={busy} />
              ) : null}
            </View>

            <View style={s.footer}>
              {mode === 'signin' ? (
                <>
                  <View style={s.footerLine}>
                    <Text style={[T.bodySm, { color: color.textMuted }]}>New to Bricks?</Text>
                    <TextLink tone="accent" title="Create account" onPress={() => { setMode('signup'); setError(''); }} />
                  </View>
                  <TextLink title="Apply to shoot with Bricks" onPress={() => router.push('/onboarding/slides')} />
                </>
              ) : (
                <View style={s.footerLine}>
                  <Text style={[T.bodySm, { color: color.textMuted }]}>Already have an account?</Text>
                  <TextLink tone="accent" title="Sign in" onPress={() => { setMode('signin'); setError(''); }} />
                </View>
              )}
            </View>
          </EnterFadeDown>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: color.bg },
  flex: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: layout.gutter, paddingBottom: space[24] },
  wordmarkWrap: { paddingTop: space[24], paddingBottom: space[48] },
  sub: { color: color.textMuted, marginTop: space[8], marginBottom: space[32] },
  form: { gap: space[16] },
  primary: { marginTop: space[8] },
  divider: { flexDirection: 'row', alignItems: 'center', gap: space[12] },
  apple: { height: layout.control },
  footer: { marginTop: 'auto', paddingTop: space[36], alignItems: 'center' },
  footerLine: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', flexWrap: 'wrap' },
});
