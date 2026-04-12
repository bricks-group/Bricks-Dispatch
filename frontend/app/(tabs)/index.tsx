import { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import {
  Camera,
  Video,
  Plane,
  Box,
  Ruler,
  ChevronRight,
  AlertTriangle,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type Job = {
  id: string;
  address: string;
  city: string;
  state: string;
  time_window_start: string;
  time_window_end: string;
  job_type: string;
  status: string;
  priority: string;
  scheduled_date: string;
  deliverables_count: number;
  uploaded_count: number;
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

const STATUS_CONFIG: Record<string, { bg: string; text: string; label: string }> = {
  assigned: { bg: '#1D4ED840', text: '#60A5FA', label: 'ASSIGNED' },
  in_progress: { bg: '#CA8A0440', text: '#FDE047', label: 'IN PROGRESS' },
  completed: { bg: '#15803D40', text: '#4ADE80', label: 'COMPLETED' },
};

const FILTERS = ['all', 'assigned', 'in_progress', 'completed'];

function JobCard({ job, onPress }: { job: Job; onPress: () => void }) {
  const statusConfig = STATUS_CONFIG[job.status] || STATUS_CONFIG.assigned;
  const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera;
  const isUrgent = job.priority === 'urgent';
  const isHigh = job.priority === 'high';

  return (
    <TouchableOpacity
      testID={`job-card-${job.id}`}
      style={[
        styles.card,
        isUrgent && styles.cardUrgent,
      ]}
      onPress={onPress}
      activeOpacity={0.7}
    >
      <View style={styles.cardHeader}>
        <View style={styles.cardTypeRow}>
          <View style={styles.typeIconWrap}>
            <TypeIcon size={16} color="#A1A1AA" strokeWidth={2.5} />
          </View>
          <Text style={styles.typeLabel}>{JOB_TYPE_LABELS[job.job_type] || job.job_type.toUpperCase()}</Text>
          {(isUrgent || isHigh) && (
            <View style={[styles.priorityBadge, isUrgent ? styles.urgentBadge : styles.highBadge]}>
              {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={3} />}
              <Text style={styles.priorityText}>{isUrgent ? 'URGENT' : 'HIGH'}</Text>
            </View>
          )}
        </View>
        <View style={[styles.statusBadge, { backgroundColor: statusConfig.bg }]}>
          <Text style={[styles.statusText, { color: statusConfig.text }]}>{statusConfig.label}</Text>
        </View>
      </View>

      <Text style={styles.address} numberOfLines={1}>{job.address}</Text>
      <Text style={styles.cityState}>{job.city}, {job.state}</Text>

      <View style={styles.cardFooter}>
        <Text style={styles.timeWindow}>{job.time_window_start} – {job.time_window_end}</Text>
        <View style={styles.deliverables}>
          <Text style={styles.deliverablesText}>
            {job.uploaded_count}/{job.deliverables_count}
          </Text>
        </View>
        <ChevronRight size={18} color="#71717A" strokeWidth={2} />
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

  const fetchJobs = useCallback(async () => {
    try {
      const url = filter === 'all' ? `${API}/api/jobs` : `${API}/api/jobs?status=${filter}`;
      const res = await fetch(url);
      const data = await res.json();
      setJobs(data);
    } catch (e) {
      console.error('Failed to fetch jobs:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [filter]);

  useEffect(() => {
    setLoading(true);
    fetchJobs();
  }, [fetchJobs]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchJobs();
  }, [fetchJobs]);

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
        <Text style={styles.headerTitle}>JOBS</Text>
        <Text style={styles.headerCount}>{jobs.length}</Text>
      </View>

      <View style={styles.filterRow}>
        {FILTERS.map((f) => (
          <TouchableOpacity
            key={f}
            testID={`filter-${f}`}
            style={[styles.filterBtn, filter === f && styles.filterBtnActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>
              {f === 'all' ? 'ALL' : f.replace('_', ' ').toUpperCase()}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <FlatList
        data={jobs}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <JobCard
            job={item}
            onPress={() => router.push(`/job/${item.id}`)}
          />
        )}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor="#FACC15"
            colors={['#FACC15']}
          />
        }
        ListEmptyComponent={
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyText}>No jobs found</Text>
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
    fontSize: 28,
    fontWeight: '900',
    color: '#FACC15',
  },
  filterRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    marginBottom: 8,
    gap: 8,
  },
  filterBtn: {
    paddingVertical: 8,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderColor: '#27272A',
    backgroundColor: '#121212',
  },
  filterBtnActive: {
    backgroundColor: '#FACC15',
    borderColor: '#FACC15',
  },
  filterText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#A1A1AA',
    letterSpacing: 1,
  },
  filterTextActive: {
    color: '#000000',
  },
  list: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 100,
  },
  card: {
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    padding: 16,
    marginBottom: 12,
  },
  cardUrgent: {
    borderColor: '#EF444480',
    borderLeftWidth: 3,
    borderLeftColor: '#EF4444',
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
  },
  cardTypeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  typeIconWrap: {
    width: 28,
    height: 28,
    backgroundColor: '#1A1A1A',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: '#27272A',
  },
  typeLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: '#A1A1AA',
    letterSpacing: 1.5,
  },
  priorityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
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
  statusBadge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  statusText: {
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.5,
  },
  address: {
    fontSize: 18,
    fontWeight: '700',
    color: '#FFFFFF',
    marginBottom: 2,
  },
  cityState: {
    fontSize: 13,
    color: '#71717A',
    marginBottom: 12,
  },
  cardFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: '#1A1A1A',
    paddingTop: 10,
  },
  timeWindow: {
    fontSize: 14,
    fontWeight: '600',
    color: '#FACC15',
    flex: 1,
  },
  deliverables: {
    marginRight: 12,
  },
  deliverablesText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#A1A1AA',
  },
  emptyWrap: {
    paddingVertical: 60,
    alignItems: 'center',
  },
  emptyText: {
    fontSize: 14,
    color: '#71717A',
  },
});
