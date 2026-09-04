import { useState, useEffect } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { getCaptureSession, submitCaptureSession } from '../../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../../lib/capture-api';
import { captureStatusCopy } from '../../lib/capture-modes';

const TONE_COLOR: Record<string, string> = { ok: COLORS.ok, warn: COLORS.warn, bad: COLORS.bad };

export default function ReviewScreen() {
  const router = useRouter();
  const { jobId, kind: kindParam } = useLocalSearchParams<{ jobId: string; kind?: string }>();
  const kind: CaptureKind = kindParam === 'floorplan' ? 'floorplan' : 'splat';

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

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
  const thinStations = stations.filter(s => !s.hasEquirect && (s.panCount ?? 0) < 8);
  const canSubmit = pipeline.length > 0 && stationCount > 0 && thinStations.length === 0;
  const status = (session?.status || '').toLowerCase();
  const statusLine = captureStatusCopy(status, kind);
  const kindLabel = kind === 'floorplan' ? 'FLOOR PLAN' : '3D TOUR';

  if (!jobId) {
    return (
      <SafeAreaView style={styles.safe} edges={['top']}>
        <View style={styles.body}>
          <Text style={styles.emptyText}>Missing jobId</Text>
          <TouchableOpacity style={styles.resultBtn} onPress={() => router.back()}>
            <Text style={styles.resultBtnText}>Back</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  if (submitted) {
    const failed = status === 'failed' || !!submitError;
    const compiling = ['submitted', 'splatting', 'tracing'].includes(status);
    const ready = status === 'delivered';
    const resultTone = failed ? styles.resultFailed : ready ? styles.resultReady : styles.resultCompiling;
    const title = failed ? 'Compile failed' : ready ? 'Submitted · ready' : 'Submitted · compiling';
    const body = failed
      ? (submitError || statusLine || 'Compile failed — try again from review or recapture stations.')
      : (statusLine || (ready
        ? `${kind === 'floorplan' ? 'Floor plan' : '3D tour'} is ready on the job.`
        : `${kind === 'floorplan' ? 'Floor plan' : '3D tour'} is compiling. This usually takes a few minutes.`));

    return (
      <SafeAreaView style={styles.safe} edges={['top']}>
        <ScrollView>
          <View style={styles.hero}>
            <TouchableOpacity style={styles.backChip} onPress={goBackToJob}>
              <Text style={styles.backChipText}>‹</Text>
            </TouchableOpacity>
            <Text style={styles.heroAddress}>{address}</Text>
            <Text style={styles.heroMeta}>
              {[meta, kindLabel].filter(Boolean).join(' · ')}
            </Text>
          </View>

          <View style={styles.body}>
            <View style={[styles.resultCard, resultTone]}>
              <Text style={styles.resultTitle}>{title}</Text>
              <Text style={styles.resultBody}>{body}</Text>
              <View style={styles.resultActions}>
                <TouchableOpacity style={styles.resultBtn} onPress={goBackToJob}>
                  <Text style={styles.resultBtnText}>Back to job</Text>
                </TouchableOpacity>
                {failed ? (
                  <TouchableOpacity
                    style={[styles.resultBtnPrimary, (submitting || !canSubmit) && { opacity: 0.5 }]}
                    onPress={handleSubmit}
                    disabled={submitting || !canSubmit}
                  >
                    <Text style={styles.resultBtnPrimaryText}>
                      {submitting ? 'Retrying…' : 'Retry compile'}
                    </Text>
                  </TouchableOpacity>
                ) : null}
                <TouchableOpacity style={styles.resultLink} onPress={goRecapture}>
                  <Text style={styles.resultLinkText}>
                    {failed ? 'Recapture' : 'Continue capture'}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color={COLORS.brick} />
          <Text style={styles.loadingText}>Loading capture…</Text>
        </View>
      )}

      <ScrollView>
        <View style={styles.hero}>
          <TouchableOpacity style={styles.backChip} onPress={() => router.back()}>
            <Text style={styles.backChipText}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.heroAddress}>{address}</Text>
          <Text style={styles.heroMeta}>
            {[meta, kindLabel, `${stationCount} STATION${stationCount === 1 ? '' : 'S'}`, `${totalFrames.toLocaleString()} PANS`].filter(Boolean).join(' · ')}
          </Text>
        </View>

        <View style={styles.body}>
          <View style={styles.sectionHead}>
            <Text style={styles.sectionLabel}>Coverage by room</Text>
            <Text style={styles.sectionNote}>{coveredCount} / {totalCount} captured</Text>
          </View>
          <View style={styles.roomGrid}>
            {rooms.length === 0 && !loading ? (
              <Text style={styles.emptyText}>No rooms recorded yet.</Text>
            ) : (
              rooms.map(r => (
                <View key={r.id ?? r.name} style={styles.roomCell}>
                  <View style={[styles.dot, { backgroundColor: TONE_COLOR[r.tone] ?? COLORS.bad }]} />
                  <Text style={styles.roomName}>{r.name}</Text>
                  <Text style={styles.roomCount}>{r.n}</Text>
                </View>
              ))
            )}
          </View>

          {!loading && (stationCount === 0 || thinStations.length > 0) && (
            <View style={styles.warnBanner}>
              <Text style={styles.warnText}>
                {stationCount === 0
                  ? 'Submit needs at least one 360 station (coverage dome), not snapshot stills.'
                  : `Station ${thinStations[0].station_index} sweep is thin — fill the dome (8+ overlapping pans) or capture an equirect.`}
              </Text>
            </View>
          )}

          <Text style={[styles.sectionLabel, { marginTop: 24 }]}>Pipeline</Text>
          <View style={{ marginTop: 12 }}>
            {pipeline.length === 0 && !loading ? (
              <Text style={styles.emptyText}>No pipeline yet — submit is unavailable until a session exists.</Text>
            ) : (
              pipeline.map((p, i) => {
                const done = p.status === 'done', active = p.status === 'active';
                return (
                  <View key={p.title} style={styles.pipeRow}>
                    <View style={{ alignItems: 'center', width: 22 }}>
                      <View style={[styles.pipeDot, done && { backgroundColor: COLORS.ok }, active && { backgroundColor: COLORS.brick }]} />
                      {i !== pipeline.length - 1 && <View style={[styles.pipeLine, done && { backgroundColor: COLORS.ok }]} />}
                    </View>
                    <View style={{ flex: 1, marginLeft: 12 }}>
                      <Text style={[styles.pipeTitle, p.status === 'todo' && { color: COLORS.ink3 }]}>{p.title}</Text>
                      <Text style={styles.pipeDetail}>{p.detail}</Text>
                    </View>
                    {active && <Text style={styles.runningBadge}>In progress</Text>}
                  </View>
                );
              })
            )}
          </View>
        </View>
      </ScrollView>

      <View style={styles.submitBar}>
        <View>
          <Text style={styles.submitLabel}>Est. delivery</Text>
          <Text style={styles.submitEta}>
            {session?.estimatedDeliveryHours != null
              ? `~${session.estimatedDeliveryHours} hrs`
              : '—'}
          </Text>
        </View>
        <TouchableOpacity
          style={[styles.submitBtn, (submitting || !canSubmit) && { opacity: 0.5 }]}
          onPress={handleSubmit}
          disabled={submitting || loading || !canSubmit}
        >
          <Text style={styles.submitBtnText}>
            {submitting ? 'Submitting…' : 'Submit to pipeline →'}
          </Text>
        </TouchableOpacity>
      </View>

      {submitError && (
        <View style={styles.errorBar}>
          <Text style={styles.errorText}>{submitError}</Text>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.paper },
  loadingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(12,11,9,0.6)',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 10,
  },
  loadingText: { fontFamily: FONTS.mono, fontSize: 12, color: COLORS.ink, marginTop: 12, letterSpacing: 1 },
  hero: { height: 200, backgroundColor: '#0C0B09', justifyContent: 'flex-end', padding: 18 },
  backChip: { position: 'absolute', top: 14, left: 18, width: 32, height: 32, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
  backChipText: { color: '#F4F1EB', fontSize: 18 },
  heroAddress: { fontFamily: FONTS.bodySemibold, fontSize: 19, color: '#F4F1EB' },
  heroMeta: { fontFamily: FONTS.mono, fontSize: 11, color: '#F4F1EBBB', marginTop: 2, letterSpacing: 0.5 },
  body: { padding: 22 },
  sectionHead: { flexDirection: 'row', justifyContent: 'space-between' },
  sectionLabel: { fontFamily: FONTS.mono, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', color: COLORS.ink3 },
  sectionNote: { fontFamily: FONTS.mono, fontSize: 11, color: COLORS.ink3 },
  roomGrid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 12, borderWidth: 1, borderColor: COLORS.hair, borderRadius: 2 },
  roomCell: { width: '25%', paddingVertical: 12, alignItems: 'center', borderWidth: 0.5, borderColor: COLORS.hair },
  dot: { width: 8, height: 8, borderRadius: 4, marginBottom: 7 },
  roomName: { fontFamily: FONTS.bodySemibold, fontSize: 11.5, textAlign: 'center' },
  roomCount: { fontFamily: FONTS.mono, fontSize: 10, color: COLORS.ink3, marginTop: 3 },
  emptyText: { fontFamily: FONTS.body, fontSize: 13, color: COLORS.ink3, width: '100%', textAlign: 'center', paddingVertical: 20 },
  warnBanner: { marginTop: 10, flexDirection: 'row', alignItems: 'center', padding: 10, backgroundColor: '#8C2F2418', borderRadius: 2 },
  warnText: { fontFamily: FONTS.body, fontSize: 11.5, color: COLORS.ink2, flex: 1 },
  pipeRow: { flexDirection: 'row', paddingBottom: 16 },
  pipeDot: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, borderColor: COLORS.ink3 },
  pipeLine: { width: 1.5, flex: 1, minHeight: 22, backgroundColor: COLORS.ink3, marginTop: 2 },
  pipeTitle: { fontFamily: FONTS.bodySemibold, fontSize: 14, color: COLORS.ink },
  pipeDetail: { fontFamily: FONTS.body, fontSize: 11.5, color: COLORS.ink3, marginTop: 2 },
  runningBadge: { fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.brick, alignSelf: 'flex-start' },
  submitBar: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 18, borderTopWidth: 1, borderTopColor: COLORS.hair, backgroundColor: COLORS.paper },
  submitLabel: { fontFamily: FONTS.mono, fontSize: 9.5, letterSpacing: 1.5, textTransform: 'uppercase', color: COLORS.ink3 },
  submitEta: { fontFamily: FONTS.mono, fontSize: 17, color: COLORS.ink, marginTop: 2 },
  submitBtn: { marginLeft: 'auto', backgroundColor: COLORS.brick, borderRadius: 2, paddingHorizontal: 20, paddingVertical: 13 },
  submitBtnText: { fontFamily: FONTS.bodySemibold, fontSize: 14, color: '#fff' },
  errorBar: { position: 'absolute', bottom: 100, left: 20, right: 20, backgroundColor: COLORS.bad, padding: 12, borderRadius: 2 },
  errorText: { fontFamily: FONTS.body, fontSize: 12, color: '#fff', textAlign: 'center' },
  resultCard: { borderWidth: 1, borderRadius: 2, padding: 18, gap: 10 },
  resultCompiling: { borderColor: 'rgba(210, 84, 43, 0.55)', backgroundColor: 'rgba(210, 84, 43, 0.08)' },
  resultReady: { borderColor: 'rgba(47, 107, 79, 0.55)', backgroundColor: 'rgba(47, 107, 79, 0.08)' },
  resultFailed: { borderColor: 'rgba(139, 46, 31, 0.7)', backgroundColor: 'rgba(139, 46, 31, 0.08)' },
  resultTitle: { fontFamily: FONTS.bodySemibold, fontSize: 18, color: COLORS.ink },
  resultBody: { fontFamily: FONTS.body, fontSize: 13, color: COLORS.ink2, lineHeight: 18 },
  resultActions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 },
  resultBtn: { borderWidth: 1, borderColor: COLORS.hair, borderRadius: 2, paddingHorizontal: 14, paddingVertical: 10 },
  resultBtnText: { fontFamily: FONTS.bodySemibold, fontSize: 13, color: COLORS.ink },
  resultBtnPrimary: { backgroundColor: COLORS.brick, borderRadius: 2, paddingHorizontal: 14, paddingVertical: 10 },
  resultBtnPrimaryText: { fontFamily: FONTS.bodySemibold, fontSize: 13, color: '#fff' },
  resultLink: { paddingHorizontal: 8, paddingVertical: 10 },
  resultLinkText: { fontFamily: FONTS.body, fontSize: 13, color: COLORS.brick },
});
