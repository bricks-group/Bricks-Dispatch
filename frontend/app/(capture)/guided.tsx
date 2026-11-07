/**
 * Guided capture (native). Data-driven by lib/guided-capture/guides.ts: the guide decides the lens,
 * format, locks, steps and motion limits. Virtual visit is the first guide; film clips and stills
 * plug in as more guides (they run on dedicated cameras, so the device policy differs).
 *
 * Flow: device check → steps → camera opens on the ultra-wide → metering ("Point at the brightest
 * room", Lock) → countdown → record with a live level + speed coach → save locally → visit-review,
 * which uploads through the existing resumable Visit video intake.
 *
 * Needs a native build with react-native-vision-camera. In Expo Go (no Nitro modules) it falls
 * back to the expo-camera recorder (visit-record) with the same params.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, Pressable, ScrollView, StyleSheet, Platform } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import * as Device from 'expo-device';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import * as Haptics from 'expo-haptics';
import { DoorOpen, Smartphone, Footprints, Pause, RotateCw, ListChecks, RefreshCw, Lock, type LucideIcon } from 'lucide-react-native';
import { color, type as T, space, layout, radius, hairline, toneColors, type Tone } from '../../theme';
import { Screen, NavBar, Section, Group, Row, Footer, Button } from '../../components/ui';
import { hud, HudScrims, HudTopBar, HudReadout, CoachCard, Shutter, HudControl, HudControls, CameraPermission } from '../../components/capture-hud';
import { getGuide, LOCK_LABEL, estimateBytes, type LockKind, type StepIcon } from '../../lib/guided-capture/guides';
import { checkDevice, POLICY_LINE } from '../../lib/guided-capture/device-policy';
import { useNativeMotion } from '../../lib/guided-capture/useNativeMotion';
import type { CoachReading } from '../../lib/guided-capture/motion';
import { VISIT_LIMITS, fmtClock, fmtBytes, newLocalId, persistVideo, saveLocalRecording, type LocalRecording, type RecordingScope } from '../../lib/visit-capture';
import type { CameraInfo, GuidedCameraHandle, LockState, LockStatus } from '../../components/guided/GuidedCamera';

type GC = typeof import('../../components/guided/GuidedCamera');
let Native: GC | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const mod: GC = require('../../components/guided/GuidedCamera');
  Native = mod.NATIVE_GUIDED_CAMERA ? mod : null;
} catch {
  Native = null; // Expo Go or a build without VisionCamera
}

const KEEP_AWAKE_TAG = 'guided-capture';
type Phase = 'intro' | 'meter' | 'locked' | 'countdown' | 'recording' | 'saving';

const STEP_ICON: Partial<Record<StepIcon, LucideIcon>> = { door: DoorOpen, phone: Smartphone, walk: Footprints, pause: Pause, turn: RotateCw };

const LOCK_TEXT: Record<LockStatus, string> = {
  locked: 'Locked', 'metering-locked': 'Held', auto: 'Auto', unsupported: 'Auto', failed: 'Auto',
};
const lockTone = (s: LockStatus): Tone => (s === 'locked' ? 'ok' : s === 'metering-locked' ? 'accent' : 'warn');

export default function GuidedCapture() {
  const router = useRouter();
  const p = useLocalSearchParams<{ jobId: string; type?: string; name?: string; scope?: string; floor?: string }>();
  const jobId = p.jobId;
  const guide = getGuide(p.type || 'virtual-visit');
  const scope: RecordingScope = p.scope === 'room' ? 'room' : 'floor';
  const recName = (p.name || 'Main floor').slice(0, 60);

  // No VisionCamera in this build: hand over to the expo-camera recorder.
  useEffect(() => {
    if (Native) return;
    router.replace({ pathname: '/(capture)/visit-record', params: { jobId, name: recName, scope, floor: p.floor ?? '0' } } as any);
  }, [jobId, recName, scope, p.floor, router]);

  const perm = Native ? Native.useGuidedCameraPermission() : null;
  const policy = useMemo(() => checkDevice({
    os: Platform.OS === 'ios' ? 'ios' : Platform.OS === 'android' ? 'android' : 'other',
    modelId: Device.modelId, modelName: Device.modelName, manufacturer: Device.manufacturer,
  }), []);

  const [phase, setPhase] = useState<Phase>('intro');
  const [override, setOverride] = useState(false);
  const [info, setInfo] = useState<CameraInfo | null>(null);
  const [locks, setLocks] = useState<LockState | null>(null);
  const [busy, setBusy] = useState(false);
  const [count, setCount] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [stepsOpen, setStepsOpen] = useState(false);
  const cam = useRef<GuidedCameraHandle>(null);
  const startedAt = useRef(0);
  const inCamera = phase !== 'intro';
  const motion = useNativeMotion(guide?.limits ?? { panDps: 45, walkMps: 0.9, levelDeg: 12, shakeMs2: 2.2, pauseSec: 2, stillSec: 8 }, inCamera);

  useEffect(() => () => { deactivateKeepAwake(KEEP_AWAKE_TAG); }, []);

  // Recording clock + soft warning. The recorder's maxDuration is the hard stop.
  useEffect(() => {
    if (phase !== 'recording' || !guide) return;
    const id = setInterval(() => {
      const s = (Date.now() - startedAt.current) / 1000;
      setElapsed(s);
      if (Math.floor(s) === guide.warnSeconds) Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
    }, 250);
    return () => clearInterval(id);
  }, [phase, guide]);

  // Gentle haptic when a new cue appears while recording.
  const cueKind = phase === 'recording' ? motion.reading.cue?.kind ?? null : null;
  useEffect(() => {
    if (cueKind) Haptics.selectionAsync().catch(() => {});
  }, [cueKind]);

  const finish = useCallback(async (filePath: string) => {
    if (!guide) return;
    setPhase('saving');
    deactivateKeepAwake(KEEP_AWAKE_TAG);
    const duration = Math.min(guide.maxSeconds, (Date.now() - startedAt.current) / 1000);
    const uri = filePath.startsWith('file://') ? filePath : `file://${filePath}`;
    if (duration < guide.minSeconds) {
      try { const { File } = await import('expo-file-system'); const f = new File(uri); if (f.exists) f.delete(); } catch { /* best effort */ }
      setError(`That was under ${guide.minSeconds} seconds. Walk the whole floor in one take.`);
      setPhase('locked');
      return;
    }
    const localId = newLocalId();
    try {
      const saved = await persistVideo(uri, localId);
      const rec: LocalRecording = {
        localId, jobId, name: recName, scope, floorIndex: Number(p.floor || 0) || 0, uri: saved.uri,
        sizeBytes: saved.size, durationS: Math.round(duration * 100) / 100,
        lens: info ? `${info.lens} · ${Platform.OS}` : null,
        rooms: [],
        coach: motion.available ? motion.summary() : null,
        capture: {
          guide: guide.id, guide_version: guide.version, platform: Platform.OS, device: policy.label, device_status: policy.status,
          lens: info?.lens, stabilization: info?.stabilization ?? undefined, hdr: info?.hdr ? 'on' : 'off', codec: info?.codec,
          width: info?.width ?? undefined, height: info?.height ?? undefined, fps: info?.fps ?? undefined, bitrate: info?.bitrate,
          locks: locks ?? undefined,
        },
        createdAt: Date.now(), committed: [], state: 'recorded',
      };
      await saveLocalRecording(rec);
      router.replace({ pathname: '/(capture)/visit-review', params: { jobId, localId } } as any);
    } catch (e: any) {
      setError(e?.message || 'Could not save the video');
      setPhase('locked');
    }
  }, [guide, info, jobId, locks, motion, p.floor, policy, recName, router, scope]);

  const startRecording = useCallback(async () => {
    setError(null);
    motion.reset();
    startedAt.current = Date.now();
    setElapsed(0);
    setPhase('recording');
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {});
    try {
      await cam.current?.start(
        (path) => { finish(path); },
        (e) => { deactivateKeepAwake(KEEP_AWAKE_TAG); setError(e?.message || 'Recording stopped unexpectedly'); setPhase('locked'); },
      );
    } catch (e: any) {
      deactivateKeepAwake(KEEP_AWAKE_TAG);
      setError(e?.message || 'Could not start recording');
      setPhase('locked');
    }
  }, [finish, motion]);

  // Countdown → record. startRecording changes every motion repaint, so read it through a ref.
  const startRef = useRef(startRecording);
  startRef.current = startRecording;
  useEffect(() => {
    if (phase !== 'countdown') return;
    if (count <= 0) { startRef.current(); return; }
    const id = setTimeout(() => { setCount((c) => c - 1); Haptics.selectionAsync().catch(() => {}); }, 1000);
    return () => clearTimeout(id);
  }, [phase, count]);

  if (!guide) {
    return (
      <Screen header={<NavBar title="Guided capture" onBack={() => router.back()} />}>
        <Text style={[T.body, { color: color.textBody, padding: layout.gutter }]}>This capture type isn’t available yet.</Text>
      </Screen>
    );
  }
  if (!Native) return <View style={styles.dark} />;

  /* ───────── intro: device check + steps ───────── */
  if (phase === 'intro') {
    const blocked = policy.status === 'not-approved' && !override;
    return (
      <Screen
        header={<NavBar title={guide.title} onBack={() => router.back()} />}
        contentStyle={styles.body}
        footer={(
          <Footer>
            <Button title="Open camera" onPress={() => { setError(null); setPhase('meter'); }} disabled={blocked} />
          </Footer>
        )}
      >
        <Text style={[T.body, { color: color.textBody }]}>{guide.summary}</Text>

        <Section first label="This phone">
          <View style={[styles.policy, { borderColor: policy.status === 'approved' ? color.hair : toneColors.warn.fg }]}>
            <Text style={[T.bodyMedium, { color: color.text }]}>{policy.label}</Text>
            <Text style={[T.bodySm, { color: policy.status === 'approved' ? color.textBody : toneColors.warn.fg }]}>{policy.reason}</Text>
            {policy.status !== 'approved' ? (
              <>
                <Text style={[T.bodySm, { color: color.textMuted }]}>
                  An unapproved phone can blur or warp the 3D visit, and the recording may need redoing.
                </Text>
                {policy.status === 'not-approved' && !override ? (
                  <Button title="Continue anyway" variant="secondary" onPress={() => setOverride(true)} />
                ) : null}
              </>
            ) : null}
            <Text style={[T.caption, { color: color.textFaint }]}>Approved: {POLICY_LINE}</Text>
          </View>
        </Section>

        <Section label={recName}>
          <Group>
            {guide.steps.map((s) => (
              <Row key={s.id} icon={STEP_ICON[s.icon]} title={s.title} subtitle={s.body} />
            ))}
          </Group>
        </Section>

        <Text style={[T.bodySm, { color: color.textMuted }]}>
          {Platform.OS === 'ios'
            ? 'The camera fixes brightness, focus and colour after you tap Lock, so rooms match.'
            : 'The camera holds brightness, focus and colour after you tap Lock, so rooms match.'}
        </Text>
      </Screen>
    );
  }

  /* ───────── camera ───────── */
  if (perm && !perm.granted) {
    return (
      <CameraPermission
        onAllow={() => { perm.request().catch(() => {}); }}
        onClose={() => setPhase('intro')}
        body="Bricks uses the camera only while you record this home’s walk-through. No sound is recorded."
      />
    );
  }

  const r = motion.reading;
  const recording = phase === 'recording';
  const left = guide.maxSeconds - elapsed;
  const limitWarn = recording && elapsed >= guide.warnSeconds;
  const anyHeld = !!locks && Object.values(locks).some((s) => s === 'metering-locked');

  let card: { step?: string; title: string; body?: string; tone: Tone };
  if (error) card = { step: 'Stopped', title: 'Something went wrong', body: error, tone: 'bad' };
  else if (phase === 'meter') card = { step: 'Step 1 of 2', title: guide.metering.title, body: guide.metering.body, tone: 'accent' };
  else if (phase === 'locked') card = {
    step: 'Step 2 of 2', title: guide.steps[0].title,
    body: anyHeld ? 'Settings are held for this walk. Tap record when you’re in place.' : 'Settings are locked for this walk. Tap record when you’re in place.',
    tone: 'accent',
  };
  else if (phase === 'saving') card = { title: 'Saving the video', tone: 'accent' };
  else if (limitWarn) card = { step: `${fmtClock(left)} left`, title: 'Wrap up this floor', body: 'Record the rest as another floor.', tone: 'warn' };
  else if (recording && r.cue) card = { step: 'Coach', title: r.cue.title, body: r.cue.body, tone: 'warn' };
  else if (recording && r.paused) card = { step: 'Paused', title: 'Good. Now carry on', body: 'Walk slowly into the next room.', tone: 'ok' };
  else if (recording) card = { step: fmtClock(elapsed), title: 'Walk slowly', body: 'Pause 2 seconds in each room. Turn gently.', tone: 'accent' };
  else card = { title: 'Get ready', tone: 'accent' };

  const lock = async () => {
    setBusy(true);
    setError(null);
    try {
      const s = await cam.current?.lock();
      if (s) setLocks(s);
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
      setPhase('locked');
    } catch (e: any) {
      setError(e?.message || 'Could not lock the camera');
    } finally {
      setBusy(false);
    }
  };
  const meterAgain = async () => {
    await cam.current?.unlock();
    setLocks(null);
    setError(null);
    setPhase('meter');
  };
  const stop = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    cam.current?.stop().catch((e: any) => setError(e?.message || 'Could not stop'));
  };
  const close = () => {
    if (recording) { cam.current?.cancel().catch(() => {}); deactivateKeepAwake(KEEP_AWAKE_TAG); }
    router.back();
  };

  const format = info
    ? `${info.height && info.height >= 2160 ? '4K' : info.height ? `${info.height}p` : '—'} · ${info.fps ?? '—'} fps`
    : '—';

  return (
    <View style={styles.dark}>
      <Native.GuidedCamera
        ref={cam}
        guide={guide}
        isActive={phase !== 'saving'}
        maxBytes={VISIT_LIMITS.maxBytes}
        onReady={setInfo}
        onError={(m) => setError(m)}
      />
      <HudScrims top={200} bottom={300} />

      <SafeAreaView style={{ flex: 1 }}>
        <HudTopBar
          title={recName}
          subtitle={guide.title}
          onClose={close}
          right={recording ? (
            <View style={styles.rec} accessibilityLabel="Recording">
              <View style={styles.recMark} />
              <Text style={[T.overline, { color: color.text }]}>Rec</Text>
            </View>
          ) : undefined}
        />

        <View style={styles.chips}>
          {guide.locks.map((k: LockKind) => {
            const s: LockStatus = locks?.[k] ?? 'auto';
            const tone = locks ? lockTone(s) : 'neutral';
            return (
              <View key={k} style={[styles.chip, { borderColor: tone === 'neutral' ? color.hairStrong : toneColors[tone].fg }]} accessibilityLabel={`${LOCK_LABEL[k]} ${LOCK_TEXT[s]}`}>
                <Text style={[T.tabLabel, { color: color.textMuted }]}>{LOCK_LABEL[k]}</Text>
                <Text style={[T.dataSm, { color: tone === 'neutral' ? color.text : toneColors[tone].fg }]}>{LOCK_TEXT[s]}</Text>
              </View>
            );
          })}
        </View>

        <HudReadout
          style={styles.readout}
          items={recording ? [
            { label: 'Time', value: `${fmtClock(elapsed)}/${fmtClock(guide.maxSeconds)}`, tone: limitWarn ? 'warn' : undefined },
            { label: 'Size', value: `~${fmtBytes(estimateBytes(guide, elapsed))}` },
            { label: 'Lens', value: info?.lensLabel ?? '—', tone: info && info.lens !== 'ultra-wide' ? 'warn' : undefined },
            { label: 'Steady', value: motion.available ? `${r.steady}` : '—' },
          ] : [
            { label: 'Lens', value: info?.lensLabel ?? '—', tone: info && info.lens !== 'ultra-wide' ? 'warn' : undefined },
            { label: 'Format', value: format },
            { label: 'Stabilised', value: info?.stabilization && info.stabilization !== 'off' ? 'On' : info ? 'Off' : '—' },
            { label: 'HDR', value: info ? (info.hdr ? 'On' : 'Off') : '—', tone: info?.hdr ? 'warn' : undefined },
          ]}
        />

        <View style={styles.middle} pointerEvents="box-none">
          {phase === 'countdown' ? (
            <View style={styles.countWrap} accessibilityLiveRegion="assertive">
              <Text style={styles.count}>{count > 0 ? count : ''}</Text>
            </View>
          ) : null}
        </View>

        {motion.available ? <Meters reading={r} recording={recording} limits={guide.limits} /> : null}

        <CoachCard step={card.step} title={card.title} body={card.body} tone={card.tone} style={styles.coach} />

        {phase === 'meter' ? (
          <View style={styles.actions}>
            <Button title={guide.metering.action} icon={Lock} onPress={lock} loading={busy} disabled={!info} />
          </View>
        ) : null}
        {phase === 'locked' && anyHeld ? (
          <Text style={[T.caption, styles.note]}>Android holds these by metering. A very bright window can still nudge them.</Text>
        ) : null}

        {stepsOpen ? (
          <View style={styles.sheet}>
            {guide.steps.map((s, i) => (
              <Text key={s.id} style={[T.bodySm, { color: color.text }]}>{i + 1}. {s.title}</Text>
            ))}
          </View>
        ) : null}

        <HudControls
          left={!recording ? <HudControl icon={ListChecks} label="Steps" onPress={() => setStepsOpen((v) => !v)} tone={stepsOpen ? 'accent' : undefined} /> : undefined}
          center={(
            <Shutter
              mode="record"
              active={recording}
              busy={phase === 'saving'}
              disabled={phase === 'meter' || phase === 'countdown'}
              onPress={recording ? stop : () => { setError(null); setStepsOpen(false); setCount(guide.countdownSec); setPhase('countdown'); }}
              label={recording ? 'Stop recording' : 'Start recording'}
            />
          )}
          right={phase === 'locked' ? <HudControl icon={RefreshCw} label="Meter again" onPress={meterAgain} /> : undefined}
        />
      </SafeAreaView>
    </View>
  );
}

/** Level (roll) bar plus walk and turn meters. Gentle: colours shift, nothing flashes. */
function Meters({ reading, recording, limits }: { reading: CoachReading; recording: boolean; limits: { levelDeg: number; walkMps: number; panDps: number } }) {
  const roll = Math.max(-30, Math.min(30, reading.rollDeg));
  const levelOk = Math.abs(reading.rollDeg) <= limits.levelDeg;
  const walkPct = Math.min(1, reading.walkMps / (limits.walkMps * 1.5));
  const panPct = Math.min(1, reading.panDps / (limits.panDps * 1.5));
  return (
    <View style={styles.meters} accessibilityLabel={`Level ${Math.round(reading.rollDeg)} degrees`}>
      <View style={styles.meterRow}>
        <Text style={[T.tabLabel, styles.meterLabel]}>Level</Text>
        <View style={styles.levelTrack}>
          <View style={styles.levelCentre} />
          <View style={[styles.levelDot, { left: `${50 + (roll / 30) * 50}%`, backgroundColor: levelOk ? color.ok : color.warn }]} />
        </View>
        <Text style={[T.dataSm, styles.meterValue]}>{`${Math.round(reading.rollDeg)}°`}</Text>
      </View>
      {recording ? (
        <>
          <Bar label="Walk" pct={walkPct} warn={reading.walkMps > limits.walkMps} value={`${reading.walkMps.toFixed(1)} m/s`} />
          <Bar label="Turn" pct={panPct} warn={reading.panDps > limits.panDps} value={`${reading.panDps}°/s`} />
        </>
      ) : null}
    </View>
  );
}

function Bar({ label, pct, warn, value }: { label: string; pct: number; warn: boolean; value: string }) {
  return (
    <View style={styles.meterRow}>
      <Text style={[T.tabLabel, styles.meterLabel]}>{label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, { width: `${Math.round(pct * 100)}%`, backgroundColor: warn ? color.warn : color.text }]} />
        <View style={[styles.barLimit, { left: `${Math.round((1 / 1.5) * 100)}%` }]} />
      </View>
      <Text style={[T.dataSm, styles.meterValue]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  dark: { flex: 1, backgroundColor: color.bg },
  body: { gap: space[16] },
  policy: { gap: space[8], padding: space[14], borderWidth: hairline, borderRadius: radius.sm, backgroundColor: color.surface },
  chips: { flexDirection: 'row', gap: space[6], paddingHorizontal: layout.gutter, marginTop: space[10] },
  chip: {
    flex: 1, minHeight: layout.tap, justifyContent: 'center', paddingHorizontal: space[8], gap: 2,
    backgroundColor: hud.panelSoft, borderWidth: hairline, borderRadius: radius.sm,
  },
  readout: { marginTop: space[8] },
  middle: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  countWrap: { width: 120, height: 120, alignItems: 'center', justifyContent: 'center', backgroundColor: hud.panel, borderRadius: radius.sm },
  count: { ...T.kpi, fontSize: 64, lineHeight: 72, color: color.text },
  meters: {
    marginHorizontal: layout.gutter, marginBottom: space[8], padding: space[12], gap: space[8],
    backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hair,
  },
  meterRow: { flexDirection: 'row', alignItems: 'center', gap: space[10] },
  meterLabel: { width: 36, color: color.textMuted },
  meterValue: { width: 64, textAlign: 'right', color: color.textBody },
  levelTrack: { flex: 1, height: 12, justifyContent: 'center' },
  levelCentre: { position: 'absolute', left: '50%', width: 1, height: 12, backgroundColor: color.hairStrong },
  levelDot: { position: 'absolute', width: 12, height: 12, marginLeft: -6, borderRadius: radius.sm },
  barTrack: { flex: 1, height: 4, backgroundColor: color.hairStrong, borderRadius: radius.sm, overflow: 'hidden' },
  barFill: { height: 4 },
  barLimit: { position: 'absolute', top: 0, width: 1, height: 4, backgroundColor: color.textMuted },
  coach: { marginHorizontal: layout.gutter, marginBottom: space[10] },
  actions: { marginHorizontal: layout.gutter, marginBottom: space[10] },
  note: { color: color.textMuted, marginHorizontal: layout.gutter, marginBottom: space[10] },
  sheet: {
    marginHorizontal: layout.gutter, marginBottom: space[10], padding: space[12], gap: space[6],
    backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong,
  },
  rec: {
    width: layout.tap, height: layout.tap, alignItems: 'center', justifyContent: 'center', gap: 3,
    backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.bad,
  },
  recMark: { width: 8, height: 8, borderRadius: 1, backgroundColor: color.bad },
});
