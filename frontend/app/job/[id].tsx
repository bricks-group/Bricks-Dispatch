import { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  RefreshControl,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import {
  ArrowLeft,
  Camera,
  Video,
  Plane,
  Box,
  Ruler,
  MapPin,
  Clock,
  CheckSquare,
  Square,
  AlertTriangle,
  Upload,
  FileText,
  ChevronRight,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type ChecklistItem = {
  id: string;
  label: string;
  completed: boolean;
};

type Job = {
  id: string;
  address: string;
  city: string;
  state: string;
  zip_code: string;
  time_window_start: string;
  time_window_end: string;
  job_type: string;
  status: string;
  priority: string;
  property_details: string;
  client_instructions: string;
  checklist: ChecklistItem[];
  deliverables_count: number;
  uploaded_count: number;
  scheduled_date: string;
};

type UploadRecord = {
  id: string;
  filename: string;
  file_size: number;
  mime_type: string;
  created_at: string;
};

const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = {
  photo: Camera,
  video: Video,
  drone: Plane,
  '3d_tour': Box,
  floor_plan: Ruler,
};

const JOB_TYPE_LABELS: Record<string, string> = {
  photo: 'PHOTO',
  video: 'VIDEO',
  drone: 'DRONE',
  '3d_tour': '3D TOUR',
  floor_plan: 'FLOOR PLAN',
};

const STATUS_CONFIG: Record<string, { bg: string; text: string; label: string; actionLabel: string }> = {
  assigned: { bg: '#1D4ED840', text: '#60A5FA', label: 'ASSIGNED', actionLabel: 'START JOB' },
  in_progress: { bg: '#CA8A0440', text: '#FDE047', label: 'IN PROGRESS', actionLabel: 'COMPLETE JOB' },
  completed: { bg: '#15803D40', text: '#4ADE80', label: 'COMPLETED', actionLabel: '' },
};

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
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
        fetch(`${API}/api/jobs/${id}`),
        fetch(`${API}/api/uploads?job_id=${id}`),
      ]);
      const jobData = await jobRes.json();
      const uploadsData = await uploadsRes.json();
      setJob(jobData);
      setUploads(uploadsData);
    } catch (e) {
      console.error('Failed to fetch job:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchData();
  }, [fetchData]);

  const handleStatusUpdate = async () => {
    if (!job || actionLoading) return;
    const nextStatus = job.status === 'assigned' ? 'in_progress' : 'completed';
    setActionLoading(true);
    try {
      const res = await fetch(`${API}/api/jobs/${id}/status`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: nextStatus }),
      });
      if (res.ok) {
        const updated = await res.json();
        setJob(updated);
      } else {
        Alert.alert('Error', 'Could not update job status');
      }
    } catch (e) {
      Alert.alert('Error', 'Network error');
    } finally {
      setActionLoading(false);
    }
  };

  const handleToggleChecklist = async (itemId: string, completed: boolean) => {
    if (!job) return;
    // Optimistic update
    setJob({
      ...job,
      checklist: job.checklist.map(item =>
        item.id === itemId ? { ...item, completed: !completed } : item
      ),
    });
    try {
      await fetch(`${API}/api/jobs/${id}/checklist/${itemId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ completed: !completed }),
      });
    } catch (e) {
      // Revert on error
      setJob({
        ...job,
        checklist: job.checklist.map(item =>
          item.id === itemId ? { ...item, completed } : item
        ),
      });
    }
  };

  const handleUpload = async () => {
    if (!job) return;
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.8,
      base64: true,
    });
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

      const res = await fetch(`${API}/api/uploads`, {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        fetchData();
      }
    } catch (e) {
      console.error('Upload error:', e);
    } finally {
      setUploading(false);
    }
  };

  if (loading || !job) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="large" color="#FACC15" />
        </View>
      </SafeAreaView>
    );
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
          <ArrowLeft size={22} color="#FFFFFF" strokeWidth={2.5} />
        </TouchableOpacity>
        <View style={[styles.statusBadge, { backgroundColor: statusConfig.bg }]}>
          <Text style={[styles.statusText, { color: statusConfig.text }]}>{statusConfig.label}</Text>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FACC15" colors={['#FACC15']} />
        }
      >
        {/* Job Type & Priority */}
        <View style={styles.typeRow}>
          <View style={styles.typeWrap}>
            <TypeIcon size={16} color="#A1A1AA" strokeWidth={2.5} />
            <Text style={styles.typeText}>{JOB_TYPE_LABELS[job.job_type] || job.job_type.toUpperCase()}</Text>
          </View>
          {(isUrgent || isHigh) && (
            <View style={[styles.priorityBadge, isUrgent ? styles.urgentBadge : styles.highBadge]}>
              {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={3} />}
              <Text style={styles.priorityText}>{isUrgent ? 'URGENT' : 'HIGH PRIORITY'}</Text>
            </View>
          )}
        </View>

        {/* Address */}
        <View style={styles.addressSection}>
          <MapPin size={18} color="#FACC15" strokeWidth={2.5} />
          <View style={styles.addressText}>
            <Text style={styles.address}>{job.address}</Text>
            <Text style={styles.cityState}>{job.city}, {job.state} {job.zip_code}</Text>
          </View>
        </View>

        {/* Time */}
        <View style={styles.timeSection}>
          <Clock size={16} color="#A1A1AA" strokeWidth={2} />
          <Text style={styles.timeText}>{job.time_window_start} – {job.time_window_end}</Text>
          <Text style={styles.dateText}>{job.scheduled_date}</Text>
        </View>

        {/* Property Details */}
        {job.property_details ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>PROPERTY</Text>
            <Text style={styles.sectionBody}>{job.property_details}</Text>
          </View>
        ) : null}

        {/* Client Instructions */}
        {job.client_instructions ? (
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>CLIENT INSTRUCTIONS</Text>
            <Text style={styles.sectionBody}>{job.client_instructions}</Text>
          </View>
        ) : null}

        {/* Checklist */}
        {job.checklist.length > 0 && (
          <View style={styles.section}>
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>CHECKLIST</Text>
              <Text style={styles.checklistProgress}>{completedChecklist}/{totalChecklist}</Text>
            </View>
            {/* Progress Bar */}
            <View style={styles.progressTrack}>
              <View
                style={[
                  styles.progressFill,
                  { width: totalChecklist > 0 ? `${(completedChecklist / totalChecklist) * 100}%` : '0%' },
                ]}
              />
            </View>
            {job.checklist.map((item) => (
              <TouchableOpacity
                key={item.id}
                testID={`checklist-${item.id}`}
                style={styles.checklistItem}
                onPress={() => handleToggleChecklist(item.id, item.completed)}
              >
                {item.completed ? (
                  <CheckSquare size={20} color="#FACC15" strokeWidth={2.5} />
                ) : (
                  <Square size={20} color="#52525B" strokeWidth={2} />
                )}
                <Text style={[styles.checklistLabel, item.completed && styles.checklistLabelDone]}>
                  {item.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        {/* Uploads Section */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>UPLOADS</Text>
            <Text style={styles.checklistProgress}>{job.uploaded_count}/{job.deliverables_count}</Text>
          </View>

          {uploads.map((upload) => (
            <View key={upload.id} testID={`job-upload-${upload.id}`} style={styles.uploadItem}>
              <FileText size={16} color="#A1A1AA" strokeWidth={2} />
              <Text style={styles.uploadFilename} numberOfLines={1}>{upload.filename}</Text>
              <Text style={styles.uploadSize}>{formatSize(upload.file_size)}</Text>
            </View>
          ))}

          {job.status !== 'completed' && (
            <TouchableOpacity
              testID="job-upload-btn"
              style={styles.uploadBtn}
              onPress={handleUpload}
              disabled={uploading}
            >
              {uploading ? (
                <ActivityIndicator size="small" color="#000" />
              ) : (
                <>
                  <Upload size={16} color="#000" strokeWidth={2.5} />
                  <Text style={styles.uploadBtnText}>UPLOAD MEDIA</Text>
                </>
              )}
            </TouchableOpacity>
          )}
        </View>
      </ScrollView>

      {/* Action Button */}
      {job.status !== 'completed' && (
        <View style={styles.actionBar}>
          <TouchableOpacity
            testID="status-action-btn"
            style={[
              styles.actionBtn,
              job.status === 'assigned' ? styles.startBtn : styles.completeBtn,
            ]}
            onPress={handleStatusUpdate}
            disabled={actionLoading}
          >
            {actionLoading ? (
              <ActivityIndicator size="small" color={job.status === 'assigned' ? '#000' : '#FFF'} />
            ) : (
              <Text style={[
                styles.actionBtnText,
                job.status === 'assigned' ? styles.startBtnText : styles.completeBtnText,
              ]}>
                {statusConfig.actionLabel}
              </Text>
            )}
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050505',
  },
  loadingWrap: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1A1A1A',
  },
  backBtn: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
  },
  statusBadge: {
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  statusText: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.5,
  },
  scrollContent: {
    paddingBottom: 120,
  },
  typeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: 8,
  },
  typeWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  typeText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#A1A1AA',
    letterSpacing: 1.5,
  },
  priorityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  urgentBadge: {
    backgroundColor: '#EF4444',
  },
  highBadge: {
    backgroundColor: '#F59E0B',
  },
  priorityText: {
    fontSize: 9,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 1,
  },
  addressSection: {
    flexDirection: 'row',
    paddingHorizontal: 20,
    paddingVertical: 12,
    gap: 12,
    alignItems: 'flex-start',
  },
  addressText: {
    flex: 1,
  },
  address: {
    fontSize: 22,
    fontWeight: '800',
    color: '#FFFFFF',
    marginBottom: 4,
  },
  cityState: {
    fontSize: 14,
    color: '#71717A',
  },
  timeSection: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingBottom: 16,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#1A1A1A',
  },
  timeText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#FACC15',
  },
  dateText: {
    fontSize: 13,
    color: '#52525B',
    marginLeft: 'auto',
  },
  section: {
    paddingHorizontal: 20,
    paddingVertical: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#1A1A1A',
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '800',
    color: '#71717A',
    letterSpacing: 2,
    marginBottom: 8,
  },
  sectionBody: {
    fontSize: 14,
    lineHeight: 22,
    color: '#D4D4D8',
  },
  checklistProgress: {
    fontSize: 14,
    fontWeight: '700',
    color: '#FACC15',
  },
  progressTrack: {
    height: 4,
    backgroundColor: '#27272A',
    marginBottom: 14,
  },
  progressFill: {
    height: '100%',
    backgroundColor: '#FACC15',
  },
  checklistItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#121212',
  },
  checklistLabel: {
    fontSize: 14,
    color: '#D4D4D8',
    flex: 1,
  },
  checklistLabelDone: {
    color: '#52525B',
    textDecorationLine: 'line-through',
  },
  uploadItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#121212',
  },
  uploadFilename: {
    flex: 1,
    fontSize: 13,
    color: '#D4D4D8',
  },
  uploadSize: {
    fontSize: 12,
    color: '#52525B',
  },
  uploadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: '#FACC15',
    paddingVertical: 14,
    marginTop: 12,
  },
  uploadBtnText: {
    fontSize: 13,
    fontWeight: '800',
    color: '#000',
    letterSpacing: 1,
  },
  actionBar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingHorizontal: 16,
    paddingVertical: 16,
    paddingBottom: 32,
    backgroundColor: '#050505',
    borderTopWidth: 1,
    borderTopColor: '#1A1A1A',
  },
  actionBtn: {
    paddingVertical: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  startBtn: {
    backgroundColor: '#FACC15',
  },
  completeBtn: {
    backgroundColor: '#22C55E',
  },
  actionBtnText: {
    fontSize: 16,
    fontWeight: '900',
    letterSpacing: 2,
  },
  startBtnText: {
    color: '#000000',
  },
  completeBtnText: {
    color: '#FFFFFF',
  },
});
