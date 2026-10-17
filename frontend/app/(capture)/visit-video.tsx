import { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Platform } from 'react-native';
import { useRouter, useLocalSearchParams, useFocusEffect } from 'expo-router';
import NetInfo from '@react-native-community/netinfo';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { Video, Layers, RotateCcw, Footprints } from 'lucide-react-native';
import { color, type as T, space, type Tone } from '../../theme';
import { Screen, NavBar, Section, Group, Row, Button, Notice, Pill, Footer, LoadingState } from '../../components/ui';
import { PipelineSteps } from '../../components/capture-hud';
import {
  visitApi, listLocalRecordings, uploadRecording, removeLocalRecording, fmtClock, fmtBytes,
  type LocalRecording, type VisitBundle,
} from '../../lib/visit-capture';

const KEEP_AWAKE_TAG = 'visit-upload';

type Item = {
  key: string; name: string; meta: string; status: { label: string; tone: Tone }; local?: LocalRecording;
};

function pct(r: LocalRecording) {
  if (!r.partsTotal || !r.partSize) return 0;
  return Math.min(100, Math.round(((r.committed.length * r.partSize) / Math.max(1, r.sizeBytes)) * 100));
}

export default function VisitVideoHub() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [bundle, setBundle] = useState<VisitBundle | null>(null);
  const [locals, setLocals] = useState<LocalRecording[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const stopRef = useRef(false);
  const runningRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!jobId) return;
    const l = await listLocalRecordings(jobId);
    setLocals(l);
    try {
      setBundle(await visitApi.get(jobId));
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Could not load this Visit');
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useFocusEffect(useCallback(() => { refresh(); }, [refresh]));

  useEffect(() => NetInfo.addEventListener((s) => setOnline(s.isConnected !== false && s.isInternetReachable !== false)), []);

  const runUploads = useCallback(async () => {
    if (!jobId || runningRef.current || Platform.OS === 'web') return; // web can't read phone files
    runningRef.current = true;
    stopRef.current = false;
    setUploading(true);
    activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {});
    try {
      const pending = (await listLocalRecordings(jobId)).filter((r) => r.state !== 'uploaded');
      for (const rec of pending) {
        if (stopRef.current) break;
        try {
          await uploadRecording(rec, (_p, r) => setLocals((cur) => cur.map((x) => (x.localId === r.localId ? r : x))), () => stopRef.current);
        } catch (e: any) {
          const failed: LocalRecording = { ...rec, state: 'failed', error: e?.message || 'Upload failed' };
          setLocals((cur) => cur.map((x) => (x.localId === rec.localId ? { ...x, state: 'failed', error: failed.error } : x)));
          setError(failed.error ?? null);
          break;
        }
      }
    } finally {
      deactivateKeepAwake(KEEP_AWAKE_TAG);
      runningRef.current = false;
      setUploading(false);
      refresh();
    }
  }, [jobId, refresh]);

  // Start (or resume) uploads whenever there is something to send and a connection.
  const pendingCount = locals.filter((r) => r.state !== 'uploaded').length;
  useEffect(() => {
    if (!online) { stopRef.current = true; return; }
    if (pendingCount > 0 && !runningRef.current && !loading) runUploads();
  }, [online, pendingCount, loading, runUploads]);

  useEffect(() => () => { stopRef.current = true; }, []);

  const submitted = !!bundle?.status && bundle.status !== 'in_progress';
  const serverRecs = bundle?.recordings ?? [];

  const items: Item[] = locals.map((r) => {
    const status: Item['status'] =
      r.state === 'uploaded' ? { label: 'Uploaded', tone: 'ok' }
        : r.state === 'failed' ? { label: 'Upload failed', tone: 'bad' }
          : !online ? { label: `Waiting · ${pct(r)}%`, tone: 'warn' }
            : r.state === 'uploading' ? { label: `Uploading · ${pct(r)}%`, tone: 'accent' }
              : { label: `Queued · ${pct(r)}%`, tone: 'neutral' };
    const rooms = r.scope === 'floor' && r.rooms.length ? ` · ${r.rooms.length} room${r.rooms.length === 1 ? '' : 's'}` : '';
    return { key: r.localId, name: r.name, meta: `${fmtClock(r.durationS)} · ${fmtBytes(r.sizeBytes)}${rooms}`, status, local: r };
  });
  for (const s of serverRecs) {
    if (locals.some((l) => l.serverId === s.id)) continue;
    items.push({
      key: s.id, name: s.name, meta: `${fmtClock(s.duration_s)} · ${fmtBytes(s.size_bytes)}`,
      status: s.complete ? { label: 'Uploaded', tone: 'ok' } : { label: 'Incomplete', tone: 'bad' },
    });
  }

  const allUploaded = items.length > 0 && items.every((i) => i.status.label === 'Uploaded');
  const nextFloor = items.filter((i) => (i.local?.scope ?? 'floor') === 'floor').length;

  const record = () => router.push({ pathname: '/(capture)/visit-prep', params: { jobId, floor: String(nextFloor) } } as any);

  const submit = async () => {
    if (!jobId) return;
    setSubmitting(true);
    setError(null);
    try {
      setBundle(await visitApi.submit(jobId));
    } catch (e: any) {
      setError(e?.message || 'Could not send the Visit');
    } finally {
      setSubmitting(false);
    }
  };

  const retake = async (it: Item) => {
    if (!jobId || !it.local) return;
    stopRef.current = true;
    if (it.local.serverId) await visitApi.discard(jobId, it.local.serverId).catch(() => {});
    await removeLocalRecording(it.local.localId);
    refresh();
  };

  const header = <NavBar title="Virtual visit" onBack={() => router.back()} />;

  if (loading) return <Screen header={header}><LoadingState label="Loading" /></Screen>;

  if (submitted) {
    return (
      <Screen header={header} contentStyle={styles.body}>
        <View style={styles.head}>
          <Text style={[T.title, { color: color.text }]}>{bundle?.address || 'Virtual visit'}</Text>
          <Text style={[T.dataSm, { color: color.textMuted }]}>{serverRecs.length} recording{serverRecs.length === 1 ? '' : 's'} sent</Text>
        </View>
        <Notice tone="accent" title="Sent for processing">The 3D build runs on our GPU pipeline. You can leave this screen; nothing else is needed from you.</Notice>
        <PipelineSteps steps={bundle?.pipeline ?? []} />
      </Screen>
    );
  }

  return (
    <Screen
      header={header}
      contentStyle={styles.body}
      footer={items.length ? (
        <Footer>
          {!allUploaded ? (
            <Text style={[T.caption, styles.footNote]}>
              {online ? 'Keep Bricks open while it uploads. It resumes if the connection drops.' : 'Offline. Upload resumes when you’re back online.'}
            </Text>
          ) : null}
          <Button title="Send for processing" onPress={submit} loading={submitting} disabled={!allUploaded || uploading} />
        </Footer>
      ) : undefined}
    >
      <View style={styles.head}>
        <Text style={[T.display, { color: color.text }]} accessibilityRole="header">
          {items.length ? (bundle?.address || 'Recordings') : 'One video per floor'}
        </Text>
        {!items.length ? (
          <Text style={[T.body, { color: color.textMuted }]}>Walk every room in one take. We build the 3D visit.</Text>
        ) : null}
      </View>

      {!items.length ? (
        <>
          <Group>
            <Row icon={Layers} title="One take per floor" subtitle="Start at the entrance. Don’t stop between rooms." />
            <Row icon={Footprints} title="Two laps, then corner to corner" subtitle="Along the walls, back the other way, then both diagonals." />
            <Row icon={RotateCcw} title="Slow, upright, no zoom" subtitle="We tell you when to slow down." />
          </Group>
          <Button icon={Video} title="Get ready to record" onPress={record} />
        </>
      ) : (
        <>
          <Section first label="Recordings">
            <Group>
              {items.map((it) => (
                <Row
                  key={it.key}
                  title={it.name}
                  subtitle={it.local?.error && it.status.tone === 'bad' ? it.local.error : it.meta}
                  right={<Pill label={it.status.label} tone={it.status.tone} />}
                  onPress={it.local && it.local.state !== 'uploaded' ? () => (it.status.tone === 'bad' ? runUploads() : undefined) : undefined}
                />
              ))}
            </Group>
          </Section>
          {error ? <Notice tone="bad">{error}</Notice> : null}
          <View style={styles.row}>
            <Button style={styles.flex} variant="secondary" icon={Video} title="Record another floor" onPress={record} />
          </View>
          {items.some((i) => i.status.tone === 'bad' && i.local) ? (
            <Button variant="ghost" title="Discard failed recording" onPress={() => retake(items.find((i) => i.status.tone === 'bad' && i.local)!)} />
          ) : null}
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingTop: space[16], gap: space[24] },
  head: { gap: space[8] },
  row: { flexDirection: 'row', gap: space[10] },
  flex: { flex: 1 },
  footNote: { color: color.textMuted },
});
