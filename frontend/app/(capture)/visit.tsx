import { useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Video } from 'lucide-react-native';
import { color, type as T, space } from '../../theme';
import { Screen, NavBar, Section, Group, Row, Field, Button, Notice } from '../../components/ui';
import { actorHeaders } from '../../lib/api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

/**
 * Virtual Visit entry. Two ways in:
 *  1. Record the walk-through in GO (guided video → owned splat pipeline).
 *  2. Deliver an existing scan: Polycam share link (unchanged deliver path).
 * Not the 360° tour (kind=splat, Capture → 360° tour).
 */
export default function VisitScreen() {
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
    <Screen header={<NavBar title="Virtual visit" close onBack={() => router.back()} border={false} />} contentStyle={styles.body}>
      <View style={styles.head}>
        <Text style={[T.display, { color: color.text }]} accessibilityRole="header">Virtual visit</Text>
        <Text style={[T.body, { color: color.textMuted }]}>Free-roam 3D of the home, built from one walk-through video per floor.</Text>
      </View>

      <Group>
        <Row
          icon={Video}
          title="Record the walk-through"
          subtitle="GO sets up the camera and coaches you while you walk."
          chevron
          onPress={() => router.push({ pathname: '/(capture)/visit-video', params: { jobId } } as any)}
        />
      </Group>

      <Section first label="Already scanned elsewhere">
        <View style={styles.deliver}>
          <Field
            label="Polycam share link"
            value={url}
            onChangeText={setUrl}
            placeholder="https://poly.cam/…"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            editable={!busy}
            hint="Files (.sog, .spz, .ply, .splat) upload on GO web."
          />
          {error ? <Notice tone="bad">{error}</Notice> : null}
          {done ? <Notice tone="ok">{done}</Notice> : null}
          <Button variant="secondary" title="Deliver link to listing" onPress={deliver} loading={busy} disabled={!canSubmit} />
        </View>
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingTop: space[16], gap: space[24] },
  head: { gap: space[8] },
  deliver: { gap: space[16] },
});
