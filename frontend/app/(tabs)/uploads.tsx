import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Upload, FileText, Image as ImageIcon, Film, ArrowRight } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
type UploadRecord = { id: string; job_id: string; filename: string; file_size: number; mime_type: string; created_at: string };
type Job = { id: string; address: string; job_type: string; status?: string };
function getFileIcon(m: string) { if (m.startsWith('image')) return ImageIcon; if (m.startsWith('video')) return Film; return FileText; }
function formatSize(b: number) { if (!b) return '0 B'; const k = 1024; const s = ['B','KB','MB','GB']; const i = Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(1))+' '+s[i]; }
function formatDate(iso: string) { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }

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

  if (loading) return <SafeAreaView style={s.ctn}><View style={s.ldg}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  return (
    <SafeAreaView style={s.ctn} edges={['top']}>
      <View style={s.hdr}>
        <View>
          <Text style={s.hdrT}>Uploads</Text>
          <Text style={s.hdrS}>{uploads.length} files</Text>
          <Text style={s.hdrHelp}>Upload new media from each job detail screen.</Text>
        </View>
        <TouchableOpacity testID="jobs-shortcut-btn" style={s.uBtn} onPress={() => router.push('/')}>
          <ArrowRight size={16} color="#FFF" strokeWidth={2.5} /><Text style={s.uBtnT}>Jobs</Text>
        </TouchableOpacity>
      </View>
      <FlatList data={uploads} keyExtractor={i=>i.id} contentContainerStyle={s.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}
        renderItem={({ item }) => { const FI = getFileIcon(item.mime_type); return (
          <View testID={`upload-item-${item.id}`} style={s.uCard}>
            <View style={s.uIco}><FI size={18} color="#8B8FA3" strokeWidth={2} /></View>
            <View style={s.uInfo}>
              <Text style={s.uFn} numberOfLines={1}>{item.filename}</Text>
              <Text style={s.uMeta}>{formatSize(item.file_size)} · {getAddr(item.job_id)}</Text>
              <Text style={s.uDate}>{formatDate(item.created_at)}</Text>
            </View>
            <TouchableOpacity testID={`view-job-${item.id}`} style={s.delBtn} onPress={() => router.push(`/job/${item.job_id}`)}>
              <ArrowRight size={15} color="#3B82F6" strokeWidth={2.5} />
            </TouchableOpacity>
          </View>
        ); }}
        ListEmptyComponent={<View style={s.empty}><Upload size={36} color="#252839" strokeWidth={1.5} /><Text style={s.emptyT}>No uploads yet</Text></View>}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: '#0C0D14' },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  hdr: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  hdrT: { fontSize: 26, fontFamily: 'Outfit_600SemiBold', color: '#FFF', letterSpacing: -0.5 },
  hdrS: { fontSize: 13, fontFamily: 'Manrope_400Regular', color: '#565B7E', marginTop: 2 },
  hdrHelp: { fontSize: 11, fontFamily: 'Manrope_400Regular', color: '#8B8FA3', marginTop: 4, maxWidth: 210 },
  uBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: '#3B82F6', paddingHorizontal: 18, paddingVertical: 11, borderRadius: 12 },
  uBtnT: { fontSize: 14, fontFamily: 'Manrope_600SemiBold', color: '#FFF' },
  list: { paddingHorizontal: 16, paddingBottom: 100 },
  uCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 14, marginBottom: 8 },
  uIco: { width: 42, height: 42, borderRadius: 10, backgroundColor: '#1E2030', alignItems: 'center', justifyContent: 'center', marginRight: 12 },
  uInfo: { flex: 1 },
  uFn: { fontSize: 14, fontFamily: 'Manrope_600SemiBold', color: '#FFF', marginBottom: 2 },
  uMeta: { fontSize: 12, fontFamily: 'Manrope_400Regular', color: '#8B8FA3', marginBottom: 1 },
  uDate: { fontSize: 11, fontFamily: 'JetBrainsMono_400Regular', color: '#3D4160' },
  delBtn: { width: 38, height: 38, borderRadius: 10, backgroundColor: '#1E2030', alignItems: 'center', justifyContent: 'center' },
  empty: { paddingVertical: 80, alignItems: 'center', gap: 10 },
  emptyT: { fontSize: 15, fontFamily: 'Manrope_500Medium', color: '#565B7E' },
});
