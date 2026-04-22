import React from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { T } from '../../constants/Theme';
import { Over, Pill, I } from '../../components/bricks/Primitives';

export default function DispUploads() {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Upload</Text>
        <Text style={styles.subtitle}>Drop the card — we auto-sort by job.</Text>
      </View>

      <View style={styles.dragZone}>
        <I name="up" size={28} color={T.brick}/>
        <Text style={styles.dragTitle}>Drag files or tap to select</Text>
        <Text style={styles.dragMeta}>RAW, JPEG, MP4 · up to 20 GB per job</Text>
      </View>

      <ScrollView style={styles.list}>
        <Over color={T.moonMute}>RECENT UPLOADS</Over>
        {[
          ['30 Mercer Loop', '183 files · 3.1 GB', 'Editing', 'warn'],
          ['8 Riverbend',    '240 files · 5.2 GB', 'Delivered', 'ok'],
        ].map(([a, m, s, t], i) => (
          <View key={i} style={styles.item}>
            <I name="file" size={18} color={T.moonMute} />
            <View style={{ flex: 1 }}>
              <Text style={styles.itemTitle}>{a}</Text>
              <Text style={styles.itemMeta}>{m}</Text>
            </View>
            <Pill tone={t}>{s}</Pill>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.night },
  header: { padding: 22, paddingTop: 40 },
  title: { fontSize: 22, fontWeight: '500', color: T.moon },
  subtitle: { fontSize: 12, color: T.moonMute, marginTop: 4 },
  dragZone: { margin: 22, padding: 32, borderWidth: 1.5, borderColor: T.nightHairS, borderStyle: 'dashed', borderRadius: 2, alignItems: 'center' },
  dragTitle: { fontSize: 15, fontWeight: '500', color: T.moon, marginTop: 12 },
  dragMeta: { fontSize: 11, color: T.moonMute, marginTop: 6 },
  list: { padding: 22 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: T.nightHair },
  itemTitle: { fontSize: 13, fontWeight: '500', color: T.moon },
  itemMeta: { fontFamily: 'JetBrainsMono-Regular', fontSize: 11, color: T.moonMute, marginTop: 2 },
});
