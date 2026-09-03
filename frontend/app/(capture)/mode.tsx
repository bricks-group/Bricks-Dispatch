import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';

export default function CaptureModeScreen() {
  const router = useRouter();
  const { jobId } = useLocalSearchParams<{ jobId: string }>();

  const go = (kind: 'splat' | 'floorplan') => {
    const pathname = kind === 'floorplan' ? '/(capture)/floorplan' : '/(capture)/capture';
    router.replace({ pathname, params: { jobId, kind } } as any);
  };

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
          Same job can have both. Professional HDR stills stay on the camera — not here.
        </Text>

        <TouchableOpacity style={styles.card} onPress={() => go('splat')} activeOpacity={0.85}>
          <Text style={styles.cardKicker}>3D TOUR</Text>
          <Text style={styles.cardTitle}>Gaussian splat frames</Text>
          <Text style={styles.cardBody}>
            Pan slowly around each room. Owned tour reconstruction — not stills, not Polycam unless we fall back later.
          </Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.card} onPress={() => go('floorplan')} activeOpacity={0.85}>
          <Text style={styles.cardKicker}>FLOOR PLAN</Text>
          <Text style={styles.cardTitle}>Room-by-room corners</Text>
          <Text style={styles.cardBody}>
            Walk each room. Photograph corners, walls, and openings for tracing. Not HDR stills. Not BoxBrownie.
          </Text>
        </TouchableOpacity>
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
