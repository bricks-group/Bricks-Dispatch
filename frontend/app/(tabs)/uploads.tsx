import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, RefreshControl, Alert, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { Upload, FileText } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.219:8002';

type UploadRecord = {
  id: string; job_id: string; filename: string; file_size: number;
  mime_type: string; created_at: string;
};

type Job = {
  id: string; address: string; status: string;
  deliverables_count: number; uploaded_count: number;
};

type JobGroup = {
  job: Job;
  uploads: UploadRecord[];
};

function fmtBytes(bytes: number) {
  if (!bytes) return '—';
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function jobStatusPill(job: Job): { label: string; tone: 'ok' | 'warn' | 'brick' | 'neutral' } {
  if (job.status === 'completed') return { label: 'Delivered', tone: 'ok' };
  if (job.status === 'in_progress') return { label: 'In Progress', tone: 'brick' };
  return { label: 'Scheduled', tone: 'neutral' };
}

const PILL_COLORS: Record<string, { bg: string; fg: string }> = {
  ok:      { bg: TOKENS.ok + '33',      fg: '#B8CC9E' },
  warn:    { bg: TOKENS.warn + '33',    fg: '#E6B477' },
  brick:   { bg: TOKENS.brick + '33',   fg: TOKENS.brickSoft },
  neutral: { bg: TOKENS.nightDeep,      fg: TOKENS.moonSoft },
};

export default function UploadsScreen() {
  const [uploads, setUploads] = useState<UploadRecord[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [uploading, setUploading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const [upRes, jobRes] = await Promise.all([
        fetch(`${API}/api/uploads`, { headers }),
        fetch(`${API}/api/jobs`, { headers }),
      ]);
      if (!upRes.ok) throw new Error(`Uploads API error: ${upRes.status}`);
      if (!jobRes.ok) throw new Error(`Jobs API error: ${jobRes.status}`);
      setUploads(await upRes.json());
      setJobs(await jobRes.json());
    } catch (e) {
      console.error('Uploads fetch error', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const pickAndUpload = async () => {
    const activeJobs = jobs.filter(j => j.status === 'in_progress');
    if (activeJobs.length === 0) {
      Alert.alert('No active jobs', 'Start a job before uploading files.');
      return;
    }
    const targetJob = activeJobs[0];

    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      Alert.alert('Permission required', 'Allow photo library access to upload files.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images', 'videos'],
      quality: 0.8,
      base64: true,
      allowsEditing: false,
    });
    if (result.canceled || !result.assets?.length) return;

    const asset = result.assets[0];
    setUploading(true);
    try {
      const form = new FormData();
      form.append('job_id', targetJob.id);
      form.append('filename', asset.fileName || `upload_${Date.now()}.jpg`);
      form.append('file_size', String(asset.fileSize || 0));
      form.append('mime_type', asset.mimeType || 'image/jpeg');
      form.append('file_data', asset.base64 || '');
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/uploads`, {
        method: 'POST', headers, body: form,
      });
      if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
      await fetchData();
    } catch {
      Alert.alert('Upload failed', 'Network error — check your connection.');
    } finally {
      setUploading(false);
    }
  };

  // Group uploads by job
  const jobMap = new Map(jobs.map(j => [j.id, j]));
  const groupMap = new Map<string, JobGroup>();
  for (const u of uploads) {
    const job = jobMap.get(u.job_id);
    if (!job) continue;
    if (!groupMap.has(u.job_id)) groupMap.set(u.job_id, { job, uploads: [] });
    groupMap.get(u.job_id)!.uploads.push(u);
  }
  const groups = Array.from(groupMap.values());
  const inProgress = groups.filter(g => g.job.status === 'in_progress');
  const recent = groups.filter(g => g.job.status !== 'in_progress');

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      {loading ? (
        <View style={s.center}><ActivityIndicator size="small" color={TOKENS.brick} /></View>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchData(); }} tintColor={TOKENS.brick} />}
          contentContainerStyle={s.scroll}
        >
          <Text style={s.title}>Upload</Text>
          <Text style={s.subtitle}>Drop the card — we auto-sort by job.</Text>

          {/* Drop zone */}
          <TouchableOpacity style={s.dropZone} onPress={pickAndUpload} disabled={uploading} activeOpacity={0.8}>
            {uploading ? (
              <ActivityIndicator size="small" color={TOKENS.brick} />
            ) : (
              <>
                <Upload size={28} color={TOKENS.brick} strokeWidth={1.5} />
                <Text style={s.dropTitle}>Tap to select files</Text>
                <Text style={s.dropMeta}>RAW, JPEG, MP4 · up to 20 GB per job</Text>
                <View style={s.chooseBtn}>
                  <Text style={s.chooseBtnText}>CHOOSE FILES</Text>
                </View>
              </>
            )}
          </TouchableOpacity>

          {/* In progress */}
          {inProgress.length > 0 && (
            <View style={s.section}>
              <Text style={s.sectionLabel}>IN PROGRESS</Text>
              {inProgress.map(({ job, uploads: ups }) => {
                const totalBytes = ups.reduce((sum, u) => sum + u.file_size, 0);
                const pct = job.deliverables_count > 0
                  ? Math.round((job.uploaded_count / job.deliverables_count) * 100)
                  : null;
                return (
                  <View key={job.id} style={s.progressCard}>
                    <View style={s.progressTop}>
                      <View style={{ flex: 1 }}>
                        <Text style={s.progressAddr}>{job.address.toLowerCase()}</Text>
                        <Text style={s.progressMeta}>
                          {ups.length} FILES · {fmtBytes(totalBytes)}
                        </Text>
                      </View>
                      {pct !== null && <Text style={s.pct}>{pct}%</Text>}
                    </View>
                    {pct !== null && (
                      <View style={s.barTrack}>
                        <View style={[s.barFill, { width: `${pct}%` as any }]} />
                      </View>
                    )}
                  </View>
                );
              })}
            </View>
          )}

          {/* Recent uploads */}
          {recent.length > 0 && (
            <View style={s.section}>
              <Text style={s.sectionLabel}>RECENT UPLOADS</Text>
              <View style={s.card}>
                {recent.map(({ job, uploads: ups }, i) => {
                  const totalBytes = ups.reduce((sum, u) => sum + u.file_size, 0);
                  const pill = jobStatusPill(job);
                  const colors = PILL_COLORS[pill.tone];
                  return (
                    <View key={job.id} style={[s.recentRow, i < recent.length - 1 && s.rowBorder]}>
                      <FileText size={18} color={TOKENS.moonFaint} strokeWidth={1.5} />
                      <View style={s.recentInfo}>
                        <Text style={s.recentAddr}>{job.address.toLowerCase()}</Text>
                        <Text style={s.recentMeta}>
                          {ups.length} files · {fmtBytes(totalBytes)}
                        </Text>
                      </View>
                      <View style={[s.pill, { backgroundColor: colors.bg }]}>
                        <Text style={[s.pillText, { color: colors.fg }]}>{pill.label.toUpperCase()}</Text>
                      </View>
                    </View>
                  );
                })}
              </View>
            </View>
          )}

          {groups.length === 0 && (
            <View style={s.empty}>
              <Text style={s.emptyText}>No uploads yet</Text>
            </View>
          )}

          <View style={{ height: 120 }} />
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  scroll: { paddingHorizontal: 24, paddingTop: 16 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },

  title: { fontSize: 26, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.4, marginBottom: 4 },
  subtitle: { fontSize: 12, color: TOKENS.moonFaint, marginBottom: 20 },

  dropZone: { borderWidth: 1.5, borderColor: TOKENS.nightHairS, borderStyle: 'dashed', borderRadius: 2, paddingVertical: 36, paddingHorizontal: 20, alignItems: 'center', marginBottom: 28 },
  dropTitle: { fontSize: 15, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, marginTop: 12, marginBottom: 6 },
  dropMeta: { fontSize: 11, color: TOKENS.moonFaint, marginBottom: 14 },
  chooseBtn: { backgroundColor: TOKENS.brick, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 2 },
  chooseBtnText: { color: '#FFF', fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, letterSpacing: 1.2 },

  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 10 },

  progressCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, padding: 16, marginBottom: 10 },
  progressTop: { flexDirection: 'row', alignItems: 'flex-start', marginBottom: 12 },
  progressAddr: { fontSize: 14, color: TOKENS.moon, marginBottom: 4 },
  progressMeta: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint },
  pct: { fontFamily: TYPOGRAPHY.mono, fontSize: 16, color: TOKENS.brick },
  barTrack: { height: 3, backgroundColor: TOKENS.nightHair, borderRadius: 2, overflow: 'hidden' },
  barFill: { height: '100%', backgroundColor: TOKENS.brick, borderRadius: 2 },

  card: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  recentRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  recentInfo: { flex: 1 },
  recentAddr: { fontSize: 13, color: TOKENS.moon, marginBottom: 3 },
  recentMeta: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint },
  pill: { paddingHorizontal: 9, paddingVertical: 4, borderRadius: 2 },
  pillText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, letterSpacing: 1.2 },

  empty: { paddingTop: 40, alignItems: 'center' },
  emptyText: { fontSize: 13, color: TOKENS.moonFaint },
});
