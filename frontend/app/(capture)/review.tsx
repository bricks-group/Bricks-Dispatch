import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { COLORS, FONTS } from '../../constants/CaptureTheme';

// Static placeholder data — no real submit-to-pipeline endpoint exists yet.
// See README "Capture flow — what's not built."
const ROOMS = [
  { name: 'Living', tone: 'ok', n: 212 }, { name: 'Kitchen', tone: 'ok', n: 188 },
  { name: 'Dining', tone: 'ok', n: 96 }, { name: 'Primary', tone: 'ok', n: 174 },
  { name: 'Bath 1', tone: 'warn', n: 54 }, { name: 'Bath 2', tone: 'warn', n: 41 },
  { name: 'Bed 2', tone: 'ok', n: 120 }, { name: 'Exterior', tone: 'bad', n: 28 },
];
const PIPELINE = [
  { title: 'Captured', detail: '1,204 frames · 8 rooms', status: 'done' },
  { title: 'Splatting', detail: 'Gaussian reconstruction · ~14 min', status: 'active' },
  { title: 'QC pass', detail: 'Auto + human review', status: 'todo' },
  { title: 'Delivered', detail: 'Tour + stills to agent', status: 'todo' },
];
const TONE_COLOR: Record<string, string> = { ok: COLORS.ok, warn: COLORS.warn, bad: COLORS.bad };

export default function ReviewScreen() {
  const router = useRouter();

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <ScrollView>
        <View style={styles.hero}>
          <TouchableOpacity style={styles.backChip} onPress={() => router.back()}>
            <Text style={styles.backChipText}>‹</Text>
          </TouchableOpacity>
          <Text style={styles.heroAddress}>214 Linden St</Text>
          <Text style={styles.heroMeta}>HOUSE · 2,140 SQFT · 1,204 FRAMES</Text>
        </View>

        <View style={styles.body}>
          <View style={styles.sectionHead}>
            <Text style={styles.sectionLabel}>Coverage by room</Text>
            <Text style={styles.sectionNote}>6 / 8 strong</Text>
          </View>
          <View style={styles.roomGrid}>
            {ROOMS.map(r => (
              <View key={r.name} style={styles.roomCell}>
                <View style={[styles.dot, { backgroundColor: TONE_COLOR[r.tone] }]} />
                <Text style={styles.roomName}>{r.name}</Text>
                <Text style={styles.roomCount}>{r.n}</Text>
              </View>
            ))}
          </View>
          <View style={styles.warnBanner}>
            <Text style={styles.warnText}>Exterior coverage is thin — add 20+ frames before submitting.</Text>
          </View>

          <Text style={[styles.sectionLabel, { marginTop: 24 }]}>Pipeline</Text>
          <View style={{ marginTop: 12 }}>
            {PIPELINE.map((p, i) => {
              const done = p.status === 'done', active = p.status === 'active';
              return (
                <View key={p.title} style={styles.pipeRow}>
                  <View style={{ alignItems: 'center', width: 22 }}>
                    <View style={[styles.pipeDot, done && { backgroundColor: COLORS.ok }, active && { backgroundColor: COLORS.brick }]} />
                    {i !== PIPELINE.length - 1 && <View style={[styles.pipeLine, done && { backgroundColor: COLORS.ok }]} />}
                  </View>
                  <View style={{ flex: 1, marginLeft: 12 }}>
                    <Text style={[styles.pipeTitle, p.status === 'todo' && { color: COLORS.ink3 }]}>{p.title}</Text>
                    <Text style={styles.pipeDetail}>{p.detail}</Text>
                  </View>
                  {active && <Text style={styles.runningBadge}>Running</Text>}
                </View>
              );
            })}
          </View>
        </View>
      </ScrollView>

      <View style={styles.submitBar}>
        <View>
          <Text style={styles.submitLabel}>Est. delivery</Text>
          <Text style={styles.submitEta}>~ 22 hrs</Text>
        </View>
        <TouchableOpacity style={styles.submitBtn} onPress={() => router.back()}>
          <Text style={styles.submitBtnText}>Submit to pipeline →</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.paper },
  hero: { height: 200, backgroundColor: '#0C0B09', justifyContent: 'flex-end', padding: 18 },
  backChip: { position: 'absolute', top: 14, left: 18, width: 32, height: 32, borderRadius: 2, borderWidth: 1, borderColor: '#F4F1EB30', alignItems: 'center', justifyContent: 'center' },
  backChipText: { color: '#F4F1EB', fontSize: 18 },
  heroAddress: { fontFamily: FONTS.bodySemibold, fontSize: 19, color: '#F4F1EB' },
  heroMeta: { fontFamily: FONTS.mono, fontSize: 11, color: '#F4F1EBBB', marginTop: 2, letterSpacing: 0.5 },
  body: { padding: 22 },
  sectionHead: { flexDirection: 'row', justifyContent: 'space-between' },
  sectionLabel: { fontFamily: FONTS.mono, fontSize: 10, letterSpacing: 2, textTransform: 'uppercase', color: COLORS.ink3 },
  sectionNote: { fontFamily: FONTS.mono, fontSize: 11, color: COLORS.ink3 },
  roomGrid: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 12, borderWidth: 1, borderColor: COLORS.hair, borderRadius: 2 },
  roomCell: { width: '25%', paddingVertical: 12, alignItems: 'center', borderWidth: 0.5, borderColor: COLORS.hair },
  dot: { width: 8, height: 8, borderRadius: 4, marginBottom: 7 },
  roomName: { fontFamily: FONTS.bodySemibold, fontSize: 11.5, textAlign: 'center' },
  roomCount: { fontFamily: FONTS.mono, fontSize: 10, color: COLORS.ink3, marginTop: 3 },
  warnBanner: { marginTop: 10, flexDirection: 'row', alignItems: 'center', padding: 10, backgroundColor: '#8C2F2418', borderRadius: 2 },
  warnText: { fontFamily: FONTS.body, fontSize: 11.5, color: COLORS.ink2, flex: 1 },
  pipeRow: { flexDirection: 'row', paddingBottom: 16 },
  pipeDot: { width: 22, height: 22, borderRadius: 11, borderWidth: 1.5, borderColor: COLORS.ink3 },
  pipeLine: { width: 1.5, flex: 1, minHeight: 22, backgroundColor: COLORS.ink3, marginTop: 2 },
  pipeTitle: { fontFamily: FONTS.bodySemibold, fontSize: 14, color: COLORS.ink },
  pipeDetail: { fontFamily: FONTS.body, fontSize: 11.5, color: COLORS.ink3, marginTop: 2 },
  runningBadge: { fontFamily: FONTS.mono, fontSize: 9.5, color: COLORS.brick, alignSelf: 'flex-start' },
  submitBar: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 18, borderTopWidth: 1, borderTopColor: COLORS.hair, backgroundColor: COLORS.paper },
  submitLabel: { fontFamily: FONTS.mono, fontSize: 9.5, letterSpacing: 1.5, textTransform: 'uppercase', color: COLORS.ink3 },
  submitEta: { fontFamily: FONTS.mono, fontSize: 17, color: COLORS.ink, marginTop: 2 },
  submitBtn: { marginLeft: 'auto', backgroundColor: COLORS.brick, borderRadius: 2, paddingHorizontal: 20, paddingVertical: 13 },
  submitBtnText: { fontFamily: FONTS.bodySemibold, fontSize: 14, color: '#fff' },
});
