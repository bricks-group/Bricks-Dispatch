import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, RefreshControl } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { ChevronLeft, Camera, Video, Plane, Box, Ruler, Navigation, Upload, Check } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { TOKENS } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || "http://10.0.0.206:8000";

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = { 
  id: string; address: string; city: string; state: string; zip_code: string; 
  time_window_start: string; time_window_end: string; job_type: string; 
  status: string; priority: string; property_details: string; 
  client_instructions: string; checklist: ChecklistItem[]; 
  deliverables_count: number; uploaded_count: number; scheduled_date: string 
};

const ICONS: Record<string, React.ComponentType<any>> = { photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler };
const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  assigned: { bg: TOKENS.nightDeep, fg: TOKENS.moonSoft },
  in_progress: { bg: TOKENS.brickTint, fg: TOKENS.brickDeep },
  completed: { bg: TOKENS.okTint, fg: TOKENS.ok }
};

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/jobs/${id}`);
      setJob(await res.json());
    } catch (e) { console.error(e); }
    finally { setLoading(false); setRefreshing(false); }
  }, [id]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleStatus = async () => {
    if (!job || actionLoading) return;
    const next = job.status === 'assigned' ? 'in_progress' : 'completed';
    setActionLoading(true);
    try {
      const r = await fetch(`${API}/api/jobs/${id}/status`, { 
        method:'PUT', 
        headers:actorHeaders({'Content-Type':'application/json'}), 
        body: JSON.stringify({ status: next }) 
      });
      if (r.ok) setJob(await r.json());
    } finally { setActionLoading(false); }
  };

  if (loading || !job) return <SafeAreaView style={s.container}><View style={s.ldg}><ActivityIndicator size="small" color={TOKENS.brick} /></View></SafeAreaView>;

  const badge = STATUS_BADGE[job.status] || STATUS_BADGE.assigned;

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <View style={s.topBar}>
        <TouchableOpacity onPress={() => router.back()} style={s.backBtn}>
          <ChevronLeft size={20} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.topId}>JOB–{job.id.slice(0,5).toUpperCase()}</Text>
        <View style={[s.badge, { backgroundColor: badge.bg }]}>
          <Text style={[s.badgeText, { color: badge.fg }]}>{job.status.toUpperCase().replace('_', ' ')}</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={s.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={fetchData} tintColor={TOKENS.brick} />}>
        <View style={s.hero}>
          <Text style={s.address}>{job.address.toLowerCase()}</Text>
          <Text style={s.sub}>{job.city}, {job.state} · {job.job_type.replace('_', ' ')}</Text>
        </View>

        {/* Rail grid */}
        <View style={s.rail}>
          <View style={s.railCell}>
            <Text style={s.railL}>ARRIVE</Text>
            <Text style={s.railV}>{job.time_window_start}</Text>
          </View>
          <View style={[s.railCell, { borderLeftWidth: 1, borderColor: TOKENS.nightHair }]}>
            <Text style={s.railL}>AGENT</Text>
            <Text style={s.railV}>M. Ortega</Text>
          </View>
          <View style={[s.railCell, { borderLeftWidth: 1, borderColor: TOKENS.nightHair }]}>
            <Text style={s.railL}>PACKAGE</Text>
            <Text style={s.railV}>SIGNATURE</Text>
          </View>
        </View>

        {/* Checklist section */}
        <View style={s.section}>
          <Text style={s.sectionL}>CHECKLIST</Text>
          <View style={s.clList}>
            {job.checklist.map(item => (
              <View key={item.id} style={s.clRow}>
                <View style={item.completed ? s.chk : s.chkEmpty}>
                  {item.completed && <Check size={10} color="#FFF" strokeWidth={3} />}
                </View>
                <Text style={item.completed ? s.clTextDone : s.clText}>{item.label}</Text>
              </View>
            ))}
          </View>
        </View>

        {/* Property section */}
        <View style={s.section}>
          <Text style={s.sectionL}>PROPERTY DETAILS</Text>
          <Text style={s.body}>{job.property_details || 'No specific details provided.'}</Text>
        </View>
        
        <View style={{ height: 120 }} />
      </ScrollView>

      <View style={s.footer}>
        <TouchableOpacity style={s.primaryBtn} onPress={handleStatus} disabled={actionLoading}>
          {actionLoading ? <ActivityIndicator color="#FFF" /> : (
            <>
              <Navigation size={15} color="#FFF" strokeWidth={2} />
              <Text style={s.btnText}>{job.status === 'assigned' ? 'START JOB' : 'COMPLETE JOB'}</Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  topBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 36, paddingVertical: 16, borderBottomWidth: 1, borderColor: TOKENS.nightHair },
  backBtn: { marginRight: 16 },
  topId: { flex: 1, fontFamily: 'JetBrainsMono-Medium', fontSize: 11, color: TOKENS.moonMute, letterSpacing: 1.4 },
  badge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 2 },
  badgeText: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, letterSpacing: 1 },
  
  content: { padding: 36 },
  hero: { marginBottom: 32 },
  address: { fontSize: 28, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -0.6, marginBottom: 8 },
  sub: { fontSize: 14, color: TOKENS.moonMute },
  
  rail: { flexDirection: 'row', backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, marginBottom: 32 },
  railCell: { flex: 1, padding: 16 },
  railL: { fontFamily: 'JetBrainsMono-Medium', fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.4, marginBottom: 6 },
  railV: { fontFamily: 'JetBrainsMono-Regular', fontSize: 13, color: TOKENS.moon },
  
  section: { marginBottom: 32 },
  sectionL: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, color: TOKENS.moonMute, letterSpacing: 1.6, marginBottom: 16 },
  clList: { gap: 12 },
  clRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  chk: { width: 16, height: 16, borderRadius: 2, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center' },
  chkEmpty: { width: 16, height: 16, borderRadius: 2, borderWidth: 1.5, borderColor: TOKENS.nightHairS },
  clText: { fontSize: 14, color: TOKENS.moon },
  clTextDone: { fontSize: 14, color: TOKENS.moonMute, textDecorationLine: 'line-through' },
  body: { fontSize: 14, color: TOKENS.moonSoft, lineHeight: 22 },
  
  footer: { position: 'absolute', bottom: 0, left: 0, right: 0, padding: 36, backgroundColor: TOKENS.night, borderTopWidth: 1, borderColor: TOKENS.nightHair },
  primaryBtn: { backgroundColor: TOKENS.brick, padding: 16, borderRadius: 2, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
  btnText: { color: '#FFF', fontFamily: 'System', fontSize: 15, fontWeight: '500' }
});
