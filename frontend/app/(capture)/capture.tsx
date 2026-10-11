import { useState, useEffect, useRef } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import Svg, { Circle, Line, Path } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { color, type as T, space, radius, hairline } from '../../theme';
import { Plus, Check } from 'lucide-react-native';
import { hud, HudScrims, HudTopBar, HudReadout, CoachCard, Shutter, HudControl, HudControls, CameraPermission, ScreenshotFeed } from '../../components/capture-hud';
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
        stroke={aim ? color.text : on ? color.accent : color.textFaint} strokeWidth={aim || on ? 3 : 2} strokeLinecap="butt" />
    );
  });
  return (
    <Svg width={220} height={220} viewBox="0 0 220 220">
      <Circle cx={cx} cy={cy} r={R - 22} fill="none" stroke={color.hairStrong} strokeWidth={1} />
      <Circle cx={cx} cy={cy} r={R + 6} fill="none" stroke={color.hairStrong} strokeWidth={1} />
      {ticks}
      <Path d={`M${cx - 10} ${cy}H${cx + 10}M${cx} ${cy - 10}V${cy + 10}`} stroke={color.textMuted} strokeWidth={1} />
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
      const result = await ensureCaptureSession(jobId, kind);
      if (cancelled) return;
      if (!result.ok || !result.session) {
        setCaptureError(result.error ?? 'Could not start capture session');
        return;
      }
      setCaptureError(null);
      const s = result.session;
      setSession(s);
      setFrameCount(s.frameCount ?? 0);
      setStationIndex(s.currentStationIndex ?? 0);
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
    return <CameraPermission onAllow={requestPermission} onClose={() => router.back()} body="GO uses the camera only while you shoot this listing's 360 stations." />;
  }

  const address = session?.address || 'Property';
  const roomLabel = session?.guidance?.room || currentRoom?.name || 'Room 1';

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

  const station = String(stationIndex + 1).padStart(2, '0');

  return (
    <View style={styles.dark}>
      <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" />
      <ScreenshotFeed />
      <HudScrims />

      <SafeAreaView style={{ flex: 1 }}>
        <HudTopBar title={address} subtitle={`360° tour · ${roomLabel}`} onClose={() => router.back()} />

        <HudReadout
          style={styles.readout}
          items={[
            { label: 'Station', value: station },
            { label: 'Sweep', value: `${ticks.size}/${DOME_TOTAL}`, tone: ticks.size >= DOME_TOTAL ? 'ok' : undefined },
            { label: 'Frames', value: String(frameCount) },
          ]}
        />

        <View style={styles.center}>
          <View style={styles.domePanel}>
            <CoverageDome captured={ticks} total={DOME_TOTAL} headingTick={aimTick} />
            <View style={styles.domeCenter} pointerEvents="none">
              <Text style={[T.kpi, { color: color.text }]}>{pct}%</Text>
            </View>
          </View>
        </View>

        {captureError ? (
          <CoachCard tone="bad" title="Frame not saved" body={captureError} style={styles.coach} />
        ) : (
          <CoachCard
            step={`360 station ${station}`}
            title={session?.guidance?.room ? `Pan slowly · ${session.guidance.room}` : 'Stand still, pan slowly'}
            body={session?.guidance?.message ?? 'Turn on the spot until the ring is full, then walk to the next station.'}
            style={styles.coach}
          />
        )}

        <HudControls
          left={<HudControl icon={Plus} label="Station" onPress={onNewStation} />}
          center={<Shutter mode="photo" busy={shutterPressed} onPress={onShutter} label="Take 360 frame" />}
          right={<HudControl icon={Check} label="Review" onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId, kind } } as any)} />}
        />
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  dark: { flex: 1, backgroundColor: color.bg },
  readout: { marginTop: space[12] },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  domePanel: { padding: space[8], backgroundColor: hud.panel, borderRadius: radius.sm, borderWidth: hairline, borderColor: color.hair },
  domeCenter: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  coach: { marginTop: space[8] },
});
