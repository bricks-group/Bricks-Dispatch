import React from 'react';
import { View, Text, StyleSheet, ScrollView } from 'react-native';
import { T } from '../../constants/Theme';
import { Over, I } from '../../components/bricks/Primitives';

export default function DispProfile() {
  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Payouts</Text>
      </View>
      <View style={styles.payoutCard}>
        <Over color={T.moonMute}>THIS WEEK</Over>
        <Text style={styles.payoutVal}>$2,184</Text>
        <Text style={styles.payoutMeta}>Pays out Monday · 7 jobs</Text>
      </View>
      <ScrollView style={styles.list}>
        <Over color={T.moonMute}>ACCOUNT</Over>
        {[
          ['user','Edit profile'],
          ['cards','Payout method'],
          ['logout','Sign out'],
        ].map(([ic,l],i) => (
          <View key={l} style={styles.item}>
            <I name={ic} size={17} color={T.moonMute} />
            <Text style={styles.itemLabel}>{l}</Text>
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
  payoutCard: { margin: 22, padding: 20, borderWidth: 1, borderColor: T.nightHairS, borderRadius: 2, backgroundColor: T.nightSoft },
  payoutVal: { fontFamily: 'JetBrainsMono-Regular', fontSize: 36, color: T.moon, marginTop: 10 },
  payoutMeta: { fontSize: 11, color: T.moonMute, marginTop: 4 },
  list: { padding: 22 },
  item: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: T.nightHair },
  itemLabel: { fontSize: 14, color: T.moon },
});
