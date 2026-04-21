import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Upload, FileText, Image as ImageIcon, Film, ArrowRight } from 'lucide-react-native';
import { TOKENS } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || "http://10.0.0.206:8000";
type UploadRecord = { id: string; job_id: string; filename: string; file_size: number; mime_type: string; created_at: string };
type Job = { id: string; address: string; job_type: string; status?: string };
function getFileIcon(m: string) { if (m.startsWith('image')) return ImageIcon; if (m.startsWith('video')) return Film; return FileText; }
function formatSize(b: number) { if (!b) return '0 B'; const k = 1024; const s = ['B','KB','MB','GB']; const i = Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(1))+' '+s[i]; }
function formatDate(iso: string) { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).toUpperCase(); }

export default function UploadsScreen() {
  const router = useRouter();
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const fetchData = useCallback(async () => {
    try { const [u, j] = await Promise.all([fetch(`${API}/api/uploads`), fetch(`${API}/api/jobs`)]); setUploads(await u.json()); setJobs(await j.json()); }
    catch (e) { console.error(e); } finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { fetchData(); }, [fetchData]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchData(); }, [fetchData]);
  const getAddr = (jid: string) => jobs.find(j => j.id === jid)?.address || 'Unknown';

  if (loading) return <SafeAreaView style={s.ctn}><View style={s.ldg}><ActivityIndicator size="small" color={TOKENS.brick} /></View></SafeAreaView>;
  return (
    <SafeAreaView style={s.ctn} edges={['top']}>
      <View style={s.hdr}>
        <View>
          <Text style={s.hdrT}>uploads</Text>
          <Text style={s.hdrS}>{uploads.length} files</Text>
        </View>
        <TouchableOpacity testID="jobs-shortcut-btn" style={s.uBtn} onPress={() => router.push('/')}>
          <Text style={s.uBtnT}>VIEW JOBS</Text>
        </TouchableOpacity>
      </View>
      <FlatList data={uploads} keyExtractor={i=>i.id} contentContainerStyle={s.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={TOKENS.brick} />}
        renderItem={({ item }) => { const FI = getFileIcon(item.mime_type); return (
          <View testID={`upload-item-${item.id}`} style={s.uCard}>
            <View style={s.uIco}><FI size={18} color={TOKENS.moonMute} strokeWidth={1.5} /></View>
            <View style={s.uInfo}>
              <Text style={s.uFn} numberOfLines={1}>{item.filename.toLowerCase()}</Text>
              <Text style={s.uMeta}>{formatSize(item.file_size)} · {getAddr(item.job_id).toLowerCase()}</Text>
              <Text style={s.uDate}>{formatDate(item.created_at)}</Text>
            </View>
            <TouchableOpacity testID={`view-job-${item.id}`} style={s.delBtn} onPress={() => router.push(`/job/${item.job_id}`)}>
              <ArrowRight size={14} color={TOKENS.moonMute} strokeWidth={1.5} />
            </TouchableOpacity>
          </View>
        ); }}
        ListEmptyComponent={<View style={s.empty}><Upload size={32} color={TOKENS.nightHairS} strokeWidth={1} /><Text style={s.emptyT}>no uploads yet</Text></View>}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: TOKENS.night },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  hdr: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 36, paddingTop: 12, paddingBottom: 16 },
  hdrT: { fontSize: 34, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -1.1 },
  hdrS: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonMute, marginTop: 4, textTransform: 'uppercase', letterSpacing: 1.4 },
  uBtn: { backgroundColor: TOKENS.nightSoft, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHair },
  uBtnT: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moon },
  list: { paddingHorizontal: 36, paddingBottom: 100 },
  uCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: TOKENS.nightSoft, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHair, padding: 16, marginBottom: 12 },
  uIco: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center', marginRight: 16 },
  uInfo: { flex: 1 },
  uFn: { fontSize: 14, fontFamily: 'System', color: TOKENS.moon, marginBottom: 4 },
  uMeta: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonMute, marginBottom: 4 },
  uDate: { fontSize: 10, fontFamily: 'JetBrainsMono-Regular', color: TOKENS.moonFaint },
  delBtn: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center' },
  empty: { paddingVertical: 80, alignItems: 'center', gap: 12 },
  emptyT: { fontSize: 13, fontFamily: 'System', color: TOKENS.moonFaint },
});
