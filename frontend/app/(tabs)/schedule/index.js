import React from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { T } from '../../constants/Theme';
import { Over, Pill, I } from '../../components/bricks/Primitives';

const weekJobs = [
  { t: '09:00', a: '1902 Pine Rd', ty: 'House', s: 'Delivered', tone: 'ok' },
  { t: '14:30', a: '214 Linden St', ty: 'House', s: 'In progress', tone: 'brick' },
  { t: '16:00', a: '30 Mercer Loop', ty: 'Comm.', s: 'Scheduled', tone: 'neutral' },
];

export default function DispSchedule() {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Week 16</Text>
        <View style={styles.nav}>
          <View style={styles.navBtn}><I name="arrL" size={13} color={T.moonMute} style={{transform:[{rotate:'90deg'}]}}/></View>
          <View style={styles.navBtn}><I name="arrR" size={13} color={T.moonMute} style={{transform:[{rotate:'90deg'}]}}/></View>
        </View>
      </View>
      
      <View style={styles.calendar}>
        {['M','T','W','T','F','S','S'].map((d, i) => (
          <View key={i} style={[styles.day, i===3 && styles.dayActive]}>
            <Over style={{ opacity: 0.6 }}>{d}</Over>
            <Text style={styles.dayNum}>{13+i}</Text>
          </View>
        ))}
      </View>

      <ScrollView style={styles.list}>
        <Over color={T.moonMute}>THURSDAY, APR 18 · 4 SHOOTS</Over>
        {weekJobs.map((j, i) => (
          <View key={i} style={styles.jobItem}>
            <Text style={styles.time}>{j.t}</Text>
            <View style={styles.line}><View style={styles.dot} /></View>
            <View style={styles.info}>
              <Text style={styles.addr}>{j.a}</Text>
              <Text style={styles.type}>{j.ty}</Text>
              <Pill tone={j.tone}>{j.s}</Pill>
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.night },
  header: { padding: 22, paddingTop: 40, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: 22, fontWeight: '500', color: T.moon },
  nav: { flexDirection: 'row', gap: 4 },
  navBtn: { padding: 8, borderWidth: 1, borderColor: T.nightHair, borderRadius: 2 },
  calendar: { flexDirection: 'row', paddingHorizontal: 22, gap: 4 },
  day: { flex: 1, paddingVertical: 8, alignItems: 'center', borderWidth: 1, borderColor: T.nightHair, borderRadius: 2 },
  dayActive: { backgroundColor: T.moon, borderColor: T.moon },
  dayNum: { fontFamily: 'JetBrainsMono-Regular', fontSize: 14, color: T.moon, marginTop: 4 },
  list: { padding: 22 },
  jobItem: { flexDirection: 'row', gap: 14, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: T.nightHair },
  time: { fontFamily: 'JetBrainsMono-Regular', fontSize: 12, color: T.moonMute, width: 44 },
  line: { width: 1, backgroundColor: T.nightHair, position: 'relative' },
  dot: { position: 'absolute', left: -3, top: 4, width: 7, height: 7, borderRadius: 99, backgroundColor: T.brick },
  info: { flex: 1, gap: 4 },
  addr: { fontSize: 14, fontWeight: '500', color: T.moon },
  type: { fontSize: 11, color: T.moonMute }
});
