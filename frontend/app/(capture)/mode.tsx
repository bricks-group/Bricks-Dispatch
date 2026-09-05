import { useEffect, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';
import { actorHeaders } from '../../lib/api';
import { captureNeitherCopy, jobCaptureModes } from '../../lib/capture-modes';
import type { CaptureFlags } from '../../lib/capture-modes';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function CaptureModeScreen() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();
  const [flags, setFlags] = useState<CaptureFlags | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!jobId) {
        setFlags({ tour: false, floorplan: false, visit: false });
        return;
      }
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/vendor-os/jobs/${jobId}`, { headers });
        const job = res.ok ? await res.json() : {};
        if (cancelled) return;
        const next = jobCaptureModes(job);
        const modes = [next.tour, next.floorplan, next.visit].filter(Boolean).length;
        if (modes === 1) {
          if (next.tour) {
            router.replace({ pathname: '/(capture)/capture', params: { jobId, kind: 'splat' } } as any);
            return;
          }
          if (next.floorplan) {
            router.replace({ pathname: '/(capture)/floorplan', params: { jobId, kind: 'floorplan' } } as any);
            return;
          }
          if (next.visit) {
            router.replace({ pathname: '/(capture)/visit', params: { jobId } } as any);
            return;
          }
        }
        setFlags(next);
      } catch {
        if (!cancelled) setFlags({ tour: false, floorplan: false, visit: false });
      }
    })();
    return () => { cancelled = true; };
  }, [jobId]);

  const go = (kind: 'splat' | 'floorplan' | 'visit') => {
    if (kind === 'visit') {
      router.replace({ pathname: '/(capture)/visit', params: { jobId } } as any);
      return;
    }
    const pathname = kind === 'floorplan' ? '/(capture)/floorplan' : '/(capture)/capture';
    router.replace({ pathname, params: { jobId, kind } } as any);
  };

  if (!flags) {
    return (
      <SafeAreaView style={styles.safe}>
        <ActivityIndicator size="large" color={COLORS.brick} style={{ flex: 1 }} />
      </SafeAreaView>
    );
  }

  if (!flags.tour && !flags.floorplan && !flags.visit) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.top}>
          <TouchableOpacity style={styles.chip} onPress={() => router.back()}>
            <Text style={styles.chipText}>✕</Text>
          </TouchableOpacity>
          <Text style={styles.kicker}>CAPTURE</Text>
          <View style={styles.chip} />
        </View>
        <View style={styles.body}>
          <Text style={styles.title}>Nothing to capture in GO</Text>
          <Text style={styles.sub}>{captureNeitherCopy(flags)}</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.top}>
        <TouchableOpacity style={styles.chip} onPress={() => router.back()}>
          <Text style={styles.chipText}>✕</Text>
        </TouchableOpacity>
        <Text style={styles.kicker}>CAPTURE</Text>
        <View style={styles.chip} />
      </View>

      <View style={styles.body}>
        <Text style={styles.title}>What are you capturing?</Text>
        <Text style={styles.sub}>
          Pick what this job purchased. Professional HDR stills stay on the camera — not here.
        </Text>

        {flags.tour ? (
        <TouchableOpacity style={styles.card} onPress={() => go('splat')} activeOpacity={0.85}>
          <Text style={styles.cardKicker}>3D TOUR</Text>
          <Text style={styles.cardTitle}>Path-confined walkthrough</Text>
          <Text style={styles.cardBody}>
            Pan slowly around each room. This is the virtual tour you stay on the path for — not a free-roam virtual visit.
          </Text>
        </TouchableOpacity>
        ) : null}

        {flags.floorplan ? (
        <TouchableOpacity style={styles.card} onPress={() => go('floorplan')} activeOpacity={0.85}>
          <Text style={styles.cardKicker}>FLOOR PLAN</Text>
          <Text style={styles.cardTitle}>Room-by-room corners</Text>
          <Text style={styles.cardBody}>
            Walk each room. Photograph corners, walls, and openings for tracing. Not HDR stills. Not BoxBrownie.
          </Text>
        </TouchableOpacity>
        ) : null}

        {flags.visit ? (
        <TouchableOpacity style={styles.card} onPress={() => go('visit')} activeOpacity={0.85}>
          <Text style={styles.cardKicker}>VIRTUAL VISIT</Text>
          <Text style={styles.cardTitle}>Free-roam splat</Text>
          <Text style={styles.cardBody}>
            Paste a Polycam share URL (or upload .ply/.splat on GO web). Not the path-confined tour.
          </Text>
        </TouchableOpacity>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#0C0B09' },
  top: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 8 },
  chip: { width: 34, height: 34, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
  chipText: { color: '#F4F1EB', fontSize: 16 },
  kicker: { fontFamily: FONTS.mono, fontSize: 11, letterSpacing: 2, color: COLORS.brick },
  body: { padding: 22, paddingTop: 36, gap: 16 },
  title: { fontFamily: FONTS.bodySemibold, fontSize: 24, color: '#F4F1EB' },
  sub: { fontFamily: FONTS.body, fontSize: 13, color: '#F4F1EBAA', marginBottom: 8, lineHeight: 18 },
  card: { borderWidth: 1, borderColor: '#F4F1EB22', borderRadius: 2, padding: 18, backgroundColor: '#141110' },
  cardKicker: { fontFamily: FONTS.mono, fontSize: 10, letterSpacing: 1.8, color: COLORS.brick, marginBottom: 6 },
  cardTitle: { fontFamily: FONTS.bodySemibold, fontSize: 17, color: '#F4F1EB' },
  cardBody: { fontFamily: FONTS.body, fontSize: 12.5, color: '#F4F1EB99', marginTop: 6, lineHeight: 18 },
});
