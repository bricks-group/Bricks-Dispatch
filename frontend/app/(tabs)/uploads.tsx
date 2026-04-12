import { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import {
  Upload,
  FileText,
  Image as ImageIcon,
  Film,
  Trash2,
  Plus,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type UploadRecord = {
  id: string;
  job_id: string;
  filename: string;
  file_size: number;
  mime_type: string;
  created_at: string;
};

type Job = {
  id: string;
  address: string;
  job_type: string;
};

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
      const [uploadsRes, jobsRes] = await Promise.all([
        fetch(`${API}/api/uploads`),
        fetch(`${API}/api/jobs`),
      ]);
      const uploadsData = await uploadsRes.json();
      const jobsData = await jobsRes.json();
      setUploads(uploadsData);
      setJobs(jobsData);
    } catch (e) {
      console.error('Failed to fetch uploads:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchData();
  }, [fetchData]);

  const getJobAddress = (jobId: string): string => {
    const job = jobs.find(j => j.id === jobId);
    return job ? job.address : 'Unknown Job';
  };

  const handlePickAndUpload = async () => {
    // Get active jobs (not completed)
    const activeJobs = jobs.filter(j => j.status !== 'completed');
    if (activeJobs.length === 0) {
      Alert.alert('No Active Jobs', 'There are no active jobs to upload files to.');
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.8,
      base64: true,
    });

    if (result.canceled) return;

    const asset = result.assets[0];
    const filename = asset.fileName || `upload_${Date.now()}.jpg`;
    const fileSize = asset.fileSize || 0;
    const mimeType = asset.mimeType || 'image/jpeg';

    // Use first active job for simplicity
    const targetJob = activeJobs[0];

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('job_id', targetJob.id);
      formData.append('filename', filename);
      formData.append('file_size', String(fileSize));
      formData.append('mime_type', mimeType);
      formData.append('file_data', asset.base64 ? asset.base64.substring(0, 200) : '');

      const res = await fetch(`${API}/api/uploads`, {
        method: 'POST',
        body: formData,
      });

      if (res.ok) {
        fetchData();
      } else {
        Alert.alert('Upload Failed', 'Could not upload the file.');
      }
    } catch (e) {
      console.error('Upload error:', e);
      Alert.alert('Upload Error', 'Something went wrong.');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (uploadId: string) => {
    Alert.alert('Delete Upload', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          try {
            await fetch(`${API}/api/uploads/${uploadId}`, { method: 'DELETE' });
            fetchData();
          } catch (e) {
            console.error('Delete error:', e);
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="large" color="#FACC15" />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>UPLOADS</Text>
          <Text style={styles.headerCount}>{uploads.length} FILES</Text>
        </View>
        <TouchableOpacity
          testID="upload-btn"
          style={styles.uploadBtn}
          onPress={handlePickAndUpload}
          disabled={uploading}
        >
          {uploading ? (
            <ActivityIndicator size="small" color="#000000" />
          ) : (
            <>
              <Plus size={18} color="#000000" strokeWidth={3} />
              <Text style={styles.uploadBtnText}>UPLOAD</Text>
            </>
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
              <View style={styles.uploadIcon}>
                <FileIcon size={20} color="#A1A1AA" strokeWidth={2} />
              </View>
              <View style={styles.uploadInfo}>
                <Text style={styles.uploadFilename} numberOfLines={1}>{item.filename}</Text>
                <Text style={styles.uploadMeta}>
                  {formatSize(item.file_size)} · {getJobAddress(item.job_id)}
                </Text>
                <Text style={styles.uploadDate}>{formatDate(item.created_at)}</Text>
              </View>
              <TouchableOpacity
                testID={`delete-upload-${item.id}`}
                style={styles.deleteBtn}
                onPress={() => handleDelete(item.id)}
              >
                <Trash2 size={16} color="#EF4444" strokeWidth={2} />
              </TouchableOpacity>
            </View>
          );
        }}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FACC15" colors={['#FACC15']} />
        }
        ListEmptyComponent={
          <View style={styles.emptyWrap}>
            <Upload size={40} color="#27272A" strokeWidth={1.5} />
            <Text style={styles.emptyText}>No uploads yet</Text>
            <Text style={styles.emptySubtext}>Tap + UPLOAD to add files</Text>
          </View>
        }
      />
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
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '900',
    color: '#FFFFFF',
    letterSpacing: 2,
  },
  headerCount: {
    fontSize: 12,
    fontWeight: '600',
    color: '#71717A',
    letterSpacing: 1,
    marginTop: 2,
  },
  uploadBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: '#FACC15',
    paddingHorizontal: 18,
    paddingVertical: 12,
  },
  uploadBtnText: {
    fontSize: 12,
    fontWeight: '800',
    color: '#000000',
    letterSpacing: 1,
  },
  list: {
    paddingHorizontal: 16,
    paddingBottom: 100,
  },
  uploadCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    padding: 14,
    marginBottom: 8,
  },
  uploadIcon: {
    width: 44,
    height: 44,
    backgroundColor: '#1A1A1A',
    borderWidth: 1,
    borderColor: '#27272A',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  uploadInfo: {
    flex: 1,
  },
  uploadFilename: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 2,
  },
  uploadMeta: {
    fontSize: 12,
    color: '#A1A1AA',
    marginBottom: 1,
  },
  uploadDate: {
    fontSize: 11,
    color: '#52525B',
  },
  deleteBtn: {
    width: 40,
    height: 40,
    alignItems: 'center',
    justifyContent: 'center',
  },
  emptyWrap: {
    paddingVertical: 80,
    alignItems: 'center',
    gap: 12,
  },
  emptyText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#71717A',
  },
  emptySubtext: {
    fontSize: 13,
    color: '#52525B',
  },
});
