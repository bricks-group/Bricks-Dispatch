import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity } from 'react-native';
import { T } from '../../constants/Theme';
import { Over, Pill, I } from '../components/bricks/Primitives';

export default function DispToday() {
  const [job, setJob] = useState(null);
  const [checklist, setChecklist] = useState([
    { id: 1, label: 'Agent confirmed · M. Ortega', done: true },
    { id: 2, label: 'Lockbox code · 4812#', done: true },
    { id: 3, label: 'Drone approved for area', done: true },
    { id: 4, label: 'Upload last shoot (#08410)', done: false },
  ]);

  useEffect(() => {
    // Mock fetch for today's primary job
    setJob({
      id: '08412',
      address: '214 Linden St',
      details: 'BROOKLYN · HOUSE · 2,140 SQFT',
      arrive: '14:30',
      drive: '22 min',
      package: 'Signature'
    });
  }, []);

  const toggleCheck = (id) => {
    setChecklist(checklist.map(c => c.id === id ? { ...c, done: !c.done } : c));
  };

  const laterJobs = [
    { time: '16:00', addr: '30 Mercer Loop', type: 'Commercial', amt: '$480' },
    { time: '17:45', addr: '55 Birch Way', type: 'Condo', amt: '$380' },
    { time: '19:00', addr: '9 Harbor Way', type: 'Condo · twilight', amt: '$560' },
  ];

  if (!job) return <View style={styles.container} />;

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View>
          <Over>THURSDAY · APR 18</Over>
          <Text style={styles.title}>4 shoots.</Text>
        </View>
        <View style={styles.avatar}><Text style={styles.avatarText}>JR</Text></View>
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        <Over color={T.brick}>NEXT UP · IN 42 MIN</Over>
        <View style={styles.nextJob}>
          <Text style={styles.nextTitle}>{job.address}</Text>
          <Text style={styles.nextMeta}>{job.details}</Text>
          
          <View style={styles.jobStats}>
            {[
              ['ARRIVE', job.arrive],
              ['DRIVE', job.drive],
              ['PACKAGE', job.package],
            ].map(([l, v]) => (
              <View key={l} style={styles.statBox}>
                <Over style={styles.statLabel}>{l}</Over>
                <Text style={styles.statValue}>{v}</Text>
              </View>
            ))}
          </View>
          <View style={styles.actions}>
            <TouchableOpacity style={styles.btnNav}><Text style={styles.btnNavText}>Navigate</Text></TouchableOpacity>
            <TouchableOpacity style={styles.btnCall}><I name="phone" size={17}/></TouchableOpacity>
          </View>
        </View>

        <View style={styles.section}>
          <Over color={T.moonMute}>PRE-SHOOT CHECKLIST</Over>
          {checklist.map((item) => (
            <TouchableOpacity key={item.id} style={styles.checkItem} onPress={() => toggleCheck(item.id)}>
              <View style={[styles.checkbox, item.done && styles.checkboxDone]}>
                {item.done && <I name="chk" size={11} color="#fff"/>}
              </View>
              <Text style={[styles.checkLabel, item.done && styles.checkDoneText]}>{item.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        <View style={styles.section}>
          <Over color={T.moonMute}>LATER TODAY</Over>
          {laterJobs.map((j) => (
            <View key={j.time} style={styles.laterJob}>
              <Text style={styles.laterTime}>{j.time}</Text>
              <View style={styles.divider}/>
              <View style={{ flex: 1 }}>
                <Text style={styles.laterAddr}>{j.addr}</Text>
                <Text style={styles.laterType}>{j.type}</Text>
              </View>
              <Text style={styles.laterAmt}>{j.amt}</Text>
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: T.night },
  header: { paddingHorizontal: 22, paddingTop: 40, paddingBottom: 20, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: 22, fontWeight: '500', color: T.moon, marginTop: 4 },
  avatar: { width: 36, height: 36, borderRadius: 2, backgroundColor: '#D2542B33', justifyContent: 'center', alignItems: 'center' },
  avatarText: { color: T.brick, fontWeight: '600' },
  scroll: { paddingHorizontal: 22, paddingBottom: 40 },
  nextJob: { marginTop: 10, padding: 18, borderRadius: 2, borderWidth: 1, borderColor: T.nightHairS, backgroundColor: T.nightSoft },
  nextTitle: { fontSize: 22, fontWeight: '500', color: T.moon },
  nextMeta: { fontFamily: 'JetBrainsMono-Regular', fontSize: 12, color: T.moonMute, marginTop: 4 },
  jobStats: { marginTop: 14, flexDirection: 'row', gap: 1, backgroundColor: T.nightHairS, borderRadius: 2, padding: 1 },
  statBox: { flex: 1, backgroundColor: T.nightSoft, padding: 10 },
  statLabel: { fontSize: 9, color: T.moonFaint },
  statValue: { color: T.moon, fontFamily: 'JetBrainsMono-Regular', fontSize: 14, marginTop: 4 },
  actions: { flexDirection: 'row', gap: 8, marginTop: 14 },
  btnNav: { flex: 1, backgroundColor: T.brick, padding: 12, borderRadius: 2, alignItems: 'center' },
  btnNavText: { color: T.moon, fontWeight: '500' },
  btnCall: { padding: 12, borderWidth: 1, borderColor: T.nightHairS, borderRadius: 2 },
  section: { marginTop: 24 },
  checkItem: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: T.nightHair },
  checkbox: { width: 18, height: 18, borderRadius: 2, borderWidth: 1.5, borderColor: T.nightHairS, justifyContent: 'center', alignItems: 'center' },
  checkboxDone: { backgroundColor: T.brick, borderColor: T.brick },
  checkLabel: { fontSize: 13, color: T.moon },
  checkDoneText: { textDecorationLine: 'line-through', color: T.moonMute },
  laterJob: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 14, borderWidth: 1, borderColor: T.nightHair, borderRadius: 2, marginTop: 10 },
  laterTime: { fontFamily: 'JetBrainsMono-Regular', fontSize: 13, color: T.moon, width: 48 },
  divider: { width: 1, height: 28, backgroundColor: T.nightHair },
  laterAddr: { fontSize: 14, fontWeight: '500', color: T.moon },
  laterType: { fontSize: 11, color: T.moonMute, marginTop: 2 },
  laterAmt: { fontFamily: 'JetBrainsMono-Regular', fontSize: 13, color: T.moon },
});
