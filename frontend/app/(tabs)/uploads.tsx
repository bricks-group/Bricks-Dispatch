import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl, Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { Upload, FileText, Image as ImageIcon, Film, Trash2, Plus } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type UploadRecord = { id: string; job_id: string; filename: string; file_size: number; mime_type: string; created_at: string };
type Job = { id: string; address: string; job_type: string; status?: string };

function getFileIcon(mime: string) {
  if (mime.startsWith('image')) return ImageIcon;
  if (mime.startsWith('video')) return Film;
  return FileText;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function UploadsScreen() {
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [uploadsRes, jobsRes] = await Promise.all([fetch(`${API}/api/uploads`), fetch(`${API}/api/jobs`)]);
      setUploads(await uploadsRes.json());
      setJobs(await jobsRes.json());
    } catch (e) { console.error('Failed to fetch uploads:', e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchData(); }, [fetchData]);

  const getJobAddress = (jobId: string): string => {
    const job = jobs.find(j => j.id === jobId);
    return job ? job.address : 'Unknown Job';
  };

  const handlePickAndUpload = async () => {
    const activeJobs = jobs.filter(j => j.status !== 'completed');
    if (activeJobs.length === 0) { Alert.alert('No Active Jobs', 'No active jobs to upload to.'); return; }
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images', 'videos'], quality: 0.8, base64: true });
    if (result.canceled) return;
    const asset = result.assets[0];
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('job_id', activeJobs[0].id);
      formData.append('filename', asset.fileName || `upload_${Date.now()}.jpg`);
      formData.append('file_size', String(asset.fileSize || 0));
      formData.append('mime_type', asset.mimeType || 'image/jpeg');
      formData.append('file_data', asset.base64 ? asset.base64.substring(0, 200) : '');
      const res = await fetch(`${API}/api/uploads`, { method: 'POST', body: formData });
      if (res.ok) fetchData();
      else Alert.alert('Upload Failed', 'Could not upload the file.');
    } catch (e) { Alert.alert('Upload Error', 'Something went wrong.'); }
    finally { setUploading(false); }
  };

  const handleDelete = (uploadId: string) => {
    Alert.alert('Delete Upload', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: async () => {
        try { await fetch(`${API}/api/uploads/${uploadId}`, { method: 'DELETE' }); fetchData(); } catch (e) {}
      }},
    ]);
  };

  if (loading) {
    return <SafeAreaView style={styles.container}><View style={styles.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Uploads</Text>
          <Text style={styles.headerSub}>{uploads.length} files</Text>
        </View>
        <TouchableOpacity testID="upload-btn" style={styles.uploadBtn} onPress={handlePickAndUpload} disabled={uploading}>
          {uploading ? <ActivityIndicator size="small" color="#FFFFFF" /> : (
            <><Plus size={16} color="#FFFFFF" strokeWidth={2.5} /><Text style={styles.uploadBtnText}>Upload</Text></>
          )}
        </TouchableOpacity>
      </View>

      <FlatList
        data={uploads}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => {
          const FileIcon = getFileIcon(item.mime_type);
          return (
            <View testID={`upload-item-${item.id}`} style={styles.uploadCard}>
              <View style={styles.uploadIcon}><FileIcon size={18} color="#8B8FA3" strokeWidth={2} /></View>
              <View style={styles.uploadInfo}>
                <Text style={styles.uploadFilename} numberOfLines={1}>{item.filename}</Text>
                <Text style={styles.uploadMeta}>{formatSize(item.file_size)} · {getJobAddress(item.job_id)}</Text>
                <Text style={styles.uploadDate}>{formatDate(item.created_at)}</Text>
              </View>
              <TouchableOpacity testID={`delete-upload-${item.id}`} style={styles.deleteBtn} onPress={() => handleDelete(item.id)}>
                <Trash2 size={15} color="#EF4444" strokeWidth={2} />
              </TouchableOpacity>
            </View>
          );
        }}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}
        ListEmptyComponent={
          <View style={styles.emptyWrap}>
            <Upload size={36} color="#252839" strokeWidth={1.5} />
            <Text style={styles.emptyText}>No uploads yet</Text>
            <Text style={styles.emptySubtext}>Tap Upload to add files</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  headerTitle: { fontSize: 26, fontWeight: '700', color: '#FFFFFF', letterSpacing: -0.5 },
  headerSub: { fontSize: 13, color: '#565B7E', marginTop: 2 },
  uploadBtn: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: '#3B82F6', paddingHorizontal: 18, paddingVertical: 11, borderRadius: 12 },
  uploadBtnText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  list: { paddingHorizontal: 16, paddingBottom: 100 },
  uploadCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 14, marginBottom: 8 },
  uploadIcon: { width: 42, height: 42, borderRadius: 10, backgroundColor: '#1E2030', alignItems: 'center', justifyContent: 'center', marginRight: 12 },
  uploadInfo: { flex: 1 },
  uploadFilename: { fontSize: 14, fontWeight: '600', color: '#FFFFFF', marginBottom: 2 },
  uploadMeta: { fontSize: 12, color: '#8B8FA3', marginBottom: 1 },
  uploadDate: { fontSize: 11, color: '#3D4160' },
  deleteBtn: { width: 38, height: 38, borderRadius: 10, backgroundColor: '#1E2030', alignItems: 'center', justifyContent: 'center' },
  emptyWrap: { paddingVertical: 80, alignItems: 'center', gap: 10 },
  emptyText: { fontSize: 15, fontWeight: '600', color: '#565B7E' },
  emptySubtext: { fontSize: 13, color: '#3D4160' },
});
