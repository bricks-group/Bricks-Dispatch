import { useState, useEffect, useRef } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import Svg, { Circle, Line, Path } from 'react-native-svg';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { getCaptureSession, bumpFrameCount } from '../../lib/capture-api';
import type { CaptureSession } from '../../lib/capture-api';

// ── Coverage dome — unchanged from current capture.tsx ──────────────────────
// Ring of ticks, captured ones lit terracotta, rest faint.
// Maps to real session data when the Supabase tables exist; static until then.

function CoverageDome({ captured, total }: { captured: number; total: number }) {
  const R = 92, cx = 110, cy = 110;
  const ticks = Array.from({ length: total }, (_, i) => {
    const ang = (i / total) * Math.PI * 2 - Math.PI / 2;
    const on = i < captured;
    const x1 = cx + Math.cos(ang) * (R - 12), y1 = cy + Math.sin(ang) * (R - 12);
    const x2 = cx + Math.cos(ang) * R, y2 = cy + Math.sin(ang) * R;
    return (
      <Line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={on ? COLORS.brick : '#F4F1EB30'} strokeWidth={on ? 3 : 2} strokeLinecap="round" />
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

// ── Screen ────────────────────────────────────────────────────────────────────

export default function CaptureScreen() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  // Session state — wired to capture-api (falls back to static shape if tables don't exist yet).
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [frameCount, setFrameCount] = useState(0);
  const [shutterPressed, setShutterPressed] = useState(false);

  // Load session data when jobId is available.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) { setLoading(false); return; }
      const s = await getCaptureSession(jobId);
      if (!cancelled) {
        setSession(s);
        setFrameCount(s?.frameCount ?? 0);
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [jobId]);

  const sessionData = session ?? null;
  const pct = sessionData ? Math.round(sessionData.rooms.reduce((s, r) => s + r.n, 0) / sessionData.rooms.length / 24 * 100) : 0;

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

  // Static fallback shape when session hasn't loaded yet.
  const address = sessionData?.address ?? '214 Linden St';
  const roomLabel = sessionData?.guidance?.room ?? 'ROOM 6 · KITCHEN';
  const frameCountDisplay = frameCount > 0 ? frameCount : (sessionData?.frameCount ?? 184);

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
          <Text style={styles.pill}>{frameCountDisplay} frames</Text>
          <Text style={styles.pill}>02:41</Text>
          <Text style={styles.pill}>Sharp · 98%</Text>
        </View>

        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <View>
            <CoverageDome captured={sessionData ? Math.round(sessionData.rooms.reduce((s, r) => s + r.n, 0) / 24) : 17} total={24} />
            <View style={styles.domeCenter} pointerEvents="none">
              <Text style={styles.domePct}>{pct}%</Text>
              <Text style={styles.domeLabel}>Angles covered</Text>
            </View>
          </View>
        </View>

        <View style={styles.guidance}>
          <Text style={styles.guidanceTitle}>
            {sessionData?.guidance ? `Pan to ${sessionData.guidance.room}` : 'Pan slowly left'}
          </Text>
          <Text style={styles.guidanceBody}>
            {sessionData?.guidance?.message ?? 'Keep the far counter in frame — fill the gap by the window.'}
          </Text>
        </View>

        <View style={styles.shutterRow}>
          <View style={{ width: 56 }} />
          <TouchableOpacity
            style={[styles.shutterOuter, shutterPressed && styles.shutterPressed]}
            onPress={() => {
              setShutterPressed(true);
              setFrameCount(c => c + 1);
              if (jobId) bumpFrameCount(jobId).then(() => setShutterPressed(false));
              setTimeout(() => setShutterPressed(false), 200);
            }}
            activeOpacity={0.8}
          >
            <View style={styles.shutterInner} />
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.doneBtn}
            onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId } } as any)}
          >
            <Text style={styles.chipText}>✓</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    </View>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Color-code the room sub-label by coverage tone. */
function roomSubColor(label: string): { color?: string } {
  if (label.includes('EXTERIOR')) return { color: COLORS.bad };
  if (label.includes('BATH')) return { color: COLORS.warn };
  return {};
}

// ── Styles ─────────────────────────────────────────────────────────────────────

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
  shutterRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingVertical: 18 },
  shutterOuter: { width: 74, height: 74, borderRadius: 37, borderWidth: 3, borderColor: '#F4F1EB', alignItems: 'center', justifyContent: 'center' },
  shutterPressed: { transform: [{ scale: 0.92 }] },
  shutterInner: { width: 58, height: 58, borderRadius: 29, backgroundColor: COLORS.brick },
  doneBtn: { width: 44, height: 44, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
});
