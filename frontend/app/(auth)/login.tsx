import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';
import { supabase } from '../../lib/supabase';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8002';

export default function LoginScreen() {
  const router = useRouter();
  const [step, setStep] = useState<'email' | 'otp'>('email');
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSendCode = async () => {
    const trimmed = email.trim().toLowerCase();
    if (!trimmed) {
      setError('Please enter your email address.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/vendor-os/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmed }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Request failed (${res.status})`);
      }
      setStep('otp');
    } catch (e: any) {
      setError(e.message || 'Failed to send code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerify = async () => {
    const code = otp.trim();
    if (code.length !== 6) {
      setError('Please enter the 6-digit code.');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/vendor-os/auth/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim().toLowerCase(), code }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Verification failed (${res.status})`);
      }
      const { access_token, refresh_token } = await res.json();
      const { error: sessionError } = await supabase.auth.setSession({
        access_token,
        refresh_token,
      });
      if (sessionError) throw sessionError;
      router.replace('/(tabs)');
    } catch (e: any) {
      setError(e.message || 'Invalid code. Please try again.');
    } finally {
      setLoading(false);
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
          {/* Wordmark */}
          <View style={s.wordmarkWrap}>
            <Text style={s.wordmark}>BRICKS</Text>
            <Text style={s.wordmarkSub}>DISPATCH</Text>
          </View>

          {step === 'email' ? (
            <>
              <Text style={s.eyebrow}>VENDOR LOGIN</Text>
              <Text style={s.heading}>Sign in.</Text>
              <Text style={s.sub}>We'll send a one-time code to your email.</Text>

              <View style={s.form}>
                {error ? (
                  <View style={s.errorBox}>
                    <Text style={s.errorText}>{error}</Text>
                  </View>
                ) : null}

                <View style={s.fieldGroup}>
                  <Text style={s.fieldLabel}>EMAIL ADDRESS</Text>
                  <TextInput
                    style={s.input}
                    value={email}
                    onChangeText={(t) => { setEmail(t); setError(''); }}
                    placeholder="you@example.com"
                    placeholderTextColor={TOKENS.moonFaint}
                    keyboardType="email-address"
                    autoCapitalize="none"
                    autoCorrect={false}
                    autoComplete="email"
                    editable={!loading}
                    returnKeyType="done"
                    onSubmitEditing={handleSendCode}
                  />
                </View>

                <TouchableOpacity
                  style={[s.primaryBtn, loading && s.btnDisabled]}
                  onPress={handleSendCode}
                  disabled={loading}
                  activeOpacity={0.85}
                >
                  {loading ? (
                    <ActivityIndicator color={TOKENS.moon} />
                  ) : (
                    <Text style={s.primaryBtnText}>Send Code</Text>
                  )}
                </TouchableOpacity>
              </View>
            </>
          ) : (
            <>
              <Text style={s.eyebrow}>VERIFICATION</Text>
              <Text style={s.heading}>Check your email.</Text>
              <Text style={s.sub}>
                Enter the 6-digit code sent to{' '}
                <Text style={s.emailHighlight}>{email.trim().toLowerCase()}</Text>
              </Text>

              <View style={s.form}>
                {error ? (
                  <View style={s.errorBox}>
                    <Text style={s.errorText}>{error}</Text>
                  </View>
                ) : null}

                <View style={s.fieldGroup}>
                  <Text style={s.fieldLabel}>ONE-TIME CODE</Text>
                  <TextInput
                    style={[s.input, s.otpInput]}
                    value={otp}
                    onChangeText={(t) => { setOtp(t.replace(/[^0-9]/g, '')); setError(''); }}
                    placeholder="000000"
                    placeholderTextColor={TOKENS.moonFaint}
                    keyboardType="number-pad"
                    maxLength={6}
                    editable={!loading}
                    returnKeyType="done"
                    onSubmitEditing={handleVerify}
                  />
                </View>

                <TouchableOpacity
                  style={[s.primaryBtn, (loading || otp.trim().length !== 6) && s.btnDisabled]}
                  onPress={handleVerify}
                  disabled={loading || otp.trim().length !== 6}
                  activeOpacity={0.85}
                >
                  {loading ? (
                    <ActivityIndicator color={TOKENS.moon} />
                  ) : (
                    <Text style={s.primaryBtnText}>Verify</Text>
                  )}
                </TouchableOpacity>

                <TouchableOpacity
                  style={s.backRow}
                  onPress={() => { setStep('email'); setOtp(''); setError(''); }}
                  activeOpacity={0.7}
                >
                  <Text style={s.backText}>← Use a different email</Text>
                </TouchableOpacity>
              </View>
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: TOKENS.night },
  flex: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: 24, paddingBottom: 48 },

  wordmarkWrap: { paddingTop: 32, paddingBottom: 40 },
  wordmark: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 22,
    letterSpacing: 6,
    color: TOKENS.brick,
  },
  wordmarkSub: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 11,
    letterSpacing: 4,
    color: TOKENS.moonMute,
    marginTop: 2,
  },

  eyebrow: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 9,
    color: TOKENS.moonMute,
    letterSpacing: 2,
    marginBottom: 10,
  },
  heading: {
    fontFamily: TYPOGRAPHY.heading,
    fontSize: 30,
    fontWeight: '500',
    color: TOKENS.moon,
    letterSpacing: -0.5,
  },
  sub: {
    fontSize: 14,
    color: TOKENS.moonSoft,
    marginTop: 6,
    marginBottom: 32,
    lineHeight: 20,
  },
  emailHighlight: {
    color: TOKENS.moon,
    fontFamily: TYPOGRAPHY.mono,
  },

  form: { gap: 16 },

  errorBox: {
    backgroundColor: TOKENS.badTint + '22',
    borderLeftWidth: 2,
    borderLeftColor: TOKENS.bad,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: TOKENS.radius,
  },
  errorText: {
    color: '#E57368',
    fontSize: 13,
    fontFamily: TYPOGRAPHY.mono,
    lineHeight: 18,
  },

  fieldGroup: { gap: 8 },
  fieldLabel: {
    fontFamily: TYPOGRAPHY.monoMedium,
    fontSize: 9,
    color: TOKENS.moonMute,
    letterSpacing: 1.6,
  },
  input: {
    height: 52,
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
    borderRadius: TOKENS.radius,
    paddingHorizontal: 14,
    fontSize: 15,
    color: TOKENS.moon,
    backgroundColor: TOKENS.nightSoft,
    fontFamily: TYPOGRAPHY.mono,
  },
  otpInput: {
    fontSize: 24,
    letterSpacing: 8,
    textAlign: 'center',
  },

  primaryBtn: {
    backgroundColor: TOKENS.brick,
    height: 52,
    borderRadius: TOKENS.radius,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 4,
  },
  btnDisabled: { opacity: 0.4 },
  primaryBtnText: {
    color: TOKENS.moon,
    fontSize: 15,
    fontFamily: TYPOGRAPHY.monoMedium,
    letterSpacing: 1,
  },

  backRow: { alignItems: 'center', paddingVertical: 6 },
  backText: {
    fontFamily: TYPOGRAPHY.mono,
    fontSize: 12,
    color: TOKENS.moonMute,
    letterSpacing: 0.5,
  },
});
