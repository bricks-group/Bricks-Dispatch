import React from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { T } from '../../constants/Theme';
import { Over, Pill, I } from '../components/bricks/Primitives';

// Mock data — will be wired to backend
const job = {
  id: '08412',
  address: '214 Linden St',
  details: 'Brooklyn, NY · House · 2,140 sqft',
  arrive: 'Today · 14:30',
  package: 'Signature',
  drive: '22 min · 9.4 mi',
  payout: '$312',
  agent: { name: 'M. Ortega', role: 'Harbor Realty · Agent' },
  lockbox: '4812#',
  notes: 'Park on Linden. Dog in back yard. Agent on site 14:15.',
  checklist: [
    '35 interior photos',
    '5 drone aerials',
    '60-second walkthrough reel',
    'Floor plan — each level'
  ]
};

export default function DispJobDetail() {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <TouchableOpacity><I name="arrL" size={18} /></TouchableOpacity>
        <Text style={styles.headerId}>#{job.id}</Text>
        <TouchableOpacity><I name="dots" size={18} /></TouchableOpacity>
      </View>

      <ScrollView>
        {/* Placeholder for hero image */}
        <View style={styles.hero}><Text style={styles.heroText}>214 LINDEN ST · STREET VIEW</Text></View>

        <View style={styles.content}>
          <View style={styles.row}>
            <View>
              <Text style={styles.title}>{job.address}</Text>
              <Text style={styles.subtitle}>{job.details}</Text>
            </View>
            <Pill tone="brick">Next up</Pill>
          </View>

          <View style={styles.stats}>
            {[
              ['ARRIVE', job.arrive],
              ['PACKAGE', job.package],
              ['DRIVE', job.drive],
              ['PAYOUT', job.payout],
            ].map(([l, v]) => (
              <View key={l} style={styles.statBox}>
                <Over style={styles.statLabel}>{l}</Over>
                <Text style={styles.statValue}>{v}</Text>
              </View>
            ))}
          </View>

          <View style={styles.section}>
            <Over color={T.moonMute}>CONTACT</Over>
            <View style={styles.contact}>
              <View style={styles.avatar}><Text style={styles.avatarText}>MO</Text></View>
              <View style={{ flex: 1 }}>
                <Text style={styles.contactName}>{job.agent.name}</Text>
                <Text style={styles.contactRole}>{job.agent.role}</Text>
              </View>
              <I name="phone" size={17} />
            </View>
          </View>

          <View style={styles.section}>
            <Over color={T.moonMute}>ACCESS</Over>
            <View style={styles.access}>
              <View style={styles.row}>
                <Text style={styles.accessLabel}>Lockbox code</Text>
                <Text style={styles.accessValue}>{job.lockbox}</Text>
              </View>
              <View style={styles.divider} />
              <Text style={styles.accessNotes}>{job.notes}</Text>
            </View>
          </View>

          <View style={styles.section}>
            <Over color={T.moonMute}>SHOT LIST</Over>
            {job.checklist.map((s, i) => (
              <View key={i} style={styles.shotItem}>
                <I name="cam" size={15} color={T.moonMute} />
                <Text style={styles.shotLabel}>{s}</Text>
              </View>
            ))}
          </View>
          <View style={{ height: 100 }} />
        </View>
      </ScrollView>

      <View style={styles.footer}>
        <TouchableOpacity style={styles.btnGhost}><I name="phone" size={17} /></TouchableOpacity>
        <TouchableOpacity style={styles.btnAccent}><Text style={styles.btnAccentText}>Navigate</Text></TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.night },
  header: { padding: 22, paddingTop: 40, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  headerId: { fontFamily: 'JetBrainsMono-Regular', fontSize: 11, color: T.moonMute, letterSpacing: 1.6 },
  hero: { height: 200, backgroundColor: T.nightSoft, justifyContent: 'center', alignItems: 'center' },
  heroText: { fontFamily: 'JetBrainsMono-Regular', fontSize: 10, color: T.moonMute, letterSpacing: 1.4 },
  content: { padding: 22 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  title: { fontSize: 22, fontWeight: '500', color: T.moon },
  subtitle: { fontSize: 12, color: T.moonMute, marginTop: 4 },
  stats: { marginTop: 16, flexDirection: 'row', flexWrap: 'wrap', gap: 1, backgroundColor: T.nightHairS, borderRadius: 2, borderWidth: 1, borderColor: T.nightHairS },
  statBox: { width: '49.5%', backgroundColor: T.nightSoft, padding: 14 },
  statLabel: { fontSize: 9, color: T.moonFaint },
  statValue: { fontFamily: 'JetBrainsMono-Regular', fontSize: 14, color: T.moon, marginTop: 4 },
  section: { marginTop: 24 },
  contact: { marginTop: 10, padding: 14, borderWidth: 1, borderColor: T.nightHair, borderRadius: 2, flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: { width: 36, height: 36, borderRadius: 2, backgroundColor: '#D2542B33', justifyContent: 'center', alignItems: 'center' },
  avatarText: { color: T.brick, fontWeight: '600' },
  contactName: { fontSize: 14, fontWeight: '500', color: T.moon },
  contactRole: { fontSize: 11, color: T.moonMute },
  access: { marginTop: 10, padding: 14, borderWidth: 1, borderColor: T.nightHair, borderRadius: 2 },
  accessLabel: { fontSize: 13, color: T.moon },
  accessValue: { fontFamily: 'JetBrainsMono-Regular', fontSize: 16, color: T.moon },
  divider: { height: 1, backgroundColor: T.nightHair, marginVertical: 12 },
  accessNotes: { fontSize: 12, color: T.moonSoft, lineHeight: 18 },
  shotItem: { flexDirection: 'row', gap: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: T.nightHair },
  shotLabel: { fontSize: 13, color: T.moon },
  footer: { padding: 14, paddingBottom: 28, borderTopWidth: 1, borderTopColor: T.nightHair, flexDirection: 'row', gap: 10, backgroundColor: T.night },
  btnGhost: { padding: 14, borderWidth: 1, borderColor: T.nightHairS, borderRadius: 2 },
  btnAccent: { flex: 1, backgroundColor: T.brick, padding: 14, borderRadius: 2, alignItems: 'center' },
  btnAccentText: { color: T.moon, fontWeight: '500' },
});
