import { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Box, LayoutGrid, Video, Camera } from 'lucide-react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { color, type as T, space, layout } from '../../theme';
import { NavBar, Group, Row, EmptyState, LoadingState } from '../../components/ui';
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
        <NavBar title="Capture" close onBack={() => router.back()} border={false} />
        <LoadingState />
      </SafeAreaView>
    );
  }

  const none = !flags.tour && !flags.floorplan && !flags.visit;

  return (
    <SafeAreaView style={styles.safe}>
      <NavBar title="Capture" close onBack={() => router.back()} border={false} />
      <View style={styles.body}>
        {none ? (
          <EmptyState icon={Camera} title="Nothing to capture in GO" body={captureNeitherCopy(flags)} />
        ) : (
          <>
            <Text style={[T.display, { color: color.text }]} accessibilityRole="header">What are you capturing?</Text>
            <Text style={[T.body, styles.sub]}>Pick what this job purchased. HDR stills stay on your camera.</Text>
            <Group>
              {flags.tour ? (
                <Row icon={Box} title="360° tour" subtitle="Stand at stations and pan a full circle in each room." chevron onPress={() => go('splat')} />
              ) : null}
              {flags.floorplan ? (
                <Row icon={LayoutGrid} title="Floor plan" subtitle="Photograph each room's corners, walls and openings." chevron onPress={() => go('floorplan')} />
              ) : null}
              {flags.visit ? (
                <Row icon={Video} title="Virtual visit" subtitle="Walk-through video, one per floor. Free-roam 3D." chevron onPress={() => go('visit')} />
              ) : null}
            </Group>
          </>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: color.bg },
  body: { paddingHorizontal: layout.gutter, paddingTop: space[16] },
  sub: { color: color.textMuted, marginTop: space[8], marginBottom: space[28] },
});
