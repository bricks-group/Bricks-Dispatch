import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Alert, RefreshControl,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import {
  ArrowLeft, Camera, Video, Plane, Box, Ruler, MapPin, Clock,
  CheckSquare, Square, AlertTriangle, Upload, FileText,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = {
  id: string; address: string; city: string; state: string; zip_code: string;
  time_window_start: string; time_window_end: string; job_type: string; status: string;
  priority: string; property_details: string; client_instructions: string;
  checklist: ChecklistItem[]; deliverables_count: number; uploaded_count: number; scheduled_date: string;
};
type UploadRecord = { id: string; filename: string; file_size: number; mime_type: string; created_at: string };

const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = {
  photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler,
};
const JOB_TYPE_LABELS: Record<string, string> = {
  photo: 'Photo', video: 'Video', drone: 'Drone', '3d_tour': '3D Tour', floor_plan: 'Floor Plan',
};
const STATUS_CONFIG: Record<string, { bg: string; dot: string; text: string; label: string; actionLabel: string }> = {
  assigned: { bg: '#3B82F615', dot: '#3B82F6', text: '#60A5FA', label: 'Assigned', actionLabel: 'Start Job' },
  in_progress: { bg: '#F59E0B15', dot: '#F59E0B', text: '#FBBF24', label: 'In Progress', actionLabel: 'Complete Job' },
  completed: { bg: '#22C55E15', dot: '#22C55E', text: '#4ADE80', label: 'Completed', actionLabel: '' },
};

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024; const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

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
    try {
      const [jobRes, uploadsRes] = await Promise.all([
        fetch(`${API}/api/jobs/${id}`), fetch(`${API}/api/uploads?job_id=${id}`),
      ]);
      setJob(await jobRes.json());
      setUploads(await uploadsRes.json());
    } catch (e) { console.error('Failed to fetch job:', e); }
    finally { setLoading(false); setRefreshing(false); }
  }, [id]);

  useEffect(() => { fetchData(); }, [fetchData]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchData(); }, [fetchData]);

  const handleStatusUpdate = async () => {
    if (!job || actionLoading) return;
    const nextStatus = job.status === 'assigned' ? 'in_progress' : 'completed';
    setActionLoading(true);
    try {
      const res = await fetch(`${API}/api/jobs/${id}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: nextStatus }),
      });
      if (res.ok) setJob(await res.json());
      else Alert.alert('Error', 'Could not update status');
    } catch (e) { Alert.alert('Error', 'Network error'); }
    finally { setActionLoading(false); }
  };

  const handleToggleChecklist = async (itemId: string, completed: boolean) => {
    if (!job) return;
    setJob({ ...job, checklist: job.checklist.map(item => item.id === itemId ? { ...item, completed: !completed } : item) });
    try {
      await fetch(`${API}/api/jobs/${id}/checklist/${itemId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ completed: !completed }),
      });
    } catch (e) {
      setJob({ ...job, checklist: job.checklist.map(item => item.id === itemId ? { ...item, completed } : item) });
    }
  };

  const handleUpload = async () => {
    if (!job) return;
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images', 'videos'], quality: 0.8, base64: true });
    if (result.canceled) return;
    const asset = result.assets[0];
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('job_id', job.id);
      formData.append('filename', asset.fileName || `upload_${Date.now()}.jpg`);
      formData.append('file_size', String(asset.fileSize || 0));
      formData.append('mime_type', asset.mimeType || 'image/jpeg');
      formData.append('file_data', asset.base64 ? asset.base64.substring(0, 200) : '');
      const res = await fetch(`${API}/api/uploads`, { method: 'POST', body: formData });
      if (res.ok) fetchData();
    } catch (e) { console.error('Upload error:', e); }
    finally { setUploading(false); }
  };

  if (loading || !job) {
    return <SafeAreaView style={styles.container}><View style={styles.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  }

  const statusConfig = STATUS_CONFIG[job.status] || STATUS_CONFIG.assigned;
  const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera;
  const completedChecklist = job.checklist.filter(c => c.completed).length;
  const totalChecklist = job.checklist.length;
  const isUrgent = job.priority === 'urgent';
  const isHigh = job.priority === 'high';

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.topBar}>
        <TouchableOpacity testID="back-btn" onPress={() => router.back()} style={styles.backBtn}>
          <ArrowLeft size={20} color="#FFFFFF" strokeWidth={2.5} />
        </TouchableOpacity>
        <Text style={styles.topBarTitle}>Job Details</Text>
        <View style={[styles.statusBadge, { backgroundColor: statusConfig.bg }]}>
          <View style={[styles.statusDot, { backgroundColor: statusConfig.dot }]} />
          <Text style={[styles.statusText, { color: statusConfig.text }]}>{statusConfig.label}</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}>

        {/* Type & Priority */}
        <View style={styles.typeRow}>
          <View style={styles.typeWrap}>
            <View style={styles.typeIconBox}><TypeIcon size={14} color="#8B8FA3" strokeWidth={2} /></View>
            <Text style={styles.typeText}>{JOB_TYPE_LABELS[job.job_type] || job.job_type}</Text>
          </View>
          {(isUrgent || isHigh) && (
            <View style={[styles.priorityBadge, isUrgent ? styles.urgentBadge : styles.highBadge]}>
              {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
              <Text style={styles.priorityText}>{isUrgent ? 'Urgent' : 'High Priority'}</Text>
            </View>
          )}
        </View>

        {/* Address */}
        <View style={styles.addressSection}>
          <MapPin size={16} color="#3B82F6" strokeWidth={2.5} />
          <View style={styles.addressText}>
            <Text style={styles.address}>{job.address}</Text>
            <Text style={styles.cityState}>{job.city}, {job.state} {job.zip_code}</Text>
          </View>
        </View>

        {/* Time */}
        <View style={styles.timeSection}>
          <Clock size={14} color="#8B8FA3" strokeWidth={2} />
          <Text style={styles.timeText}>{job.time_window_start} – {job.time_window_end}</Text>
          <Text style={styles.dateText}>{job.scheduled_date}</Text>
        </View>

        {/* Property Details */}
        {job.property_details ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Property</Text>
            <Text style={styles.sectionBody}>{job.property_details}</Text>
          </View>
        ) : null}

        {/* Client Instructions */}
        {job.client_instructions ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Client Instructions</Text>
            <Text style={styles.sectionBody}>{job.client_instructions}</Text>
          </View>
        ) : null}

        {/* Checklist */}
        {job.checklist.length > 0 && (
          <View style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>Checklist</Text>
              <Text style={styles.checklistProgress}>{completedChecklist}/{totalChecklist}</Text>
            </View>
            <View style={styles.progressTrack}>
              <View style={[styles.progressFill, { width: totalChecklist > 0 ? `${(completedChecklist / totalChecklist) * 100}%` : '0%' }]} />
            </View>
            {job.checklist.map((item) => (
              <TouchableOpacity key={item.id} testID={`checklist-${item.id}`} style={styles.checklistItem}
                onPress={() => handleToggleChecklist(item.id, item.completed)}>
                {item.completed
                  ? <CheckSquare size={20} color="#3B82F6" strokeWidth={2.5} />
                  : <Square size={20} color="#3D4160" strokeWidth={1.8} />}
                <Text style={[styles.checklistLabel, item.completed && styles.checklistLabelDone]}>{item.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* Uploads */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Uploads</Text>
            <Text style={styles.checklistProgress}>{job.uploaded_count}/{job.deliverables_count}</Text>
          </View>
          {uploads.map((upload) => (
            <View key={upload.id} testID={`job-upload-${upload.id}`} style={styles.uploadItem}>
              <FileText size={14} color="#8B8FA3" strokeWidth={2} />
              <Text style={styles.uploadFilename} numberOfLines={1}>{upload.filename}</Text>
              <Text style={styles.uploadSize}>{formatSize(upload.file_size)}</Text>
            </View>
          ))}
          {job.status !== 'completed' && (
            <TouchableOpacity testID="job-upload-btn" style={styles.uploadBtn} onPress={handleUpload} disabled={uploading}>
              {uploading ? <ActivityIndicator size="small" color="#FFF" /> : (
                <><Upload size={14} color="#FFF" strokeWidth={2.5} /><Text style={styles.uploadBtnText}>Upload Media</Text></>
              )}
            </TouchableOpacity>
          )}
        </View>
      </ScrollView>

      {/* Action Button */}
      {job.status !== 'completed' && (
        <View style={styles.actionBar}>
          <TouchableOpacity testID="status-action-btn"
            style={[styles.actionBtn, job.status === 'assigned' ? styles.startBtn : styles.completeBtn]}
            onPress={handleStatusUpdate} disabled={actionLoading}>
            {actionLoading ? <ActivityIndicator size="small" color="#FFF" /> : (
              <Text style={styles.actionBtnText}>{statusConfig.actionLabel}</Text>
            )}
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  topBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  backBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center', backgroundColor: '#151722', borderRadius: 10, borderWidth: 1, borderColor: '#1E2030' },
  topBarTitle: { flex: 1, fontSize: 16, fontWeight: '600', color: '#FFFFFF', marginLeft: 12 },
  statusBadge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: { fontSize: 12, fontWeight: '600' },
  scrollContent: { paddingBottom: 120 },
  typeRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 20, paddingBottom: 8 },
  typeWrap: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  typeIconBox: { width: 30, height: 30, borderRadius: 8, backgroundColor: '#151722', alignItems: 'center', justifyContent: 'center' },
  typeText: { fontSize: 13, fontWeight: '500', color: '#8B8FA3' },
  priorityBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  urgentBadge: { backgroundColor: '#EF4444' },
  highBadge: { backgroundColor: '#F59E0B' },
  priorityText: { fontSize: 11, fontWeight: '600', color: '#FFFFFF' },
  addressSection: { flexDirection: 'row', paddingHorizontal: 20, paddingVertical: 12, gap: 12, alignItems: 'flex-start' },
  addressText: { flex: 1 },
  address: { fontSize: 22, fontWeight: '700', color: '#FFFFFF', letterSpacing: -0.3, marginBottom: 4 },
  cityState: { fontSize: 14, color: '#565B7E' },
  timeSection: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingBottom: 16, gap: 8, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  timeText: { fontSize: 15, fontWeight: '600', color: '#3B82F6' },
  dateText: { fontSize: 13, color: '#3D4160', marginLeft: 'auto' },
  section: { paddingHorizontal: 20, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: '#1E2030' },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  sectionTitle: { fontSize: 14, fontWeight: '600', color: '#8B8FA3', marginBottom: 8 },
  sectionBody: { fontSize: 14, lineHeight: 22, color: '#C4C7D8' },
  checklistProgress: { fontSize: 14, fontWeight: '700', color: '#3B82F6' },
  progressTrack: { height: 4, backgroundColor: '#1E2030', borderRadius: 2, marginBottom: 14 },
  progressFill: { height: '100%', borderRadius: 2, backgroundColor: '#3B82F6' },
  checklistItem: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#151722' },
  checklistLabel: { fontSize: 14, color: '#C4C7D8', flex: 1 },
  checklistLabelDone: { color: '#3D4160', textDecorationLine: 'line-through' },
  uploadItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#151722' },
  uploadFilename: { flex: 1, fontSize: 13, color: '#C4C7D8' },
  uploadSize: { fontSize: 12, color: '#3D4160' },
  uploadBtn: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: '#3B82F6', paddingVertical: 14, borderRadius: 12, marginTop: 12 },
  uploadBtnText: { fontSize: 14, fontWeight: '600', color: '#FFFFFF' },
  actionBar: { position: 'absolute', bottom: 0, left: 0, right: 0, paddingHorizontal: 16, paddingVertical: 16, paddingBottom: 32, backgroundColor: '#0C0D14', borderTopWidth: 1, borderTopColor: '#1E2030' },
  actionBtn: { paddingVertical: 16, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  startBtn: { backgroundColor: '#3B82F6' },
  completeBtn: { backgroundColor: '#22C55E' },
  actionBtnText: { fontSize: 16, fontWeight: '700', color: '#FFFFFF', letterSpacing: 0.3 },
});
