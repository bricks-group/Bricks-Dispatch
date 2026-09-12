import { useState, useEffect, useRef } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, TextInput, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import Svg, { Rect, Line } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { addCaptureRoom, ensureCaptureSession, openCaptureStation, uploadCaptureFrame } from '../../lib/capture-api';
import type { CaptureSession } from '../../lib/capture-api';

const KIND = 'floorplan' as const;
const CORNER_TOTAL = 4;

const CUES = [
  { title: 'Corner 1 of 4', body: 'Stand near the middle. Point at one corner of this room and fire. Essentials floor plans need four corners — not a 360 dome.' },
  { title: 'Corner 2 of 4', body: 'Turn to the next corner. Keep the same spot on the floor so the server can pose the room.' },
  { title: 'Corner 3 of 4', body: 'Third corner. Include the doorway edge if you see one so rooms connect on the plan.' },
  { title: 'Corner 4 · next room', body: 'Last corner for this room. Add a room name, walk there, and shoot its four corners. Server compiles the plan.' },
];

function CornerBox({ captured, total }: { captured: number; total: number }) {
  const on = (i: number) => i < Math.min(captured, total);
  return (
    <Svg width={160} height={110} viewBox="0 0 160 110">
      <Rect x={18} y={12} width={124} height={86} fill="none" stroke="#F4F1EB22" strokeWidth={1.5} />
      <Line x1={18} y1={12} x2={36} y2={12} stroke={on(0) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(0) ? 4 : 2} />
      <Line x1={18} y1={12} x2={18} y2={30} stroke={on(0) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(0) ? 4 : 2} />
      <Line x1={142} y1={12} x2={124} y2={12} stroke={on(1) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(1) ? 4 : 2} />
      <Line x1={142} y1={12} x2={142} y2={30} stroke={on(1) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(1) ? 4 : 2} />
      <Line x1={142} y1={98} x2={124} y2={98} stroke={on(2) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(2) ? 4 : 2} />
      <Line x1={142} y1={98} x2={142} y2={80} stroke={on(2) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(2) ? 4 : 2} />
      <Line x1={18} y1={98} x2={36} y2={98} stroke={on(3) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(3) ? 4 : 2} />
      <Line x1={18} y1={98} x2={18} y2={80} stroke={on(3) ? COLORS.brick : '#F4F1EB40'} strokeWidth={on(3) ? 4 : 2} />
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
    return (
      <SafeAreaView style={[styles.dark, styles.permWrap]}>
        <Text style={styles.permText}>Bricks needs camera access to capture room corners for the floor plan.</Text>
        <TouchableOpacity style={styles.permBtn} onPress={requestPermission}>
          <Text style={styles.permBtnText}>Allow camera</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const address = session?.address || 'Property';
  const roomLabel = currentRoom?.name ? currentRoom.name.toUpperCase() : 'ROOM 1';

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
      <View style={styles.scrim} pointerEvents="none" />

      <SafeAreaView style={{ flex: 1 }}>
        <View style={styles.topBar}>
          <TouchableOpacity style={styles.chip} onPress={() => router.back()}>
            <Text style={styles.chipText}>✕</Text>
          </TouchableOpacity>
          <View style={{ alignItems: 'center' }}>
            <Text style={styles.roomTitle}>{address}</Text>
            <Text style={styles.roomSub}>FLOOR PLAN · {roomLabel}</Text>
          </View>
          <View style={styles.chip} />
        </View>

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.roomRow}>
          {(session?.rooms ?? []).map(r => (
            <TouchableOpacity
              key={r.id ?? r.name}
              style={[styles.roomChip, r.id === selectedRoomId && styles.roomChipOn]}
              onPress={() => setSelectedRoomId(r.id ?? null)}
            >
              <Text style={[styles.roomChipText, r.id === selectedRoomId && styles.roomChipTextOn]}>
                {r.name} · {r.n}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>

        <View style={styles.addRow}>
          <TextInput
            style={styles.addInput}
            placeholder="Kitchen, Bedroom…"
            placeholderTextColor="#F4F1EB55"
            value={newRoomName}
            onChangeText={setNewRoomName}
            returnKeyType="done"
            onSubmitEditing={onAddRoom}
          />
          <TouchableOpacity style={styles.addBtn} onPress={onAddRoom} disabled={addingRoom}>
            <Text style={styles.addBtnText}>{addingRoom ? '…' : '+ Room'}</Text>
          </TouchableOpacity>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <CornerBox captured={roomFrames} total={CORNER_TOTAL} />
          <Text style={styles.domePct}>{roomFrames} / {CORNER_TOTAL}</Text>
          <Text style={styles.domeLabel}>Corners this room</Text>
        </View>

        <View style={styles.guidance}>
          <Text style={styles.guidanceTitle}>{cue.title}</Text>
          <Text style={styles.guidanceBody}>{cue.body}</Text>
        </View>

        <Text style={styles.pill}>{frameCount} corner frames · compiled on server</Text>

        {captureError ? (
          <Text style={styles.errorText}>{captureError}</Text>
        ) : null}

        <View style={styles.shutterRow}>
          <View style={{ width: 56 }} />
          <TouchableOpacity
            style={[styles.shutterOuter, shutterPressed && styles.shutterPressed]}
            onPress={onShutter}
            activeOpacity={0.8}
          >
            <View style={styles.shutterInner} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.doneBtn}
            onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId, kind: KIND } } as any)}
          >
            <Text style={styles.chipText}>✓</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  dark: { flex: 1, backgroundColor: '#0C0B09' },
  scrim: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(12,11,9,0.35)' },
  permWrap: { alignItems: 'center', justifyContent: 'center', padding: 32, gap: 20 },
  permText: { fontFamily: FONTS.body, color: '#F4F1EB', textAlign: 'center', fontSize: 14 },
  permBtn: { backgroundColor: COLORS.brick, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 2 },
  permBtnText: { fontFamily: FONTS.bodySemibold, color: '#fff' },
  topBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 8 },
  chip: { width: 34, height: 34, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
  chipText: { color: '#F4F1EB', fontSize: 16 },
  roomTitle: { fontFamily: FONTS.bodySemibold, fontSize: 13, color: '#F4F1EB' },
  roomSub: { fontFamily: FONTS.mono, fontSize: 10, letterSpacing: 1.4, color: COLORS.brick, marginTop: 1 },
  roomRow: { paddingHorizontal: 16, paddingTop: 10, gap: 8, alignItems: 'center' },
  roomChip: { borderWidth: 1, borderColor: '#F4F1EB30', borderRadius: 2, paddingHorizontal: 10, paddingVertical: 6, marginRight: 8 },
  roomChipOn: { borderColor: COLORS.brick, backgroundColor: '#8C2F2433' },
  roomChipText: { fontFamily: FONTS.mono, fontSize: 10, color: '#F4F1EBAA' },
  roomChipTextOn: { color: '#F4F1EB' },
  addRow: { flexDirection: 'row', gap: 8, paddingHorizontal: 16, marginTop: 8 },
  addInput: {
    flex: 1, height: 36, borderWidth: 1, borderColor: '#F4F1EB22', borderRadius: 2,
    color: '#F4F1EB', paddingHorizontal: 10, fontFamily: FONTS.body, fontSize: 13, backgroundColor: '#14111099',
  },
  addBtn: { height: 36, paddingHorizontal: 12, borderRadius: 2, backgroundColor: COLORS.brick, alignItems: 'center', justifyContent: 'center' },
  addBtnText: { fontFamily: FONTS.bodySemibold, fontSize: 12, color: '#fff' },
  domePct: { fontFamily: FONTS.mono, fontSize: 22, color: '#F4F1EB', marginTop: 4 },
  domeLabel: { fontFamily: FONTS.mono, fontSize: 9, letterSpacing: 1.5, textTransform: 'uppercase', color: '#F4F1EBAA', marginTop: 2 },
  guidance: { marginHorizontal: 20, backgroundColor: COLORS.brick, borderRadius: 2, padding: 12 },
  guidanceTitle: { fontFamily: FONTS.bodySemibold, fontSize: 13, color: '#fff' },
  guidanceBody: { fontFamily: FONTS.body, fontSize: 11, color: '#FFFFFFCC', marginTop: 2 },
  pill: { fontFamily: FONTS.mono, fontSize: 10.5, color: '#F4F1EB', textAlign: 'center', marginTop: 8 },
  errorText: { fontFamily: FONTS.body, fontSize: 11, color: '#F4F1EB', backgroundColor: COLORS.bad, marginHorizontal: 20, marginTop: 8, padding: 8, borderRadius: 2, textAlign: 'center' },
  shutterRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingVertical: 18 },
  shutterOuter: { width: 74, height: 74, borderRadius: 37, borderWidth: 3, borderColor: '#F4F1EB', alignItems: 'center', justifyContent: 'center' },
  shutterPressed: { transform: [{ scale: 0.92 }] },
  shutterInner: { width: 58, height: 58, borderRadius: 29, backgroundColor: COLORS.brick },
  doneBtn: { width: 44, height: 44, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
});
