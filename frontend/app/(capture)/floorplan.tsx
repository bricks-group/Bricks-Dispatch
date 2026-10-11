import { useState, useEffect, useRef } from 'react';
import { View, Text, Pressable, StyleSheet, TextInput, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import Svg, { Rect, Line } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { color, type as T, space, radius, layout, hairline } from '../../theme';
import { Plus, Check } from 'lucide-react-native';
import { hud, HudScrims, HudTopBar, HudIconButton, HudReadout, CoachCard, Shutter, HudControl, HudControls, CameraPermission, ScreenshotFeed } from '../../components/capture-hud';
import { addCaptureRoom, ensureCaptureSession, openCaptureStation, uploadCaptureFrame } from '../../lib/capture-api';
import type { CaptureSession } from '../../lib/capture-api';

const KIND = 'floorplan' as const;
const CORNER_TOTAL = 4;

const CUES = [
  { step: 'Corner 1 of 4', title: 'Aim at a corner', body: 'Stand near the middle of the room. Frame one corner, floor to ceiling.' },
  { step: 'Corner 2 of 4', title: 'Turn to the next corner', body: 'Same spot on the floor, so the room lines up.' },
  { step: 'Corner 3 of 4', title: 'Third corner', body: 'Keep a doorway edge in frame if you can see one.' },
  { step: 'Corner 4 of 4', title: 'Last corner, then next room', body: 'Name the next room above and walk there.' },
];

function CornerBox({ captured, total }: { captured: number; total: number }) {
  const on = (i: number) => i < Math.min(captured, total);
  return (
    <Svg width={160} height={110} viewBox="0 0 160 110">
      <Rect x={18} y={12} width={124} height={86} fill="none" stroke={color.hairStrong} strokeWidth={1.5} />
      <Line x1={18} y1={12} x2={36} y2={12} stroke={on(0) ? color.accent : color.hairStrong} strokeWidth={on(0) ? 4 : 2} />
      <Line x1={18} y1={12} x2={18} y2={30} stroke={on(0) ? color.accent : color.hairStrong} strokeWidth={on(0) ? 4 : 2} />
      <Line x1={142} y1={12} x2={124} y2={12} stroke={on(1) ? color.accent : color.hairStrong} strokeWidth={on(1) ? 4 : 2} />
      <Line x1={142} y1={12} x2={142} y2={30} stroke={on(1) ? color.accent : color.hairStrong} strokeWidth={on(1) ? 4 : 2} />
      <Line x1={142} y1={98} x2={124} y2={98} stroke={on(2) ? color.accent : color.hairStrong} strokeWidth={on(2) ? 4 : 2} />
      <Line x1={142} y1={98} x2={142} y2={80} stroke={on(2) ? color.accent : color.hairStrong} strokeWidth={on(2) ? 4 : 2} />
      <Line x1={18} y1={98} x2={36} y2={98} stroke={on(3) ? color.accent : color.hairStrong} strokeWidth={on(3) ? 4 : 2} />
      <Line x1={18} y1={98} x2={18} y2={80} stroke={on(3) ? color.accent : color.hairStrong} strokeWidth={on(3) ? 4 : 2} />
    </Svg>
  );
}

export default function FloorPlanCaptureScreen() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const shutterBusy = useRef(false);

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [frameCount, setFrameCount] = useState(0);
  const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
  const [shutterPressed, setShutterPressed] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [newRoomName, setNewRoomName] = useState('');
  const [addingRoom, setAddingRoom] = useState(false);
  const headingRef = useRef(0);
  const [stationIndex, setStationIndex] = useState(0);

  useEffect(() => {
    let sub: Location.LocationSubscription | undefined;
    (async () => {
      try {
        const perm = await Location.requestForegroundPermissionsAsync();
        if (perm.status !== 'granted') return;
        sub = await Location.watchHeadingAsync((h) => {
          headingRef.current = Number(h.trueHeading ?? h.magHeading ?? 0);
        });
      } catch { /* optional */ }
    })();
    return () => { sub?.remove(); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) return;
      const result = await ensureCaptureSession(jobId, KIND);
      if (cancelled) return;
      if (!result.ok || !result.session) {
        setCaptureError(result.error ?? 'Could not start capture session');
        return;
      }
      setCaptureError(null);
      const s = result.session;
      setSession(s);
      setFrameCount(s.frameCount ?? 0);
      setSelectedRoomId(s.currentRoomId ?? s.rooms?.[0]?.id ?? null);
    })();
    return () => { cancelled = true; };
  }, [jobId]);

  const currentRoom = session?.rooms?.find(r => r.id === selectedRoomId)
    ?? session?.rooms?.[0];
  const roomFrames = currentRoom?.n ?? 0;
  const cue = CUES[Math.min(roomFrames, CUES.length - 1)];

  if (!permission) return <View style={styles.dark} />;
  if (!permission.granted) {
    return <CameraPermission onAllow={requestPermission} onClose={() => router.back()} body="GO uses the camera only while you photograph room corners for the floor plan." />;
  }

  const address = session?.address || 'Property';
  const roomLabel = currentRoom?.name || 'Room 1';

  const onShutter = async () => {
    if (shutterBusy.current || !jobId) return;
    shutterBusy.current = true;
    setShutterPressed(true);
    setCaptureError(null);
    try {
      const photo = await cameraRef.current?.takePictureAsync({ quality: 0.8 });
      if (!photo?.uri) {
        setCaptureError('Camera did not return a photo');
        return;
      }
      const yaw = headingRef.current;
      const idx = session?.rooms?.findIndex(r => r.id === (selectedRoomId ?? currentRoom?.id)) ?? stationIndex;
      const result = await uploadCaptureFrame(jobId, photo.uri, selectedRoomId ?? currentRoom?.id, KIND, {
        stationIndex: idx < 0 ? stationIndex : idx,
        yaw,
        heading: yaw,
        role: 'pan',
        contentType: 'image/jpeg',
      });
      if (!result.ok) {
        setCaptureError(result.error ?? 'Frame upload failed');
        return;
      }
      if (result.session) {
        setSession(result.session);
        if (result.session.currentRoomId) setSelectedRoomId(result.session.currentRoomId);
      }
      setFrameCount(result.frameCount ?? (c => c + 1));
    } catch (e: any) {
      setCaptureError(String(e?.message ?? e));
    } finally {
      shutterBusy.current = false;
      setShutterPressed(false);
    }
  };

  const onAddRoom = async () => {
    if (!jobId || addingRoom) return;
    setAddingRoom(true);
    setCaptureError(null);
    const result = await addCaptureRoom(jobId, newRoomName.trim(), KIND);
    setAddingRoom(false);
    if (!result.ok) {
      setCaptureError(result.error ?? 'Could not add room');
      return;
    }
    if (result.session) {
      setSession(result.session);
      setSelectedRoomId(result.session.currentRoomId ?? null);
    }
    setNewRoomName('');
  };

  return (
    <View style={styles.dark}>
      <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />
      <ScreenshotFeed />
      <HudScrims top={252} />

      <SafeAreaView style={{ flex: 1 }}>
        <HudTopBar title={address} subtitle={`Floor plan · ${roomLabel}`} onClose={() => router.back()} />

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.roomRow}>
          {(session?.rooms ?? []).map(r => {
            const on = r.id === selectedRoomId;
            return (
              <Pressable
                key={r.id ?? r.name}
                accessibilityRole="button"
                accessibilityState={{ selected: on }}
                style={[styles.roomChip, on && styles.roomChipOn]}
                onPress={() => setSelectedRoomId(r.id ?? null)}
              >
                <Text style={[T.callout, { color: on ? color.textInverse : color.text }]}>{r.name}</Text>
                <Text style={[T.dataSm, { color: on ? color.textInverse : color.textMuted }]}>{r.n}/{CORNER_TOTAL}</Text>
              </Pressable>
            );
          })}
        </ScrollView>

        <View style={styles.addRow}>
          <TextInput
            style={[T.body, styles.addInput]}
            placeholder="Next room: Kitchen, Bedroom…"
            placeholderTextColor={color.textFaint}
            value={newRoomName}
            onChangeText={setNewRoomName}
            returnKeyType="done"
            onSubmitEditing={onAddRoom}
            accessibilityLabel="Room name"
          />
          <HudIconButton icon={Plus} label="Add room" onPress={onAddRoom} active={newRoomName.trim().length > 0 && !addingRoom} />
        </View>

        <View style={styles.center}>
          <CornerBox captured={roomFrames} total={CORNER_TOTAL} />
          <HudReadout
            style={styles.readout}
            items={[
              { label: 'Corners', value: `${Math.min(roomFrames, CORNER_TOTAL)}/${CORNER_TOTAL}`, tone: roomFrames >= CORNER_TOTAL ? 'ok' : undefined },
              { label: 'Rooms', value: String(session?.rooms?.length ?? 0) },
              { label: 'Frames', value: String(frameCount) },
            ]}
          />
        </View>

        {captureError ? (
          <CoachCard tone="bad" title="Frame not saved" body={captureError} />
        ) : (
          <CoachCard step={cue.step} title={cue.title} body={cue.body} />
        )}

        <HudControls
          center={<Shutter mode="photo" busy={shutterPressed} onPress={onShutter} label="Photograph corner" />}
          right={<HudControl icon={Check} label="Review" onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId, kind: KIND } } as any)} />}
        />
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  dark: { flex: 1, backgroundColor: color.bg },
  roomRow: { paddingHorizontal: layout.gutter, paddingTop: space[12], gap: space[8], alignItems: 'center' },
  roomChip: {
    minHeight: layout.tap, flexDirection: 'row', alignItems: 'center', gap: space[8], paddingHorizontal: space[12],
    borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hairStrong, backgroundColor: hud.panel,
  },
  roomChipOn: { backgroundColor: color.inverse, borderColor: color.inverse },
  addRow: { flexDirection: 'row', gap: space[8], paddingHorizontal: layout.gutter, marginTop: space[8] },
  addInput: {
    flex: 1, height: layout.tap, borderWidth: hairline, borderColor: color.hairStrong, borderRadius: radius.sm,
    color: color.text, paddingHorizontal: space[12], backgroundColor: hud.panel,
  },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: space[16] },
  readout: {},
});
