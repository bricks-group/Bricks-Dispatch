import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl, Alert } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { Upload, FileText, Image as ImageIcon, Film, Trash2, Plus } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
type UploadRecord = { id: string; job_id: string; filename: string; file_size: number; mime_type: string; created_at: string };
type Job = { id: string; address: string; job_type: string; status?: string };
function getFileIcon(m: string) { if (m.startsWith('image')) return ImageIcon; if (m.startsWith('video')) return Film; return FileText; }
function formatSize(b: number) { if (!b) return '0 B'; const k = 1024; const s = ['B','KB','MB','GB']; const i = Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(1))+' '+s[i]; }
function formatDate(iso: string) { return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }

export default function UploadsScreen() {
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fetchData = useCallback(async () => {
    try { const [u, j] = await Promise.all([fetch(`${API}/api/uploads`), fetch(`${API}/api/jobs`)]); setUploads(await u.json()); setJobs(await j.json()); }
    catch (e) { console.error(e); } finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { fetchData(); }, [fetchData]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchData(); }, [fetchData]);
  const getAddr = (jid: string) => jobs.find(j => j.id === jid)?.address || 'Unknown';
  const handleUpload = async () => {
    const active = jobs.filter(j => j.status !== 'completed');
    if (!active.length) { Alert.alert('No Active Jobs'); return; }
    const r = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images','videos'], quality: 0.8, base64: true });
    if (r.canceled) return;
    const a = r.assets[0]; setUploading(true);
    try { const fd = new FormData(); fd.append('job_id', active[0].id); fd.append('filename', a.fileName||`upload_${Date.now()}.jpg`); fd.append('file_size', String(a.fileSize||0)); fd.append('mime_type', a.mimeType||'image/jpeg'); fd.append('file_data', a.base64?a.base64.substring(0,200):''); const res = await fetch(`${API}/api/uploads`, { method:'POST', body: fd }); if (res.ok) fetchData(); }
    catch (e) { Alert.alert('Error'); } finally { setUploading(false); }
  };
  const handleDelete = (id: string) => Alert.alert('Delete?', '', [{ text:'Cancel', style:'cancel' }, { text:'Delete', style:'destructive', onPress: async () => { try { await fetch(`${API}/api/uploads/${id}`, { method:'DELETE' }); fetchData(); } catch {} } }]);

  if (loading) return <SafeAreaView style={s.ctn}><View style={s.ldg}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  return (
    <SafeAreaView style={s.ctn} edges={['top']}>
      <View style={s.hdr}>
        <View><Text style={s.hdrT}>Uploads</Text><Text style={s.hdrS}>{uploads.length} files</Text></View>
        <TouchableOpacity testID="upload-btn" style={s.uBtn} onPress={handleUpload} disabled={uploading}>
          {uploading ? <ActivityIndicator size="small" color="#FFF" /> : <><Plus size={16} color="#FFF" strokeWidth={2.5} /><Text style={s.uBtnT}>Upload</Text></>}
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
            <TouchableOpacity testID={`delete-upload-${item.id}`} style={s.delBtn} onPress={() => handleDelete(item.id)}><Trash2 size={15} color="#EF4444" strokeWidth={2} /></TouchableOpacity>
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
