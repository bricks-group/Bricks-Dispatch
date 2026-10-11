import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { color, type as T, space } from '../../theme';
import { Box } from 'lucide-react-native';
import { Screen, NavBar, Section, Group, Row, Button, TextLink, Notice, Pill, StatGrid, Footer, EmptyState, LoadingState } from '../../components/ui';
import { PipelineSteps } from '../../components/capture-hud';
import { getCaptureSession, rebuildFloorPlan, rebuildTourPlayer, submitCaptureSession } from '../../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../../lib/capture-api';
import { captureStatusCopy } from '../../lib/capture-modes';
import { TourTagEditor } from '../../components/TourTagEditor';


export default function ReviewScreen() {
  const router = useRouter();
  const { jobId, kind: kindParam } = useLocalSearchParams<{ jobId: string; kind?: string }>();
  const kind: CaptureKind = kindParam === 'floorplan' ? 'floorplan' : 'splat';

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildMsg, setRebuildMsg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) { setLoading(false); return; }
      const s = await getCaptureSession(jobId, kind);
      if (!cancelled) {
        setSession(s);
        const st = (s.status || '').toLowerCase();
        if (['submitted', 'splatting', 'tracing', 'delivered', 'failed'].includes(st)) {
          setSubmitted(true);
        }
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId, kind]);

  const handleSubmit = async () => {
    if (!jobId) return;
    setSubmitting(true);
    setSubmitError(null);
    const result = await submitCaptureSession(jobId, kind);
    setSubmitting(false);
    if (result.session) setSession(result.session);
    if (!result.ok) {
      setSubmitError(result.error ?? 'Submit failed');
      setSubmitted(true);
      return;
    }
    setSubmitted(true);
  };

  const handleRebuild = async () => {
    if (!jobId || (kind !== 'splat' && kind !== 'floorplan')) return;
    setRebuilding(true);
    setRebuildMsg(null);
    setSubmitError(null);
    const result = kind === 'floorplan'
      ? await rebuildFloorPlan(jobId, session?.sessionId)
      : await rebuildTourPlayer(jobId, kind, session?.sessionId);
    setRebuilding(false);
    if (result.session) setSession(result.session);
    if (!result.ok) {
      setRebuildMsg(result.error ?? 'Rebuild failed');
      return;
    }
    setRebuildMsg(
      kind === 'floorplan'
        ? 'Floor plan PNG rebuilt from stored stations. Refresh the listing to see north arrow, scale, and labels.'
        : 'Tour player rebuilt from existing 360 stations. Listing embed URL is unchanged — hard-refresh the listing iframe to see tags.',
    );
  };

  const goBackToJob = () => {
    if (jobId) {
      router.replace(`/job/${jobId}` as any);
      return;
    }
    router.back();
  };

  const goRecapture = () => {
    const pathname = kind === 'floorplan' ? '/(capture)/floorplan' : '/(capture)/capture';
    router.replace({ pathname, params: { jobId, kind } } as any);
  };

  const rooms = session?.rooms ?? [];
  const pipeline = session?.pipeline ?? [];
  const stations = session?.stations ?? [];
  const stationCount = session?.stationCount ?? stations.length;
  const totalFrames = session?.frameCount ?? 0;
  const address = session?.address || 'Property';
  const meta = session?.meta || '';
  const coveredCount = rooms.filter(r => r.n > 0).length;
  const totalCount = rooms.length;
  const minPans = kind === 'floorplan' ? 4 : 8;
  const thinStations = stations.filter(s => !s.hasEquirect && (s.panCount ?? 0) < minPans);
  const canSubmit = pipeline.length > 0 && stationCount > 0 && thinStations.length === 0;
  const status = (session?.status || '').toLowerCase();
  const statusLine = captureStatusCopy(status, kind);

  if (!jobId) {
    return (
      <Screen header={<NavBar title="Review" onBack={() => router.back()} />}>
        <EmptyState icon={Box} title="No job selected" body="Open capture from a job." />
      </Screen>
    );
  }

  const kindName = kind === 'floorplan' ? 'Floor plan' : '360° tour';

  if (submitted) {
    const failed = status === 'failed' || !!submitError;
    const ready = status === 'delivered';
    const tone = failed ? 'bad' : ready ? 'ok' : 'accent';
    const title = failed ? 'Compile failed' : ready ? `${kindName} is ready` : `${kindName} is compiling`;
    const body = failed
      ? (submitError || statusLine || 'Try again, or recapture the thin stations.')
      : (statusLine || (ready ? `The ${kindName.toLowerCase()} is on the job.` : 'This usually takes a few minutes. You can leave this screen.'));
    const rebuildOk = !!rebuildMsg && (rebuildMsg.startsWith('Tour player rebuilt') || rebuildMsg.startsWith('Floor plan PNG rebuilt'));

    return (
      <Screen header={<NavBar title={kindName} onBack={goBackToJob} />} contentStyle={styles.body}>
        <>
          <View style={styles.head}>
            <Text style={[T.title, { color: color.text }]}>{address}</Text>
            {meta ? <Text style={[T.dataSm, { color: color.textMuted }]}>{meta}</Text> : null}
          </View>
          <Notice tone={tone} title={title}>{body}</Notice>
          {rebuildMsg ? <Notice tone={rebuildOk ? 'ok' : 'bad'}>{rebuildMsg}</Notice> : null}
          <View style={styles.actions}>
            {failed ? (
              <Button title={submitting ? 'Retrying…' : 'Retry compile'} onPress={handleSubmit} loading={submitting} disabled={!canSubmit} />
            ) : null}
            {ready && (kind === 'splat' || kind === 'floorplan') ? (
              <Button
                variant="secondary"
                title={kind === 'floorplan' ? 'Rebuild floor plan' : 'Rebuild tour player'}
                onPress={handleRebuild}
                loading={rebuilding}
              />
            ) : null}
            <Button variant="secondary" title="Back to job" onPress={goBackToJob} />
            <TextLink title={failed ? 'Recapture' : 'Continue capture'} tone="accent" onPress={goRecapture} />
          </View>
          {kind === 'splat' && session ? (
            <TourTagEditor jobId={jobId} session={session} onSession={setSession} />
          ) : null}
        </>
      </Screen>
    );
  }

  const blocker = !loading && (stationCount === 0 || thinStations.length > 0)
    ? (stationCount === 0
      ? (kind === 'floorplan'
        ? 'Shoot at least one room’s four corners before sending.'
        : 'Shoot at least one full 360 station before sending.')
      : (kind === 'floorplan'
        ? `Station ${thinStations[0].station_index} needs all four corners.`
        : `Station ${thinStations[0].station_index} is thin. Fill the ring (8+ overlapping frames).`))
    : null;

  return (
    <Screen
      scroll={!loading}
      contentStyle={styles.body}
      header={<NavBar title={`Review ${kindName.toLowerCase()}`} onBack={() => router.back()} />}
      footer={(
        <Footer>
          <View style={styles.footerRow}>
            <View>
              <Text style={[T.overline, { color: color.textMuted }]}>Est. delivery</Text>
              <Text style={[T.dataLg, { color: color.text }]}>
                {session?.estimatedDeliveryHours != null ? `~${session.estimatedDeliveryHours} h` : '—'}
              </Text>
            </View>
            <Button style={styles.flex} title="Send to pipeline" onPress={handleSubmit} loading={submitting} disabled={loading || !canSubmit} />
          </View>
        </Footer>
      )}
    >
      {loading ? <LoadingState label="Loading capture" /> : (
        <>
          <View style={styles.head}>
            <Text style={[T.title, { color: color.text }]}>{address}</Text>
            {meta ? <Text style={[T.dataSm, { color: color.textMuted }]}>{meta}</Text> : null}
          </View>
          <StatGrid
            large
            items={[
              { label: 'Stations', value: String(stationCount) },
              { label: kind === 'floorplan' ? 'Corners' : 'Frames', value: totalFrames.toLocaleString() },
              { label: 'Rooms', value: `${coveredCount}/${totalCount}` },
            ]}
          />
          {blocker ? <Notice tone="warn">{blocker}</Notice> : null}

          <Section first label="Rooms">
            {rooms.length === 0 ? (
              <Text style={[T.bodySm, { color: color.textMuted }]}>No rooms yet.</Text>
            ) : (
              <Group>
                {rooms.map(r => (
                  <Row
                    key={r.id ?? r.name}
                    title={r.name}
                    right={<Pill label={`${r.n} ${kind === 'floorplan' ? 'corners' : 'frames'}`} tone={(r.tone as any) === 'ok' ? 'ok' : r.tone === 'warn' ? 'warn' : 'bad'} />}
                  />
                ))}
              </Group>
            )}
          </Section>

          <Section first label="Pipeline">
            {pipeline.length === 0 ? (
              <Text style={[T.bodySm, { color: color.textMuted }]}>Starts when the session does.</Text>
            ) : (
              <PipelineSteps steps={pipeline} />
            )}
          </Section>
          {submitError ? <Notice tone="bad">{submitError}</Notice> : null}
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  body: { paddingTop: space[12], gap: space[20] },
  head: { gap: space[4] },
  actions: { gap: space[10] },
  footerRow: { flexDirection: 'row', alignItems: 'center', gap: space[16] },
  flex: { flex: 1 },
});
