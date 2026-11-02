import { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Linking, ActivityIndicator } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { actorHeaders } from '../lib/api';
import { supabase } from '../lib/supabase';
import { markTermsCleared } from '../lib/terms-gate';
import { LegalDocSheet } from '../components/LegalDocSheet';
import { PressScale } from '../components/motion';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';
import {
  A1_GUIDE_LINE, LEGAL_DOCS, acceptTermsBody, allRead, lockedReason, requiredDocs,
  type LegalKey, type LoadedDoc, type TermsMember,
} from '../lib/legal-terms';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

type Me = NonNullable<TermsMember> & { name?: string | null; needs_terms?: boolean | null };

/**
 * Contractor agreement step, same as Dispatch web's AgreeTermsPage. Vendors who
 * already accepted the agreement read amendment A1 only; everyone else reads the
 * agreement, privacy policy and A1. "I agree" stays locked until each is scrolled
 * to the end, then records the versions through /profile/accept-terms.
 */
export default function AgreeTermsScreen() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [read, setRead] = useState<Partial<Record<LegalKey, boolean>>>({});
  const [open, setOpen] = useState<LegalKey | null>(null);
  const [icVersion, setIcVersion] = useState('');
  const [a1Version, setA1Version] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const loadMe = useCallback(async () => {
    setLoadErr(false);
    try {
      const res = await fetch(`${API}/api/vendor-os/auth/me`, { headers: await actorHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const m = (await res.json()) as Me;
      if (m.needs_terms !== true) {
        const { data } = await supabase.auth.getSession();
        markTermsCleared(data.session?.user?.id);
        router.replace('/(tabs)');
        return;
      }
      setMe(m);
    } catch {
      setLoadErr(true);
    }
  }, [router]);

  useEffect(() => { loadMe(); }, [loadMe]);

  const REQUIRED_DOCS = requiredDocs(me);
  const amendmentOnly = !REQUIRED_DOCS.includes('ic');
  const done = !!me && allRead(REQUIRED_DOCS, read) && (amendmentOnly || !!icVersion)
    && (!REQUIRED_DOCS.includes('ic_a1') || !!a1Version);
  const markRead = useCallback((k: LegalKey) => setRead((r) => (r[k] ? r : { ...r, [k]: true })), []);
  const onLoaded = useCallback((k: LegalKey, d: LoadedDoc) => {
    if (k === 'ic' && d.version) setIcVersion(d.version);
    if (k === 'ic_a1' && d.version) setA1Version(d.version);
  }, []);

  const agree = async () => {
    if (!done || busy) return;
    setBusy(true); setError('');
    try {
      const res = await fetch(`${API}/api/vendor-os/profile/accept-terms`, {
        method: 'POST',
        headers: await actorHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(acceptTermsBody(REQUIRED_DOCS, read, icVersion, a1Version)),
      });
      if (!res.ok) {
        setError(res.status === 409
          ? 'The agreement was just updated. Close and reopen the app to read the current version.'
          : "Couldn't save your agreement. Try again.");
        setBusy(false);
        return;
      }
      const { data } = await supabase.auth.getSession();
      markTermsCleared(data.session?.user?.id);
      router.replace('/(tabs)');
    } catch {
      setError("Couldn't save your agreement. Try again.");
      setBusy(false);
    }
  };

  const first = (me?.name || '').trim().split(/\s+/)[0];

  if (!me) {
    return (
      <SafeAreaView style={s.page} edges={['top', 'bottom']}>
        <View style={s.center}>
          {loadErr ? (
            <>
              <Text style={s.lede}>Couldn't load your account.</Text>
              <TouchableOpacity onPress={loadMe} accessibilityRole="button"><Text style={s.out}>Try again</Text></TouchableOpacity>
            </>
          ) : <ActivityIndicator color={TOKENS.moonMute} />}
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.page} edges={['top', 'bottom']} testID="agree-terms-page">
      <ScrollView contentContainerStyle={s.card}>
        {amendmentOnly ? (
          <>
            <Text style={s.h1} accessibilityRole="header">Your contractor agreement has changed</Text>
            <Text style={s.lede}>
              Amendment A1 adds rules for jobs you've accepted. {A1_GUIDE_LINE} Check in within 100 m of the property.
            </Text>
            <Text style={s.lede}>
              It binds you from 16 November 2026, or from when you accept it here. Questions? Email{' '}
              <Text style={s.link} onPress={() => Linking.openURL('mailto:help@brxs.ca')}>help@brxs.ca</Text>.
            </Text>
          </>
        ) : (
          <>
            <Text style={s.h1} accessibilityRole="header">{first ? `Welcome, ${first}` : 'Before you start'}</Text>
            <Text style={s.lede}>Read the contractor agreement, privacy policy and amendment A1 once before your first job.</Text>
            {REQUIRED_DOCS.includes('ic_a1') ? <Text style={s.lede}>{A1_GUIDE_LINE}</Text> : null}
          </>
        )}
        <View style={s.rows}>
          {REQUIRED_DOCS.map((k, i) => (
            <TouchableOpacity
              key={k}
              style={[s.row, i > 0 && s.rowSep]}
              onPress={() => setOpen(k)}
              accessibilityRole="button"
              testID={`legal-open-${k}`}
            >
              <Text style={[s.rowTitle, !read[k] && s.rowTitleOpen]}>{LEGAL_DOCS[k].title}</Text>
              <Text style={[s.rowState, read[k] && s.rowStateRead]}>{read[k] ? 'Read' : 'Open'}</Text>
            </TouchableOpacity>
          ))}
        </View>
        <PressScale
          style={[s.btn, (!done || busy) && s.disabled]}
          disabled={!done || busy}
          onPress={agree}
          accessibilityRole="button"
          accessibilityState={{ disabled: !done || busy }}
          testID="agree-terms-submit"
        >
          <Text style={s.btnLabel}>{busy ? 'Saving…' : 'I agree'}</Text>
        </PressScale>
        <Text style={[s.note, !!error && s.noteErr]} accessibilityRole={error ? 'alert' : undefined}>
          {error || (done
            ? (REQUIRED_DOCS.length === 1 ? 'You agree to the amendment as written.' : 'You agree to these documents as written.')
            : lockedReason(REQUIRED_DOCS, read) || 'Loading the agreement…')}
        </Text>
        <TouchableOpacity onPress={() => supabase.auth.signOut()} accessibilityRole="button">
          <Text style={s.out}>Log out</Text>
        </TouchableOpacity>
      </ScrollView>
      <LegalDocSheet doc={open} read={open ? !!read[open] : false} onRead={markRead} onLoaded={onLoaded} onClose={() => setOpen(null)} />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: TOKENS.night },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, gap: 12 },
  card: { flexGrow: 1, justifyContent: 'center', paddingHorizontal: 20, paddingVertical: 32, maxWidth: 460, width: '100%', alignSelf: 'center' },
  h1: { fontFamily: TYPOGRAPHY.heading, fontSize: 28, letterSpacing: -0.8, color: TOKENS.moon, marginBottom: 10 },
  lede: { fontFamily: TYPOGRAPHY.body, fontSize: 14, lineHeight: 22, color: TOKENS.moonSoft, marginBottom: 16 },
  link: { color: TOKENS.moon, textDecorationLine: 'underline' },
  rows: { borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius, backgroundColor: TOKENS.nightSoft, marginTop: 8, marginBottom: 20 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, minHeight: 52, paddingHorizontal: 16, paddingVertical: 12 },
  rowSep: { borderTopWidth: 1, borderTopColor: TOKENS.nightHair },
  rowTitle: { flex: 1, fontFamily: TYPOGRAPHY.body, fontSize: 14, color: TOKENS.moon },
  rowTitleOpen: { textDecorationLine: 'underline' },
  rowState: { fontFamily: TYPOGRAPHY.body, fontSize: 13, color: TOKENS.moonMute },
  rowStateRead: { color: TOKENS.moon },
  btn: { minHeight: 48, borderRadius: TOKENS.radius, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center' },
  btnLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 15, color: '#FFFFFF' },
  disabled: { opacity: 0.32 },
  note: { fontFamily: TYPOGRAPHY.body, fontSize: 13, lineHeight: 20, color: TOKENS.moonMute, marginTop: 12, marginBottom: 28 },
  noteErr: { color: TOKENS.brick },
  out: { fontFamily: TYPOGRAPHY.body, fontSize: 13, color: TOKENS.moonMute, textDecorationLine: 'underline' },
});
