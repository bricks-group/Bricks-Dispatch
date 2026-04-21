import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, RefreshControl, Image,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Bell, Navigation, Check, Camera } from 'lucide-react-native';
import { TOKENS } from '../../constants/Theme';
import BricksWordmark from '../../components/BricksWordmark';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8000';

export default function DispatchTodayScreen() {
  const router = useRouter();
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const data = await (await fetch(`${API}/api/jobs`)).json();
      setJobs(data);
    } catch (e) { console.error(e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) return (
    <SafeAreaView style={s.container}>
      <View style={s.ldg}><ActivityIndicator size="small" color={TOKENS.brick} /></View>
    </SafeAreaView>
  );

  const nextJob = jobs.find(j => j.status !== 'completed');
  const laterJobs = jobs.filter(j => j.id !== nextJob?.id).slice(0, 2);

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <ScrollView 
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={fetchData} tintColor={TOKENS.brick} />}
      >
        {/* Header */}
        <View style={s.hdr}>
          <View>
            <Text style={s.overline}>Tuesday · Apr 21</Text>
            <Text style={s.hdrT}>Today</Text>
          </View>
          <TouchableOpacity style={s.bellBtn}>
            <Bell size={16} color={TOKENS.moon} strokeWidth={1.5} />
            <View style={s.bellDot} />
          </TouchableOpacity>
        </View>

        {/* Day summary strip */}
        <View style={s.summaryStrip}>
          {[
            ['SHOOTS', jobs.length],
            ['DRIVE', '42m'],
            ['UPLOAD', '23/45'],
          ].map(([l, v], i) => (
            <View key={l} style={[s.summaryCell, i > 0 && { borderLeftWidth: 1, borderColor: TOKENS.nightHair }]}>
              <Text style={s.summaryLabel}>{l}</Text>
              <Text style={s.summaryValue}>{v}</Text>
            </View>
          ))}
        </View>

        {/* Next up — the hero */}
        <View style={s.heroSec}>
          <View style={s.heroHdr}>
            <Text style={s.heroOverline}>Next up · in 22 min</Text>
            <Text style={s.navLink}>Navigate</Text>
          </View>

          {nextJob ? (
            <View style={s.heroCard}>
              <View style={s.placeholderImg}>
                <Camera size={24} color={TOKENS.moonFaint} strokeWidth={1} />
                <Text style={s.placeholderLabel}>PROPERTY — {nextJob.address.toUpperCase()}</Text>
              </View>
              
              <View style={s.heroBody}>
                <View style={s.heroTop}>
                  <View>
                    <Text style={s.jobId}>JOB–{nextJob.id.slice(0,5).toUpperCase()}</Text>
                    <Text style={s.heroAddress}>{nextJob.address}</Text>
                    <Text style={s.heroSub}>Brooklyn · House · 2,140 sqft</Text>
                  </View>
                  <View style={s.pkgBadge}>
                    <Text style={s.pkgText}>SIGNATURE</Text>
                  </View>
                </View>

                {/* time / distance rail */}
                <View style={s.rail}>
                  {[
                    ['ARRIVE', nextJob.time_window_start],
                    ['DRIVE',  '15 min'],
                    ['AGENT',  'M. Ortega'],
                  ].map(([l, v], i) => (
                    <View key={l} style={[s.railCell, i > 0 && { borderLeftWidth: 1, borderColor: TOKENS.nightHair }]}>
                      <Text style={s.railLabel}>{l}</Text>
                      <Text style={s.railValue}>{v}</Text>
                    </View>
                  ))}
                </View>

                {/* Checklist Preview */}
                <View style={s.cl}>
                  <View style={s.clRow}>
                    <View style={s.chk}><Check size={10} color="#FFF" strokeWidth={3} /></View>
                    <Text style={s.clTextDone}>35 photos + drone</Text>
                  </View>
                  <View style={s.clRow}>
                    <View style={s.chkEmpty} />
                    <Text style={s.clText}>60-sec reel</Text>
                  </View>
                  <View style={s.clRow}>
                    <View style={s.chkEmpty} />
                    <Text style={s.clText}>Floor plan scan</Text>
                  </View>
                </View>

                <TouchableOpacity style={s.heroBtn} onPress={() => router.push(`/job/${nextJob.id}`)}>
                  <Navigation size={15} color="#FFF" strokeWidth={2} />
                  <Text style={s.heroBtnText}>Start navigation</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <Text style={s.noJobs}>No more jobs today.</Text>
          )}
        </View>

        {/* Later today */}
        <View style={s.laterSec}>
          <Text style={s.heroOverline}>Later today</Text>
          <View style={s.laterList}>
            {laterJobs.map((j, i) => (
              <TouchableOpacity 
                key={j.id} 
                style={[s.laterRow, i === 0 && { borderTopWidth: 1 }]}
                onPress={() => router.push(`/job/${j.id}`)}
              >
                <Text style={s.laterTime}>{j.time_window_start}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={s.laterAddr}>{j.address}</Text>
                  <Text style={s.laterSub}>Condo · 1,200 sqft</Text>
                </View>
                <View style={s.laterPkg}>
                  <Text style={s.laterPkgText}>ESSENTIAL</Text>
                </View>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        <View style={{ height: 100 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  hdr: { paddingHorizontal: 24, paddingTop: 10, paddingBottom: 18, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  overline: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, color: TOKENS.moonMute, letterSpacing: 1.6, textTransform: 'uppercase' },
  hdrT: { fontSize: 26, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -0.8, marginTop: 4 },
  bellBtn: { width: 36, height: 36, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, alignItems: 'center', justifyContent: 'center' },
  bellDot: { position: 'absolute', top: 6, right: 6, width: 6, height: 6, borderRadius: 3, backgroundColor: TOKENS.brick },
  
  summaryStrip: { marginHorizontal: 24, flexDirection: 'row', backgroundColor: TOKENS.night, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  summaryCell: { flex: 1, padding: 12 },
  summaryLabel: { fontFamily: 'JetBrainsMono-Medium', fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.6 },
  summaryValue: { fontFamily: 'JetBrainsMono-Regular', fontSize: 22, color: TOKENS.moon, marginTop: 4, letterSpacing: -0.5 },

  heroSec: { paddingHorizontal: 24, paddingTop: 22 },
  heroHdr: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 },
  heroOverline: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, color: TOKENS.moonMute, letterSpacing: 1.4, textTransform: 'uppercase' },
  navLink: { fontSize: 12, color: TOKENS.brick, fontFamily: 'System', fontWeight: '500' },
  
  heroCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, overflow: 'hidden' },
  placeholderImg: { height: 132, backgroundColor: TOKENS.nightDeep, alignItems: 'center', justifyContent: 'center', gap: 8 },
  placeholderLabel: { fontFamily: 'JetBrainsMono-Medium', fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1 },
  
  heroBody: { padding: 18 },
  heroTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  jobId: { fontFamily: 'JetBrainsMono-Regular', fontSize: 11, color: TOKENS.moonMute, letterSpacing: 1.4 },
  heroAddress: { fontSize: 19, fontFamily: 'System', color: TOKENS.moon, marginTop: 6, letterSpacing: -0.3 },
  heroSub: { fontSize: 13, color: TOKENS.moonMute, marginTop: 2 },
  pkgBadge: { paddingHorizontal: 9, paddingVertical: 5, backgroundColor: '#F4D9CC22', borderRadius: 2 },
  pkgText: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, color: TOKENS.brickSoft, letterSpacing: 1.4 },
  
  rail: { marginTop: 16, paddingVertical: 12, flexDirection: 'row', borderTopWidth: 1, borderBottomWidth: 1, borderColor: TOKENS.nightHair },
  railCell: { flex: 1, paddingLeft: 0, paddingRight: 0, alignItems: 'flex-start' },
  railLabel: { fontFamily: 'JetBrainsMono-Medium', fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.6 },
  railValue: { fontFamily: 'JetBrainsMono-Regular', fontSize: 13, color: TOKENS.moon, marginTop: 3 },
  
  cl: { marginTop: 14, gap: 6 },
  clRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  chk: { width: 16, height: 16, borderRadius: 2, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center' },
  chkEmpty: { width: 16, height: 16, borderRadius: 2, borderWidth: 1.5, borderColor: TOKENS.nightHairS },
  clText: { fontSize: 13, color: TOKENS.moon },
  clTextDone: { fontSize: 13, color: TOKENS.moonMute, textDecorationLine: 'line-through' },
  
  heroBtn: { marginTop: 16, width: '100%', backgroundColor: TOKENS.brick, padding: 15, borderRadius: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
  heroBtnText: { color: '#FFF', fontFamily: 'System', fontSize: 15, fontWeight: '500' },

  laterSec: { paddingHorizontal: 24, paddingTop: 18 },
  laterList: { marginTop: 10 },
  laterRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14, borderColor: TOKENS.nightHair, borderBottomWidth: 1 },
  laterTime: { fontFamily: 'JetBrainsMono-Regular', fontSize: 14, color: TOKENS.moon, width: 60 },
  laterAddr: { fontSize: 14, fontFamily: 'System', color: TOKENS.moon, fontWeight: '500' },
  laterSub: { fontSize: 12, color: TOKENS.moonMute, marginTop: 2 },
  laterPkg: { paddingHorizontal: 8, paddingVertical: 4, backgroundColor: TOKENS.nightSoft, borderRadius: 2 },
  laterPkgText: { fontFamily: 'JetBrainsMono-Medium', fontSize: 9, color: TOKENS.moonSoft, letterSpacing: 1.4, textTransform: 'uppercase' },
  noJobs: { color: TOKENS.moonFaint, textAlign: 'center', marginTop: 20 }
});
