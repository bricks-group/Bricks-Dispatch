import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { RotateCcw, Upload } from 'lucide-react-native';
import { color, type as T, space, radius, hairline } from '../../theme';
import { Screen, NavBar, Section, Group, Row, Button, Notice, StatGrid, Footer, LoadingState } from '../../components/ui';
import { ScreenshotFeed } from '../../components/capture-hud';
import { coachVerdict } from '../../lib/motion-coach';
import { getLocalRecording, removeLocalRecording, fmtClock, fmtBytes, type LocalRecording } from '../../lib/visit-capture';

function Player({ uri }: { uri: string }) {
  const player = useVideoPlayer(uri, (pl) => { pl.loop = false; pl.muted = true; });
  return <VideoView player={player} style={styles.video} contentFit="cover" nativeControls allowsFullscreen />;
}

/** Step 3: watch it back, see the coach's verdict, then keep (upload) or record again. */
export default function VisitReview() {
  const router = useRouter();
  const { jobId, localId, preview, name } = useLocalSearchParams<{ jobId: string; localId?: string; preview?: string; name?: string }>();
  const [rec, setRec] = useState<LocalRecording | null>(null);
  const [loading, setLoading] = useState(!!localId);

  useEffect(() => {
    if (!localId) return;
    getLocalRecording(localId).then((r) => { setRec(r); setLoading(false); });
  }, [localId]);

  const isPreview = preview === '1' || (!rec && Platform.OS === 'web');
  const demo: LocalRecording | null = isPreview ? {
    localId: 'preview', jobId: jobId || '', name: name || 'Main floor', scope: 'floor', floorIndex: 0, uri: '',
    sizeBytes: 1_870_000_000, durationS: 498,
    rooms: [
      { name: 'Entrance', t0: 0, t1: 58 }, { name: 'Living', t0: 58, t1: 196 }, { name: 'Kitchen', t0: 196, t1: 322 },
      { name: 'Dining', t0: 322, t1: 410 }, { name: 'Hall', t0: 410, t1: 498 },
    ],
    coach: { turn_fast_pct: 4.1, shake_pct: 2.3, tilt_pct: 6.8, still_pct: 3.0, max_turn_dps: 61 },
    createdAt: Date.now(), committed: [], state: 'recorded',
  } : null;
  const r = rec ?? demo;
  const verdict = coachVerdict(r?.coach);

  const keep = () => router.replace({ pathname: '/(capture)/visit-video', params: { jobId } } as any);
  const again = async () => {
    if (rec) await removeLocalRecording(rec.localId);
    router.replace({ pathname: '/(capture)/visit-record', params: { jobId, name: r?.name, scope: r?.scope, floor: String(r?.floorIndex ?? 0) } } as any);
  };

  const header = <NavBar title="Review recording" onBack={keep} />;
  if (loading) return <Screen header={header}><LoadingState label="Loading video" /></Screen>;
  if (!r) {
    return (
      <Screen header={header} contentStyle={styles.body}>
        <Notice tone="bad" title="Recording not found">It may have been discarded. Record it again from the Visit screen.</Notice>
      </Screen>
    );
  }

  return (
    <Screen
      header={header}
      contentStyle={styles.body}
      footer={(
        <Footer>
          <View style={styles.footRow}>
            <Button style={styles.flex} variant="secondary" icon={RotateCcw} title="Record again" onPress={again} />
            <Button style={styles.flex} icon={Upload} title={isPreview ? 'Done' : 'Keep and upload'} onPress={keep} />
          </View>
        </Footer>
      )}
    >
      {rec?.uri && Platform.OS !== 'web' ? <Player uri={rec.uri} /> : (
        <View style={[styles.video, styles.videoEmpty]}>
          <Text style={[T.dataSm, { color: color.textMuted }]}>{isPreview ? 'Web preview · no video recorded' : 'Video unavailable'}</Text>
          {isPreview ? <ScreenshotFeed /> : null}
        </View>
      )}

      <View style={styles.head}>
        <Text style={[T.title, { color: color.text }]}>{r.name}</Text>
        <Text style={[T.dataSm, { color: color.textMuted }]}>{r.scope === 'floor' ? 'Whole floor' : 'One room'} · {r.lens ? 'Ultra-wide 0.5×' : Platform.OS === 'web' ? 'Ultra-wide 0.5×' : 'Main lens 1×'}</Text>
      </View>

      <StatGrid items={[
        { label: 'Length', value: fmtClock(r.durationS) },
        { label: 'Size', value: fmtBytes(r.sizeBytes) },
        { label: 'Rooms', value: String(r.scope === 'floor' ? r.rooms.length : 1) },
      ]} />

      <Notice tone={verdict.tone} title={verdict.title}>{verdict.body}</Notice>

      {r.scope === 'floor' && r.rooms.length ? (
        <Section first label="Rooms marked">
          <Group>
            {r.rooms.map((m, i) => (
              <Row key={`${m.name}-${i}`} title={m.name} value={`${fmtClock(m.t0)}–${fmtClock(m.t1)}`} />
            ))}
          </Group>
        </Section>
      ) : null}

      {isPreview ? <Notice tone="neutral">Recording needs the Bricks app on a phone.</Notice> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingTop: space[16], gap: space[20] },
  head: { gap: space[4] },
  video: { width: '100%', aspectRatio: 4 / 3, borderRadius: radius.sm, backgroundColor: color.surfaceDeep, overflow: 'hidden' },
  videoEmpty: { alignItems: 'center', justifyContent: 'center', borderWidth: hairline, borderColor: color.hair },
  footRow: { flexDirection: 'row', gap: space[10] },
  flex: { flex: 1 },
});
