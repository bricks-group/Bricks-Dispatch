import { useState, useEffect, useRef } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import Svg, { Circle, Line, Path } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { ensureCaptureSession, openCaptureStation, uploadCaptureFrame } from '../../lib/capture-api';
import type { CaptureKind, CaptureSession } from '../../lib/capture-api';

const DEFAULT_KIND: CaptureKind = 'splat';

const DOME_TOTAL = 24;

function CoverageDome({ captured, total, headingTick }: { captured: Set<number> | number; total: number; headingTick?: number }) {
  const R = 92, cx = 110, cy = 110;
  const onSet = captured instanceof Set ? captured : new Set(Array.from({ length: Math.min(Number(captured) || 0, total) }, (_, i) => i));
  const ticks = Array.from({ length: total }, (_, i) => {
    const ang = (i / total) * Math.PI * 2 - Math.PI / 2;
    const on = onSet.has(i);
    const aim = headingTick === i;
    const x1 = cx + Math.cos(ang) * (R - 12), y1 = cy + Math.sin(ang) * (R - 12);
    const x2 = cx + Math.cos(ang) * R, y2 = cy + Math.sin(ang) * R;
    return (
      <Line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={aim ? '#F4F1EB' : on ? COLORS.brick : '#F4F1EB30'} strokeWidth={aim || on ? 3 : 2} strokeLinecap="round" />
    );
  });
  return (
    <Svg width={220} height={220} viewBox="0 0 220 220">
      <Circle cx={cx} cy={cy} r={R - 22} fill="none" stroke="#F4F1EB1A" strokeWidth={1} />
      <Circle cx={cx} cy={cy} r={R + 6} fill="none" stroke="#F4F1EB12" strokeWidth={1} />
      {ticks}
      <Path d={`M${cx - 10} ${cy}H${cx + 10}M${cx} ${cy - 10}V${cy + 10}`} stroke="#F4F1EB55" strokeWidth={1} />
    </Svg>
  );
}

export default function CaptureScreen() {
  const router = useRouter();
  const { jobId, kind: kindParam } = useLocalSearchParams<{ jobId: string; kind?: string }>();
  const kind: CaptureKind = kindParam === 'floorplan' ? 'floorplan' : DEFAULT_KIND;
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const shutterBusy = useRef(false);

  const [session, setSession] = useState<CaptureSession | null>(null);
  const [frameCount, setFrameCount] = useState(0);
  const [shutterPressed, setShutterPressed] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [stationIndex, setStationIndex] = useState(0);
  const [heading0, setHeading0] = useState<number | null>(null);
  const [ticks, setTicks] = useState<Set<number>>(() => new Set());
  const [aimTick, setAimTick] = useState(0);
  const headingRef = useRef(0);

  useEffect(() => {
    if (kind === 'floorplan' && jobId) {
      router.replace({ pathname: '/(capture)/floorplan', params: { jobId, kind: 'floorplan' } } as any);
    }
  }, [kind, jobId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId || kind === 'floorplan') return;
      const s = await ensureCaptureSession(jobId, kind);
      if (!cancelled) {
        setSession(s);
        setFrameCount(s.frameCount ?? 0);
        setStationIndex(s.currentStationIndex ?? 0);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId, kind]);

  useEffect(() => {
    let sub: Location.LocationSubscription | undefined;
    (async () => {
      try {
        const perm = await Location.requestForegroundPermissionsAsync();
        if (perm.status !== 'granted') return;
        sub = await Location.watchHeadingAsync((h) => {
          const yaw = Number(h.trueHeading ?? h.magHeading ?? 0);
          headingRef.current = yaw;
          const origin = heading0 ?? yaw;
          const rel = ((yaw - origin) + 360) % 360;
          setAimTick(Math.round(rel / (360 / DOME_TOTAL)) % DOME_TOTAL);
        });
      } catch { /* heading optional; yaw still sent as 0 */ }
    })();
    return () => { sub?.remove(); };
  }, [heading0]);

  const currentRoom = session?.rooms?.find(r => r.name === session.guidance?.room)
    ?? session?.rooms?.[0];
  const capturedForDome = ticks.size;
  const pct = Math.round(Math.min(capturedForDome, DOME_TOTAL) / DOME_TOTAL * 100);

  if (!permission) return <View style={styles.dark} />;
  if (!permission.granted) {
    return (
      <SafeAreaView style={[styles.dark, styles.permWrap]}>
        <Text style={styles.permText}>Bricks needs camera access to photograph listings.</Text>
        <TouchableOpacity style={styles.permBtn} onPress={requestPermission}>
          <Text style={styles.permBtnText}>Allow camera</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  const address = session?.address || 'Property';
  const roomLabel = session?.guidance?.room
    ? session.guidance.room.toUpperCase()
    : (currentRoom?.name ? currentRoom.name.toUpperCase() : 'ROOM 1');

  const onShutter = async () => {
    if (shutterBusy.current || !jobId) return;
    shutterBusy.current = true;
    setShutterPressed(true);
    setCaptureError(null);
    try {
      const yaw = headingRef.current;
      if (heading0 == null) setHeading0(yaw);
      const origin = heading0 ?? yaw;
      const rel = ((yaw - origin) + 360) % 360;
      const tick = Math.round(rel / (360 / DOME_TOTAL)) % DOME_TOTAL;
      const photo = await cameraRef.current?.takePictureAsync({ quality: 0.8 });
      if (!photo?.uri) {
        setCaptureError('Camera did not return a photo');
        return;
      }
      const result = await uploadCaptureFrame(jobId, photo.uri, session?.currentRoomId, kind, {
        stationIndex,
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
        if (result.session.currentStationIndex != null) setStationIndex(result.session.currentStationIndex);
      }
      setFrameCount(result.frameCount ?? (c => c + 1));
      setTicks(prev => new Set(prev).add(tick));
    } catch (e: any) {
      setCaptureError(String(e?.message ?? e));
    } finally {
      shutterBusy.current = false;
      setShutterPressed(false);
    }
  };

  const onNewStation = async () => {
    if (!jobId) return;
    const result = await openCaptureStation(jobId, kind, {
      roomId: session?.currentRoomId,
      heading: headingRef.current,
      increment: true,
    });
    if (!result.ok) {
      setCaptureError(result.error ?? 'Could not open next station');
      return;
    }
    if (result.session) setSession(result.session);
    setStationIndex((result.session?.currentStationIndex ?? stationIndex) + 0);
    setTicks(new Set());
    setHeading0(null);
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
            <Text style={[styles.roomSub, roomSubColor(roomLabel)]}>{roomLabel}</Text>
          </View>
          <View style={styles.chip} />
        </View>

        <View style={styles.pillRow}>
          <Text style={styles.pill}>Station {stationIndex} · {ticks.size}/{DOME_TOTAL} · {frameCount} frames</Text>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <View>
            <CoverageDome captured={ticks} total={DOME_TOTAL} headingTick={aimTick} />
            <View style={styles.domeCenter} pointerEvents="none">
              <Text style={styles.domePct}>{pct}%</Text>
              <Text style={styles.domeLabel}>Station {stationIndex} sweep</Text>
            </View>
          </View>
        </View>

        <View style={styles.guidance}>
          <Text style={styles.guidanceTitle}>
            {session?.guidance ? `360 station · ${session.guidance.room}` : 'Stand still · pan slowly through 360'}
          </Text>
          <Text style={styles.guidanceBody}>
            {session?.guidance?.message ?? 'Fill the coverage dome for this station, then walk to the next. Server stitches the pan into a 360.'}
          </Text>
        </View>

        {captureError ? (
          <Text style={styles.errorText}>{captureError}</Text>
        ) : null}

        <View style={styles.shutterRow}>
          <TouchableOpacity style={styles.doneBtn} onPress={onNewStation}>
            <Text style={[styles.chipText, { fontSize: 11 }]}>+St</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.shutterOuter, shutterPressed && styles.shutterPressed]}
            onPress={onShutter}
            activeOpacity={0.8}
          >
            <View style={styles.shutterInner} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.doneBtn}
            onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId, kind } } as any)}
          >
            <Text style={styles.chipText}>✓</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </View>
  );
}

function roomSubColor(label: string): { color?: string } {
  if (label.includes('EXTERIOR')) return { color: COLORS.bad };
  if (label.includes('BATH')) return { color: COLORS.warn };
  return {};
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
  pillRow: { flexDirection: 'row', gap: 8, justifyContent: 'center', marginTop: 12 },
  pill: { fontFamily: FONTS.mono, fontSize: 10.5, color: '#F4F1EB', backgroundColor: '#14111099', borderWidth: 1, borderColor: '#F4F1EB1A', borderRadius: 2, paddingHorizontal: 10, paddingVertical: 5 },
  domeCenter: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  domePct: { fontFamily: FONTS.mono, fontSize: 30, color: '#F4F1EB' },
  domeLabel: { fontFamily: FONTS.mono, fontSize: 9, letterSpacing: 1.5, textTransform: 'uppercase', color: '#F4F1EBAA', marginTop: 2 },
  guidance: { marginHorizontal: 20, backgroundColor: COLORS.brick, borderRadius: 2, padding: 12 },
  guidanceTitle: { fontFamily: FONTS.bodySemibold, fontSize: 13, color: '#fff' },
  guidanceBody: { fontFamily: FONTS.body, fontSize: 11, color: '#FFFFFFCC', marginTop: 2 },
  errorText: { fontFamily: FONTS.body, fontSize: 11, color: '#F4F1EB', backgroundColor: COLORS.bad, marginHorizontal: 20, marginTop: 8, padding: 8, borderRadius: 2, textAlign: 'center' },
  shutterRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingVertical: 18 },
  shutterOuter: { width: 74, height: 74, borderRadius: 37, borderWidth: 3, borderColor: '#F4F1EB', alignItems: 'center', justifyContent: 'center' },
  shutterPressed: { transform: [{ scale: 0.92 }] },
  shutterInner: { width: 58, height: 58, borderRadius: 29, backgroundColor: COLORS.brick },
  doneBtn: { width: 44, height: 44, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
});
