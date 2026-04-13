import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, RefreshControl } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { ArrowLeft, Camera, Video, Plane, Box, Ruler, MapPin, Clock, CheckSquare, Square, AlertTriangle, Upload, FileText } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = { id: string; address: string; city: string; state: string; zip_code: string; time_window_start: string; time_window_end: string; job_type: string; status: string; priority: string; property_details: string; client_instructions: string; checklist: ChecklistItem[]; deliverables_count: number; uploaded_count: number; scheduled_date: string };
type UploadRecord = { id: string; filename: string; file_size: number; mime_type: string; created_at: string };
const ICONS: Record<string, React.ComponentType<any>> = { photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler };
const LABELS: Record<string, string> = { photo: 'Photo', video: 'Video', drone: 'Drone', '3d_tour': '3D Tour', floor_plan: 'Floor Plan' };
const SC: Record<string, { bg: string; dot: string; text: string; label: string; action: string }> = {
  assigned: { bg: '#3B82F615', dot: '#3B82F6', text: '#60A5FA', label: 'Assigned', action: 'Start Job' },
  in_progress: { bg: '#F59E0B15', dot: '#F59E0B', text: '#FBBF24', label: 'In Progress', action: 'Complete Job' },
  completed: { bg: '#22C55E15', dot: '#22C55E', text: '#4ADE80', label: 'Completed', action: '' },
};
function fmtSize(b: number) { if (!b) return '0 B'; const k=1024,s=['B','KB','MB','GB'],i=Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(1))+' '+s[i]; }

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [uploading, setUploading] = useState(false);

  const fetchData = useCallback(async () => {
    try { const [j, u] = await Promise.all([fetch(`${API}/api/jobs/${id}`), fetch(`${API}/api/uploads?job_id=${id}`)]); setJob(await j.json()); setUploads(await u.json()); }
    catch (e) { console.error(e); } finally { setLoading(false); setRefreshing(false); }
  }, [id]);
  useEffect(() => { fetchData(); }, [fetchData]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchData(); }, [fetchData]);

  const handleStatus = async () => {
    if (!job || actionLoading) return;
    const next = job.status === 'assigned' ? 'in_progress' : 'completed';
    setActionLoading(true);
    try { const r = await fetch(`${API}/api/jobs/${id}/status`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ status: next }) }); if (r.ok) setJob(await r.json()); else Alert.alert('Error'); }
    catch { Alert.alert('Error'); } finally { setActionLoading(false); }
  };
  const toggleCheck = async (itemId: string, done: boolean) => {
    if (!job) return;
    setJob({ ...job, checklist: job.checklist.map(i => i.id === itemId ? { ...i, completed: !done } : i) });
    try { await fetch(`${API}/api/jobs/${id}/checklist/${itemId}`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ completed: !done }) }); }
    catch { setJob({ ...job, checklist: job.checklist.map(i => i.id === itemId ? { ...i, completed: done } : i) }); }
  };
  const handleUpload = async () => {
    if (!job) return;
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images','videos'], quality: 0.8, base64: true });
    if (r.canceled) return;
    const a = r.assets[0]; setUploading(true);
    try { const fd = new FormData(); fd.append('job_id', job.id); fd.append('filename', a.fileName||`upload_${Date.now()}.jpg`); fd.append('file_size', String(a.fileSize||0)); fd.append('mime_type', a.mimeType||'image/jpeg'); fd.append('file_data', a.base64?a.base64.substring(0,200):''); const res = await fetch(`${API}/api/uploads`, { method:'POST', body: fd }); if (res.ok) fetchData(); }
    catch { } finally { setUploading(false); }
  };

  if (loading || !job) return <SafeAreaView style={st.ctn}><View style={st.ldg}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  const sc = SC[job.status] || SC.assigned;
  const TI = ICONS[job.job_type] || Camera;
  const done = job.checklist.filter(c => c.completed).length;
  const total = job.checklist.length;

  return (
    <SafeAreaView style={st.ctn} edges={['top']}>
      <View style={st.topBar}>
        <TouchableOpacity testID="back-btn" onPress={() => router.back()} style={st.backBtn}><ArrowLeft size={20} color="#FFF" strokeWidth={2.5} /></TouchableOpacity>
        <Text style={st.topTitle}>Job Details</Text>
        <View style={[st.sBadge, { backgroundColor: sc.bg }]}><View style={[st.sDot, { backgroundColor: sc.dot }]} /><Text style={[st.sTx, { color: sc.text }]}>{sc.label}</Text></View>
      </View>
      <ScrollView contentContainerStyle={st.scroll} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}>
        <View style={st.typeRow}>
          <View style={st.typeW}><View style={st.typeIco}><TI size={14} color="#8B8FA3" strokeWidth={2} /></View><Text style={st.typeTx}>{LABELS[job.job_type] || job.job_type}</Text></View>
          {(job.priority === 'urgent' || job.priority === 'high') && (
            <View style={[st.priBadge, job.priority === 'urgent' ? st.urgBadge : st.hiBadge]}>
              {job.priority === 'urgent' && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
              <Text style={st.priTx}>{job.priority === 'urgent' ? 'Urgent' : 'High Priority'}</Text>
            </View>
          )}
        </View>
        <View style={st.addrSec}><MapPin size={16} color="#3B82F6" strokeWidth={2.5} /><View style={st.addrTx}><Text style={st.addr}>{job.address}</Text><Text style={st.city}>{job.city}, {job.state} {job.zip_code}</Text></View></View>
        <View style={st.timeSec}><Clock size={14} color="#8B8FA3" strokeWidth={2} /><Text style={st.timeTx}>{job.time_window_start} – {job.time_window_end}</Text><Text style={st.dateTx}>{job.scheduled_date}</Text></View>
        {job.property_details ? <View style={st.sec}><Text style={st.secT}>Property</Text><Text style={st.secB}>{job.property_details}</Text></View> : null}
        {job.client_instructions ? <View style={st.sec}><Text style={st.secT}>Client Instructions</Text><Text style={st.secB}>{job.client_instructions}</Text></View> : null}
        {total > 0 && (
          <View style={st.sec}>
            <View style={st.secH}><Text style={st.secT}>Checklist</Text><Text style={st.clProg}>{done}/{total}</Text></View>
            <View style={st.progTrack}><View style={[st.progFill, { width: total > 0 ? `${(done/total)*100}%` : '0%' }]} /></View>
            {job.checklist.map(i => (
              <TouchableOpacity key={i.id} testID={`checklist-${i.id}`} style={st.clItem} onPress={() => toggleCheck(i.id, i.completed)}>
                {i.completed ? <CheckSquare size={20} color="#3B82F6" strokeWidth={2.5} /> : <Square size={20} color="#3D4160" strokeWidth={1.8} />}
                <Text style={[st.clLabel, i.completed && st.clDone]}>{i.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}
        <View style={st.sec}>
          <View style={st.secH}><Text style={st.secT}>Uploads</Text><Text style={st.clProg}>{job.uploaded_count}/{job.deliverables_count}</Text></View>
          {uploads.map(u => <View key={u.id} testID={`job-upload-${u.id}`} style={st.upItem}><FileText size={14} color="#8B8FA3" strokeWidth={2} /><Text style={st.upFn} numberOfLines={1}>{u.filename}</Text><Text style={st.upSz}>{fmtSize(u.file_size)}</Text></View>)}
          {job.status !== 'completed' && (
            <TouchableOpacity testID="job-upload-btn" style={st.upBtn} onPress={handleUpload} disabled={uploading}>
              {uploading ? <ActivityIndicator size="small" color="#FFF" /> : <><Upload size={14} color="#FFF" strokeWidth={2.5} /><Text style={st.upBtnTx}>Upload Media</Text></>}
            </TouchableOpacity>
          )}
        </View>
      </ScrollView>
      {job.status !== 'completed' && (
        <View style={st.actBar}>
          <TouchableOpacity testID="status-action-btn" style={[st.actBtn, job.status === 'assigned' ? st.startBtn : st.compBtn]} onPress={handleStatus} disabled={actionLoading}>
            {actionLoading ? <ActivityIndicator size="small" color="#FFF" /> : <Text style={st.actBtnTx}>{sc.action}</Text>}
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

const st = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: '#0C0D14' },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  topBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', backgroundColor: '#151722', borderRadius: 10, borderWidth: 1, borderColor: '#1E2030' },
  topTitle: { flex: 1, fontSize: 16, fontFamily: 'Outfit_600SemiBold', color: '#FFF', marginLeft: 12 },
  sBadge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  sDot: { width: 6, height: 6, borderRadius: 3 },
  sTx: { fontSize: 12, fontFamily: 'Manrope_600SemiBold' },
  scroll: { paddingBottom: 120 },
  typeRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 20, paddingBottom: 8 },
  typeW: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  typeIco: { width: 30, height: 30, borderRadius: 8, backgroundColor: '#151722', alignItems: 'center', justifyContent: 'center' },
  typeTx: { fontSize: 13, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  priBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  urgBadge: { backgroundColor: '#EF4444' },
  hiBadge: { backgroundColor: '#F59E0B' },
  priTx: { fontSize: 11, fontFamily: 'Manrope_600SemiBold', color: '#FFF' },
  addrSec: { flexDirection: 'row', paddingHorizontal: 20, paddingVertical: 12, gap: 12, alignItems: 'flex-start' },
  addrTx: { flex: 1 },
  addr: { fontSize: 22, fontFamily: 'Outfit_700Bold', color: '#FFF', letterSpacing: -0.3, marginBottom: 4 },
  city: { fontSize: 14, fontFamily: 'Manrope_400Regular', color: '#565B7E' },
  timeSec: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingBottom: 16, gap: 8, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  timeTx: { fontSize: 15, fontFamily: 'JetBrainsMono_500Medium', color: '#3B82F6' },
  dateTx: { fontSize: 13, fontFamily: 'JetBrainsMono_400Regular', color: '#3D4160', marginLeft: 'auto' },
  sec: { paddingHorizontal: 20, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  secH: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  secT: { fontSize: 14, fontFamily: 'Outfit_500Medium', color: '#8B8FA3', marginBottom: 8 },
  secB: { fontSize: 14, fontFamily: 'Manrope_400Regular', lineHeight: 22, color: '#C4C7D8' },
  clProg: { fontSize: 14, fontFamily: 'JetBrainsMono_500Medium', color: '#3B82F6' },
  progTrack: { height: 4, backgroundColor: '#1E2030', borderRadius: 2, marginBottom: 14 },
  progFill: { height: '100%', borderRadius: 2, backgroundColor: '#3B82F6' },
  clItem: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#151722' },
  clLabel: { fontSize: 14, fontFamily: 'Manrope_400Regular', color: '#C4C7D8', flex: 1 },
  clDone: { color: '#3D4160', textDecorationLine: 'line-through' },
  upItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#151722' },
  upFn: { flex: 1, fontSize: 13, fontFamily: 'Manrope_400Regular', color: '#C4C7D8' },
  upSz: { fontSize: 12, fontFamily: 'JetBrainsMono_400Regular', color: '#3D4160' },
  upBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: '#3B82F6', paddingVertical: 14, borderRadius: 12, marginTop: 12 },
  upBtnTx: { fontSize: 14, fontFamily: 'Manrope_600SemiBold', color: '#FFF' },
  actBar: { position: 'absolute', bottom: 0, left: 0, right: 0, paddingHorizontal: 16, paddingVertical: 16, paddingBottom: 32, backgroundColor: '#0C0D14', borderTopWidth: 1, borderTopColor: '#1E2030' },
  actBtn: { paddingVertical: 16, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  startBtn: { backgroundColor: '#3B82F6' },
  compBtn: { backgroundColor: '#22C55E' },
  actBtnTx: { fontSize: 16, fontFamily: 'Outfit_600SemiBold', color: '#FFF', letterSpacing: 0.3 },
});
