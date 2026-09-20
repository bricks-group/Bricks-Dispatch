import { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';
import * as Linking from 'expo-linking';
import { useRouter } from 'expo-router';
import { supabase } from '../../lib/supabase';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';
import BricksWordmark from '../../components/BricksWordmark';

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
        const url = new URL(result.url);
        const code = url.searchParams.get('code');
        if (code) {
          await supabase.auth.exchangeCodeForSession(code);
          router.replace('/(tabs)');
        }
      }
    } catch (e: any) {
      setError(e.message || 'Google sign-in failed');
    } finally {
      setGoogleLoading(false);
    }
  };

  return (
    <SafeAreaView style={s.safe}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={s.flex}
      >
        <ScrollView
          contentContainerStyle={s.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={s.wordmarkWrap}>
            <BricksWordmark size={1.0} color={TOKENS.brick} pressable />
          </View>

          <Text style={s.eyebrow}>DISPATCH</Text>
          <Text style={s.heading}>{mode === 'signin' ? 'Sign in.' : 'Create account.'}</Text>
          <Text style={s.sub}>
            {mode === 'signin'
              ? 'Manage jobs, uploads, and payouts.'
              : 'Join Bricks GO — shoot, upload, get paid.'}
          </Text>

          <View style={s.form}>
            {!!error && (
              <View style={s.errorBox}>
                <Text style={s.errorText}>{error}</Text>
              </View>
            )}

            {mode === 'signup' && (
              <View style={s.fieldGroup}>
                <Text style={s.label}>NAME</Text>
                <TextInput
                  style={s.input}
                  value={name}
                  onChangeText={setName}
                  placeholder="Your name"
                  placeholderTextColor={TOKENS.moonFaint}
                  autoCapitalize="words"
                  autoCorrect={false}
                />
              </View>
            )}

            <View style={s.fieldGroup}>
              <Text style={s.label}>EMAIL</Text>
              <TextInput
                style={s.input}
                value={email}
                onChangeText={setEmail}
                placeholder="you@example.com"
                placeholderTextColor={TOKENS.moonFaint}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
              />
            </View>

            <View style={s.fieldGroup}>
              <Text style={s.label}>PASSWORD</Text>
              <TextInput
                style={s.input}
                value={password}
                onChangeText={setPassword}
                placeholder="Enter password"
                placeholderTextColor={TOKENS.moonFaint}
                secureTextEntry
                autoCapitalize="none"
              />
            </View>

            <TouchableOpacity
              style={[s.primaryBtn, loading && s.btnDisabled]}
              onPress={mode === 'signin' ? handleLogin : handleSignup}
              disabled={loading || googleLoading}
              activeOpacity={0.85}
            >
              {loading
                ? <ActivityIndicator color={TOKENS.night} />
                : <Text style={s.primaryBtnText}>{mode === 'signin' ? 'Sign in' : 'Create account'}</Text>
              }
            </TouchableOpacity>

            <View style={s.divider}>
              <View style={s.dividerLine} />
              <Text style={s.dividerLabel}>OR</Text>
              <View style={s.dividerLine} />
            </View>

            <TouchableOpacity
              style={[s.ghostBtn, googleLoading && s.btnDisabled]}
              onPress={handleGoogle}
              disabled={loading || googleLoading}
              activeOpacity={0.8}
            >
              {googleLoading
                ? <ActivityIndicator color={TOKENS.moon} size="small" />
                : <Text style={s.ghostBtnText}>Continue with Google</Text>
              }
            </TouchableOpacity>
          </View>

          <View style={s.footer}>
            {mode === 'signin' ? (
              <>
                <Text style={s.footerText}>New to Bricks GO?{'  '}</Text>
                <TouchableOpacity
                  onPress={() => { setMode('signup'); setError(''); }}
                  activeOpacity={0.7}
                >
                  <Text style={s.footerLink}>Create account</Text>
                </TouchableOpacity>
                <Text style={s.footerText}>{'  ·  '}</Text>
                <TouchableOpacity onPress={() => router.push('/onboarding/slides')} activeOpacity={0.7}>
                  <Text style={s.footerLink}>Apply</Text>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <Text style={s.footerText}>Already have an account?{'  '}</Text>
                <TouchableOpacity
                  onPress={() => { setMode('signin'); setError(''); }}
                  activeOpacity={0.7}
                >
                  <Text style={s.footerLink}>Sign in</Text>
                </TouchableOpacity>
              </>
            )}
          </View>

        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: TOKENS.night },
  flex: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: 24, paddingBottom: 40 },

  wordmarkWrap: { paddingTop: 28, paddingBottom: 32 },

  eyebrow: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 10,
    color: TOKENS.moonFaint,
    letterSpacing: 2,
    marginBottom: 10,
  },
  heading: {
    fontFamily: TYPOGRAPHY.heading,
    fontSize: 34,
    fontWeight: '500',
    letterSpacing: -1,
    color: TOKENS.moon,
  },
  sub: {
    fontFamily: TYPOGRAPHY.body,
    fontSize: 14,
    color: TOKENS.moonMute,
    marginTop: 6,
    marginBottom: 36,
  },

  form: { gap: 14 },

  errorBox: {
    backgroundColor: '#2A1510',
    borderRadius: TOKENS.radius,
    padding: 12,
  },
  errorText: { color: '#F4A195', fontSize: 13, fontFamily: TYPOGRAPHY.body },

  fieldGroup: { gap: 6 },
  label: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 10,
    color: TOKENS.moonMute,
    letterSpacing: 1.4,
  },
  input: {
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
    borderRadius: TOKENS.radius,
    padding: 14,
    fontSize: 15,
    color: TOKENS.moon,
    backgroundColor: TOKENS.nightSoft,
    fontFamily: TYPOGRAPHY.body,
  },

  primaryBtn: {
    marginTop: 4,
    backgroundColor: TOKENS.brick,
    borderRadius: TOKENS.radius,
    paddingVertical: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryBtnText: {
    color: TOKENS.moon,
    fontSize: 15,
    fontWeight: '500',
    fontFamily: TYPOGRAPHY.body,
  },
  btnDisabled: { opacity: 0.5 },

  divider: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginVertical: 2,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: TOKENS.nightHairS },
  dividerLabel: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 10,
    color: TOKENS.moonFaint,
    letterSpacing: 1.4,
  },

  ghostBtn: {
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
    borderRadius: TOKENS.radius,
    paddingVertical: 15,
    alignItems: 'center',
    justifyContent: 'center',
  },
  ghostBtnText: {
    color: TOKENS.moonSoft,
    fontSize: 14,
    fontFamily: TYPOGRAPHY.body,
  },

  footer: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 'auto',
    paddingTop: 40,
  },
  footerText: { fontSize: 13, color: TOKENS.moonMute, fontFamily: TYPOGRAPHY.body },
  footerLink: { fontSize: 13, color: TOKENS.brick, fontFamily: TYPOGRAPHY.body },
});
