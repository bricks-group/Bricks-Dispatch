import { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { actorHeaders } from '../../lib/api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function VisitDeliverScreen() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const deliver = async () => {
    if (!jobId) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const headers = await actorHeaders();
      headers['Content-Type'] = 'application/json';
      const delRes = await fetch(`${API}/api/capture/visit/deliver`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ job_id: jobId, polycam_url: url.trim(), title: 'Virtual Visit' }),
      });
      const del = await delRes.json().catch(() => ({}));
      if (!delRes.ok) throw new Error(del?.detail || del?.message || 'Could not deliver visit');
      setDone('Virtual Visit link is on the listing.');
      setUrl('');
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = !!jobId && !busy && url.trim().length > 8;

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.top}>
        <TouchableOpacity style={styles.chip} onPress={() => router.back()}>
          <Text style={styles.chipText}>✕</Text>
        </TouchableOpacity>
        <Text style={styles.kicker}>VIRTUAL VISIT</Text>
        <View style={styles.chip} />
      </View>
      <View style={styles.body}>
        <Text style={styles.title}>Deliver Virtual Visit</Text>
        <Text style={styles.sub}>
          Free-roam splat — not the path-confined 3D tour. Paste a Polycam share URL. File upload (.sog / .spz / .ply / .splat) is on GO web for now; owned phone-video Visit capture is next.
        </Text>

        <Text style={styles.label}>Polycam / share URL</Text>
        <TextInput
          style={styles.input}
          value={url}
          onChangeText={setUrl}
          placeholder="https://poly.cam/…"
          placeholderTextColor="#F4F1EB55"
          autoCapitalize="none"
          editable={!busy}
        />

        {error ? <Text style={styles.error}>{error}</Text> : null}
        {done ? <Text style={styles.done}>{done}</Text> : null}

        <TouchableOpacity
          style={[styles.submit, !canSubmit && { opacity: 0.45 }]}
          onPress={deliver}
          disabled={!canSubmit}
          activeOpacity={0.85}
        >
          {busy ? <ActivityIndicator color="#F4F1EB" /> : <Text style={styles.submitText}>Deliver to listing</Text>}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0C0B09' },
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 8 },
  chip: { width: 34, height: 34, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
  chipText: { color: '#F4F1EB', fontSize: 16 },
  kicker: { fontFamily: FONTS.mono, fontSize: 11, letterSpacing: 2, color: COLORS.brick },
  body: { padding: 22, paddingTop: 28, gap: 12 },
  title: { fontFamily: FONTS.bodySemibold, fontSize: 24, color: '#F4F1EB' },
  sub: { fontFamily: FONTS.body, fontSize: 13, color: '#F4F1EBAA', lineHeight: 18 },
  label: { fontFamily: FONTS.mono, fontSize: 10, letterSpacing: 1.5, color: '#F4F1EB88', marginTop: 8 },
  input: { borderWidth: 1, borderColor: '#F4F1EB22', borderRadius: 2, padding: 12, color: '#F4F1EB', backgroundColor: '#141110', fontFamily: FONTS.body },
  error: { color: '#E57373', fontFamily: FONTS.body, fontSize: 13 },
  done: { color: '#7dba7d', fontFamily: FONTS.body, fontSize: 13 },
  submit: { marginTop: 8, backgroundColor: COLORS.brick, borderRadius: 2, paddingVertical: 14, alignItems: 'center' },
  submitText: { color: '#F4F1EB', fontFamily: FONTS.bodySemibold, fontSize: 15 },
});
