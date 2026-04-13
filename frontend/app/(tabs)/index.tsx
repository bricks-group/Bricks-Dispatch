import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator, RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Camera, Video, Plane, Box, Ruler, ChevronRight, AlertTriangle, Bell } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type Job = {
  id: string; address: string; city: string; state: string;
  time_window_start: string; time_window_end: string; job_type: string;
  status: string; priority: string; scheduled_date: string;
  deliverables_count: number; uploaded_count: number;
};

const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = {
  photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler,
};
const JOB_TYPE_LABELS: Record<string, string> = {
  photo: 'Photo', video: 'Video', drone: 'Drone', '3d_tour': '3D Tour', floor_plan: 'Floor Plan',
};
const STATUS_CONFIG: Record<string, { bg: string; dot: string; text: string; label: string }> = {
  assigned: { bg: '#3B82F615', dot: '#3B82F6', text: '#60A5FA', label: 'Assigned' },
  in_progress: { bg: '#F59E0B15', dot: '#F59E0B', text: '#FBBF24', label: 'In Progress' },
  completed: { bg: '#22C55E15', dot: '#22C55E', text: '#4ADE80', label: 'Completed' },
};
const FILTERS = ['all', 'assigned', 'in_progress', 'completed'];
const FILTER_LABELS: Record<string, string> = { all: 'All', assigned: 'Assigned', in_progress: 'In Progress', completed: 'Completed' };

function JobCard({ job, onPress }: { job: Job; onPress: () => void }) {
  const sc = STATUS_CONFIG[job.status] || STATUS_CONFIG.assigned;
  const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera;
  const isUrgent = job.priority === 'urgent';
  const isHigh = job.priority === 'high';

  return (
    <TouchableOpacity testID={`job-card-${job.id}`} style={styles.card} onPress={onPress} activeOpacity={0.7}>
      <View style={styles.cardTop}>
        <View style={styles.cardTypeRow}>
          <View style={styles.typeIconWrap}><TypeIcon size={14} color="#8B8FA3" strokeWidth={2} /></View>
          <Text style={styles.typeLabel}>{JOB_TYPE_LABELS[job.job_type] || job.job_type}</Text>
          {(isUrgent || isHigh) && (
            <View style={[styles.priorityBadge, isUrgent ? styles.urgentBadge : styles.highBadge]}>
              {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
              <Text style={styles.priorityText}>{isUrgent ? 'Urgent' : 'High'}</Text>
            </View>
          )}
        </View>
        <View style={[styles.statusBadge, { backgroundColor: sc.bg }]}>
          <View style={[styles.statusDot, { backgroundColor: sc.dot }]} />
          <Text style={[styles.statusText, { color: sc.text }]}>{sc.label}</Text>
        </View>
      </View>
      <Text style={styles.address} numberOfLines={1}>{job.address}</Text>
      <Text style={styles.cityState}>{job.city}, {job.state}</Text>
      <View style={styles.cardFooter}>
        <Text style={styles.timeWindow}>{job.time_window_start} – {job.time_window_end}</Text>
        <View style={styles.progressWrap}>
          <View style={styles.progressTrack}>
            <View style={[styles.progressFill, { width: job.deliverables_count > 0 ? `${(job.uploaded_count / job.deliverables_count) * 100}%` : '0%' }]} />
          </View>
          <Text style={styles.progressText}>{job.uploaded_count}/{job.deliverables_count}</Text>
        </View>
        <ChevronRight size={16} color="#565B7E" strokeWidth={2} />
      </View>
    </TouchableOpacity>
  );
}

export default function JobsScreen() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState('all');
  const [unreadCount, setUnreadCount] = useState(0);

  const fetchJobs = useCallback(async () => {
    try {
      const url = filter === 'all' ? `${API}/api/jobs` : `${API}/api/jobs?status=${filter}`;
      const data = await (await fetch(url)).json();
      setJobs(data);
    } catch (e) { console.error('Failed to fetch jobs:', e); }
    finally { setLoading(false); setRefreshing(false); }
  }, [filter]);

  const fetchUnread = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/notifications/unread-count`);
      const data = await res.json();
      setUnreadCount(data.count || 0);
    } catch (e) {}
  }, []);

  useEffect(() => { setLoading(true); fetchJobs(); fetchUnread(); }, [fetchJobs, fetchUnread]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchJobs(); fetchUnread(); }, [fetchJobs, fetchUnread]);

  if (loading) return <SafeAreaView style={styles.container}><View style={styles.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <View style={styles.brandRow}>
          <View style={styles.wordmark}>
            <View style={styles.wordmarkAccent} />
            <Text style={styles.wordmarkText}>BRICKS</Text>
          </View>
          <Text style={styles.wordmarkSub}>DISPATCH</Text>
        </View>
        <TouchableOpacity testID="notifications-btn" style={styles.bellBtn}>
          <Bell size={20} color="#8B8FA3" strokeWidth={2} />
          {unreadCount > 0 && (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{unreadCount > 9 ? '9+' : unreadCount}</Text>
            </View>
          )}
        </TouchableOpacity>
      </View>
      <View style={styles.subHeader}>
        <Text style={styles.headerSub}>{jobs.length} assignments</Text>
      </View>
      <View style={styles.filterRow}>
        {FILTERS.map((f) => (
          <TouchableOpacity key={f} testID={`filter-${f}`} style={[styles.filterBtn, filter === f && styles.filterBtnActive]} onPress={() => setFilter(f)}>
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>{FILTER_LABELS[f]}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <FlatList data={jobs} keyExtractor={(item) => item.id}
        renderItem={({ item }) => <JobCard job={item} onPress={() => router.push(`/job/${item.id}`)} />}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}
        ListEmptyComponent={<View style={styles.emptyWrap}><Text style={styles.emptyText}>No jobs found</Text></View>}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 20, paddingTop: 12, paddingBottom: 4 },
  brandRow: { flexDirection: 'row', alignItems: 'baseline', gap: 6 },
  wordmark: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  wordmarkAccent: { width: 4, height: 22, borderRadius: 2, backgroundColor: '#3B82F6' },
  wordmarkText: { fontSize: 22, fontFamily: 'Outfit_700Bold', color: '#FFFFFF', letterSpacing: 3 },
  wordmarkSub: { fontSize: 12, fontFamily: 'Manrope_400Regular', color: '#565B7E', letterSpacing: 2 },
  bellBtn: { width: 44, height: 44, borderRadius: 12, backgroundColor: '#151722', borderWidth: 1, borderColor: '#1E2030', alignItems: 'center', justifyContent: 'center', position: 'relative' },
  badge: { position: 'absolute', top: -4, right: -4, minWidth: 18, height: 18, borderRadius: 9, backgroundColor: '#EF4444', alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4 },
  badgeText: { fontSize: 10, fontFamily: 'Manrope_600SemiBold', color: '#FFFFFF' },
  subHeader: { paddingHorizontal: 20, paddingBottom: 12 },
  headerSub: { fontSize: 13, fontFamily: 'Manrope_400Regular', color: '#565B7E' },
  filterRow: { flexDirection: 'row', paddingHorizontal: 16, marginBottom: 12, gap: 8 },
  filterBtn: { paddingVertical: 8, paddingHorizontal: 16, borderRadius: 20, backgroundColor: '#151722', borderWidth: 1, borderColor: '#1E2030' },
  filterBtnActive: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  filterText: { fontSize: 13, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  filterTextActive: { color: '#FFFFFF' },
  list: { paddingHorizontal: 16, paddingTop: 4, paddingBottom: 100 },
  card: { backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 16, marginBottom: 10 },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  cardTypeRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  typeIconWrap: { width: 30, height: 30, borderRadius: 8, backgroundColor: '#1E2030', alignItems: 'center', justifyContent: 'center' },
  typeLabel: { fontSize: 13, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  priorityBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  urgentBadge: { backgroundColor: '#EF4444' },
  highBadge: { backgroundColor: '#F59E0B' },
  priorityText: { fontSize: 11, fontFamily: 'Manrope_600SemiBold', color: '#FFFFFF' },
  statusBadge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 8 },
  statusDot: { width: 6, height: 6, borderRadius: 3 },
  statusText: { fontSize: 12, fontFamily: 'Manrope_600SemiBold' },
  address: { fontSize: 17, fontFamily: 'Outfit_600SemiBold', color: '#FFFFFF', marginBottom: 3 },
  cityState: { fontSize: 13, fontFamily: 'Manrope_400Regular', color: '#565B7E', marginBottom: 14 },
  cardFooter: { flexDirection: 'row', alignItems: 'center', paddingTop: 12, borderTopWidth: 1, borderTopColor: '#1E2030' },
  timeWindow: { fontSize: 14, fontFamily: 'JetBrainsMono_500Medium', color: '#3B82F6', flex: 1 },
  progressWrap: { flexDirection: 'row', alignItems: 'center', gap: 8, marginRight: 12 },
  progressTrack: { width: 48, height: 4, borderRadius: 2, backgroundColor: '#1E2030', overflow: 'hidden' },
  progressFill: { height: '100%', borderRadius: 2, backgroundColor: '#3B82F6' },
  progressText: { fontSize: 12, fontFamily: 'JetBrainsMono_400Regular', color: '#565B7E' },
  emptyWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyText: { fontSize: 14, fontFamily: 'Manrope_400Regular', color: '#565B7E' },
});
