import { useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { color, type as T, space, layout } from '../../theme';
import { NavBar, Field, Button, Notice } from '../../components/ui';
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
      setDone('The virtual visit is on the listing.');
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
      <NavBar title="Virtual visit" close onBack={() => router.back()} border={false} />
      <View style={styles.body}>
        <Text style={[T.display, { color: color.text }]} accessibilityRole="header">Deliver the visit</Text>
        <Text style={[T.body, styles.sub]}>
          Paste the Polycam share link for the free-roam scan. It goes straight onto the listing.
        </Text>

        <Field
          label="Share link"
          value={url}
          onChangeText={setUrl}
          placeholder="https://poly.cam/…"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          editable={!busy}
          hint="File uploads (.sog, .spz, .ply, .splat) are on GO web for now."
        />

        {error ? <Notice tone="bad">{error}</Notice> : null}
        {done ? <Notice tone="ok">{done}</Notice> : null}

        <Button title="Deliver to listing" onPress={deliver} loading={busy} disabled={!canSubmit} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: color.bg },
  body: { paddingHorizontal: layout.gutter, paddingTop: space[16], gap: space[20] },
  sub: { color: color.textMuted, marginTop: -space[12] },
});
