import { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, ScrollView, StyleSheet, Platform } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { CameraView, useCameraPermissions } from 'expo-camera';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import * as Haptics from 'expo-haptics';
import { DoorOpen, ListChecks } from 'lucide-react-native';
import { color, type as T, space, layout, radius, hairline } from '../../theme';
import {
  hud, HudScrims, HudTopBar, HudReadout, CoachCard, Shutter, HudControl, HudControls, CameraPermission, ScreenshotFeed,
} from '../../components/capture-hud';
import { useMotionCoach } from '../../lib/motion-coach';
import {
  VISIT_CAMERA, VISIT_LIMITS, BYTES_PER_SECOND_EST, ROOM_PRESETS, fmtClock, fmtBytes, newLocalId, persistVideo,
  saveLocalRecording, type RoomMark, type RecordingScope, type LocalRecording,
} from '../../lib/visit-capture';

const KEEP_AWAKE_TAG = 'visit-record';
const IS_WEB = Platform.OS === 'web';

/** Room walk method (SplatTour-style): perimeter lap, reverse lap, corner to corner. Paced by time in room. */
function phaseFor(secInRoom: number, scope: RecordingScope) {
  if (secInRoom < 30) return { step: 'Lap 1 of 3', title: 'Walk along the walls', body: 'Slow lap, phone upright, looking into the room.' };
  if (secInRoom < 60) return { step: 'Lap 2 of 3', title: 'Same lap, other direction', body: 'Shows each wall from the other side.' };
  if (secInRoom < 90) return { step: 'Lap 3 of 3', title: 'Corner to corner', body: 'Cross both diagonals through the middle.' };
  return scope === 'floor'
    ? { step: 'Room done', title: 'Walk to the next room', body: 'Keep recording. Tap Next room as you go in.' }
    : { step: 'Room done', title: 'Stop when you’re ready', body: 'Tap the square to finish this recording.' };
}

/** Lens: prefer the ultra-wide (0.5×) on iOS. expo-camera can't pick lenses on Android yet. */
function pickUltraWide(lenses: string[]) {
  return lenses.find((l) => /ultra\s*wide/i.test(l)) ?? null;
}

export default function VisitRecord() {
  const router = useRouter();
  const p = useLocalSearchParams<{ jobId: string; name?: string; scope?: string; floor?: string; demo?: string }>();
  const jobId = p.jobId;
  const scope: RecordingScope = p.scope === 'room' ? 'room' : 'floor';
  const recName = (p.name || (scope === 'floor' ? 'Main floor' : 'Room')).slice(0, 60);
  const demo = IS_WEB && p.demo === 'recording'; // web screenshot capture only

  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const [ready, setReady] = useState(IS_WEB);
  const [lens, setLens] = useState<string | null>(null);
  const [recording, setRecording] = useState(demo);
  const [elapsed, setElapsed] = useState(demo ? 252 : 0);
  const [rooms, setRooms] = useState<RoomMark[]>(demo
    ? [{ name: 'Entrance', t0: 0, t1: 48 }, { name: 'Kitchen', t0: 48, t1: 212 }, { name: 'Living', t0: 212, t1: 0 }]
    : []);
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef<number>(0);
  const roomsRef = useRef<RoomMark[]>(rooms);
  roomsRef.current = rooms;
  const coach = useMotionCoach(!IS_WEB);

  // Timer + hard limits. maxDuration on recordAsync is the real stop; this keeps the UI honest.
  useEffect(() => {
    if (!recording || demo) return;
    const id = setInterval(() => {
      const s = (Date.now() - startedAt.current) / 1000;
      setElapsed(s);
      if (Math.floor(s) === VISIT_LIMITS.warnSeconds) Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
    }, 250);
    return () => clearInterval(id);
  }, [recording, demo]);

  useEffect(() => () => { deactivateKeepAwake(KEEP_AWAKE_TAG); }, []);

  const currentRoom = rooms.length ? rooms[rooms.length - 1] : null;
  const secInRoom = currentRoom ? elapsed - currentRoom.t0 : elapsed;
  const estBytes = elapsed * BYTES_PER_SECOND_EST;
  const left = VISIT_LIMITS.maxSeconds - elapsed;

  const finish = async (uri: string | null) => {
    const duration = Math.min(VISIT_LIMITS.maxSeconds, (Date.now() - startedAt.current) / 1000);
    const marks = roomsRef.current.map((r, i, arr) => ({ ...r, t1: i === arr.length - 1 ? Math.round(duration * 100) / 100 : r.t1 }));
    const localId = newLocalId();
    if (!uri) {
      router.replace({ pathname: '/(capture)/visit-review', params: { jobId, preview: '1', name: recName } } as any);
      return;
    }
    try {
      const saved = await persistVideo(uri, localId);
      const rec: LocalRecording = {
        localId, jobId, name: recName, scope, floorIndex: Number(p.floor || 0) || 0, uri: saved.uri,
        sizeBytes: saved.size, durationS: Math.round(duration * 100) / 100, lens, rooms: scope === 'floor' ? marks : [],
        coach: coach.available ? coach.summary() : null, createdAt: Date.now(), committed: [], state: 'recorded',
      };
      await saveLocalRecording({ ...rec, state: 'recorded' });
      router.replace({ pathname: '/(capture)/visit-review', params: { jobId, localId } } as any);
    } catch (e: any) {
      setError(e?.message || 'Could not save the video');
    }
  };

  const start = async () => {
    setError(null);
    coach.reset();
    startedAt.current = Date.now();
    setElapsed(0);
    setRooms(scope === 'floor' ? [{ name: 'Entrance', t0: 0, t1: 0 }] : []);
    setRecording(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {});
    if (IS_WEB) return; // web preview has no recorder
    try {
      const res = await cameraRef.current?.recordAsync({
        maxDuration: VISIT_LIMITS.maxSeconds,
        maxFileSize: VISIT_LIMITS.maxBytes,
        codec: Platform.OS === 'ios' ? VISIT_CAMERA.codec : undefined,
      });
      setRecording(false);
      deactivateKeepAwake(KEEP_AWAKE_TAG);
      await finish(res?.uri ?? null);
    } catch (e: any) {
      setRecording(false);
      deactivateKeepAwake(KEEP_AWAKE_TAG);
      setError(e?.message || 'Recording stopped unexpectedly');
    }
  };

  const stop = () => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    if (IS_WEB) {
      setRecording(false);
      deactivateKeepAwake(KEEP_AWAKE_TAG);
      finish(null);
      return;
    }
    cameraRef.current?.stopRecording(); // recordAsync resolves → finish()
  };

  const markRoom = (name: string) => {
    const t = Math.round(elapsed * 100) / 100;
    setRooms((cur) => {
      const closed = cur.map((r, i) => (i === cur.length - 1 ? { ...r, t1: t } : r));
      return [...closed, { name, t0: t, t1: 0 }];
    });
    setPicker(false);
    Haptics.selectionAsync().catch(() => {});
  };

  if (!IS_WEB && !permission) return <View style={styles.dark} />;
  if (!IS_WEB && permission && !permission.granted) {
    return <CameraPermission onAllow={requestPermission} onClose={() => router.back()} body="Bricks uses the camera only while you record this home’s walk-through. No sound is recorded." />;
  }

  const phase = phaseFor(secInRoom, scope);
  const cue = recording ? coach.cue : null;
  const limitWarn = recording && elapsed >= VISIT_LIMITS.warnSeconds;
  const lensLabel = IS_WEB ? '0.5×' : lens ? '0.5×' : '1×';

  let card: { step?: string; title: string; body?: string; tone: 'accent' | 'warn' | 'bad' | 'ok' };
  if (error) card = { title: 'Recording stopped', body: error, tone: 'bad' };
  else if (limitWarn) card = { step: `${fmtClock(left)} left`, title: 'Wrap up this floor', body: 'Recording stops at 15:00. Record the rest as another floor.', tone: 'warn' };
  else if (cue) card = { step: 'Coach', title: cue.title, body: cue.body, tone: 'warn' };
  else if (recording) card = { ...phase, tone: 'accent' };
  else card = {
    step: 'Ready',
    title: scope === 'floor' ? 'Start at the front door' : `Start in the doorway of ${recName}`,
    body: 'Portrait, chest height. One take: don’t stop between rooms.',
    tone: 'accent',
  };

  return (
    <View style={styles.dark}>
      {!IS_WEB || !demo ? (
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing="back"
          mode="video"
          mute
          videoQuality={VISIT_CAMERA.quality}
          videoBitrate={VISIT_CAMERA.bitrate}
          videoStabilizationMode={VISIT_CAMERA.stabilization}
          autofocus="off"
          selectedLens={lens ?? undefined}
          onCameraReady={() => setReady(true)}
          onAvailableLensesChanged={(e) => { const uw = pickUltraWide(e.lenses); if (uw && uw !== lens) setLens(uw); }}
          onMountError={(e) => setError(e.message)}
        />
      ) : null}
      <ScreenshotFeed />
      <HudScrims top={recording && scope === 'floor' && rooms.length > 1 ? 222 : 178} bottom={260} />

      <SafeAreaView style={{ flex: 1 }}>
        <HudTopBar
          title={recName}
          subtitle={`Visit · ${scope === 'floor' ? 'Whole floor' : 'One room'}`}
          onClose={() => (recording ? stop() : router.back())}
          right={recording ? (
            <View style={styles.rec} accessibilityLabel="Recording">
              <View style={styles.recMark} />
              <Text style={[T.overline, { color: color.text }]}>Rec</Text>
            </View>
          ) : undefined}
        />
        <HudReadout
          style={styles.readout}
          items={[
            { label: 'Time', value: `${fmtClock(elapsed)}/${fmtClock(VISIT_LIMITS.maxSeconds)}`, tone: limitWarn ? 'warn' : undefined },
            { label: 'Size', value: `~${fmtBytes(estBytes)}` },
            { label: 'Lens', value: lensLabel, tone: lensLabel === '1×' ? 'warn' : undefined },
            { label: scope === 'floor' ? 'Room' : 'Steady', value: scope === 'floor' ? (currentRoom?.name ?? '—') : `${IS_WEB ? 92 : coach.steady}` },
          ]}
        />

        <View style={styles.middle} pointerEvents="box-none">
          {recording && scope === 'floor' && rooms.length > 1 ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.trailScroll} contentContainerStyle={styles.trail}>
              {rooms.map((r, i) => (
                <View key={`${r.name}-${i}`} style={[styles.trailItem, i === rooms.length - 1 && styles.trailItemOn]}>
                  <Text style={[T.dataSm, { color: i === rooms.length - 1 ? color.textInverse : color.textBody }]}>{r.name}</Text>
                </View>
              ))}
            </ScrollView>
          ) : null}
        </View>

        <CoachCard step={card.step} title={card.title} body={card.body} tone={card.tone} style={styles.coach} />

        {picker ? (
          <View style={styles.picker}>
            <Text style={[T.overline, { color: color.textMuted }]}>Walking into</Text>
            <View style={styles.pickerGrid}>
              {ROOM_PRESETS.map((r) => (
                <Pressable key={r} onPress={() => markRoom(r)} accessibilityRole="button" style={({ pressed }) => [styles.pickerItem, pressed && styles.pickerItemOn]}>
                  <Text style={[T.callout, { color: color.text }]}>{r}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}

        <HudControls
          left={scope === 'floor'
            ? <HudControl icon={DoorOpen} label="Next room" onPress={() => setPicker((v) => !v)} disabled={!recording} tone={picker ? 'accent' : undefined} />
            : undefined}
          center={(
            <Shutter
              mode="record"
              active={recording}
              disabled={!ready}
              onPress={recording ? stop : start}
              label={recording ? 'Stop recording' : 'Start recording'}
            />
          )}
          right={!recording ? <HudControl icon={ListChecks} label="Checklist" onPress={() => router.back()} /> : undefined}
        />
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  dark: { flex: 1, backgroundColor: color.bg },
  readout: { marginTop: space[12] },
  middle: { flex: 1, justifyContent: 'flex-start', paddingTop: space[12] },
  trailScroll: { flexGrow: 0 },
  trail: { paddingHorizontal: layout.gutter, gap: space[6], alignItems: 'flex-start' },
  rec: {
    width: layout.tap, height: layout.tap, alignItems: 'center', justifyContent: 'center', gap: 3,
    backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.bad,
  },
  recMark: { width: 8, height: 8, borderRadius: 1, backgroundColor: color.bad },
  trailItem: {
    paddingHorizontal: space[10], paddingVertical: space[6], borderRadius: radius.sm,
    backgroundColor: hud.panelSoft, borderWidth: hairline, borderColor: color.hairStrong,
  },
  trailItemOn: { backgroundColor: color.inverse, borderColor: color.inverse },
  coach: { marginHorizontal: layout.gutter, marginBottom: space[12] },
  picker: {
    marginHorizontal: layout.gutter, marginBottom: space[12], padding: space[12], gap: space[10],
    backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong,
  },
  pickerGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: space[8] },
  pickerItem: {
    minHeight: layout.tap, paddingHorizontal: space[12], justifyContent: 'center',
    borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong,
  },
  pickerItemOn: { backgroundColor: color.accent, borderColor: color.accent },
});
