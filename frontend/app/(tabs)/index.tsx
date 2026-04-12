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
  Circle,
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
  photo: 'Photo',
  video: 'Video',
  drone: 'Drone',
  '3d_tour': '3D Tour',
  floor_plan: 'Floor Plan',
};

const STATUS_CONFIG: Record<string, { bg: string; dot: string; text: string; label: string }> = {
  assigned: { bg: '#3B82F615', dot: '#3B82F6', text: '#60A5FA', label: 'Assigned' },
  in_progress: { bg: '#F59E0B15', dot: '#F59E0B', text: '#FBBF24', label: 'In Progress' },
  completed: { bg: '#22C55E15', dot: '#22C55E', text: '#4ADE80', label: 'Completed' },
};

const FILTERS = ['all', 'assigned', 'in_progress', 'completed'];
const FILTER_LABELS: Record<string, string> = {
  all: 'All',
  assigned: 'Assigned',
  in_progress: 'In Progress',
  completed: 'Completed',
};

function JobCard({ job, onPress }: { job: Job; onPress: () => void }) {
  const statusConfig = STATUS_CONFIG[job.status] || STATUS_CONFIG.assigned;
  const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera;
  const isUrgent = job.priority === 'urgent';
  const isHigh = job.priority === 'high';

  return (
    <TouchableOpacity
      testID={`job-card-${job.id}`}
      style={styles.card}
      onPress={onPress}
      activeOpacity={0.7}
    >
      {/* Top Row: Type + Priority + Status */}
      <View style={styles.cardTop}>
        <View style={styles.cardTypeRow}>
          <View style={styles.typeIconWrap}>
            <TypeIcon size={14} color="#8B8FA3" strokeWidth={2} />
          </View>
          <Text style={styles.typeLabel}>{JOB_TYPE_LABELS[job.job_type] || job.job_type}</Text>
          {(isUrgent || isHigh) && (
            <View style={[styles.priorityBadge, isUrgent ? styles.urgentBadge : styles.highBadge]}>
              {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
              <Text style={styles.priorityText}>{isUrgent ? 'Urgent' : 'High'}</Text>
            </View>
          )}
        </View>
        <View style={[styles.statusBadge, { backgroundColor: statusConfig.bg }]}>
          <View style={[styles.statusDot, { backgroundColor: statusConfig.dot }]} />
          <Text style={[styles.statusText, { color: statusConfig.text }]}>{statusConfig.label}</Text>
        </View>
      </View>

      {/* Address */}
      <Text style={styles.address} numberOfLines={1}>{job.address}</Text>
      <Text style={styles.cityState}>{job.city}, {job.state}</Text>

      {/* Footer */}
      <View style={styles.cardFooter}>
        <View style={styles.timeWrap}>
          <Text style={styles.timeWindow}>{job.time_window_start} – {job.time_window_end}</Text>
        </View>
        <View style={styles.progressWrap}>
          <View style={styles.progressTrack}>
            <View
              style={[
                styles.progressFill,
                {
                  width: job.deliverables_count > 0
                    ? `${(job.uploaded_count / job.deliverables_count) * 100}%`
                    : '0%',
                },
              ]}
            />
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
          <ActivityIndicator size="large" color="#3B82F6" />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Jobs</Text>
          <Text style={styles.headerSub}>{jobs.length} assignments</Text>
        </View>
      </View>

      {/* Filter Chips */}
      <View style={styles.filterRow}>
        {FILTERS.map((f) => (
          <TouchableOpacity
            key={f}
            testID={`filter-${f}`}
            style={[styles.filterBtn, filter === f && styles.filterBtnActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.filterText, filter === f && styles.filterTextActive]}>
              {FILTER_LABELS[f]}
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
            tintColor="#3B82F6"
            colors={['#3B82F6']}
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
    backgroundColor: '#0C0D14',
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
    paddingTop: 12,
    paddingBottom: 16,
  },
  headerTitle: {
    fontSize: 26,
    fontWeight: '700',
    color: '#FFFFFF',
    letterSpacing: -0.5,
  },
  headerSub: {
    fontSize: 13,
    color: '#565B7E',
    marginTop: 2,
  },
  filterRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    marginBottom: 12,
    gap: 8,
  },
  filterBtn: {
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 20,
    backgroundColor: '#151722',
    borderWidth: 1,
    borderColor: '#1E2030',
  },
  filterBtnActive: {
    backgroundColor: '#3B82F6',
    borderColor: '#3B82F6',
  },
  filterText: {
    fontSize: 13,
    fontWeight: '500',
    color: '#8B8FA3',
  },
  filterTextActive: {
    color: '#FFFFFF',
    fontWeight: '600',
  },
  list: {
    paddingHorizontal: 16,
    paddingTop: 4,
    paddingBottom: 100,
  },
  card: {
    backgroundColor: '#151722',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#1E2030',
    padding: 16,
    marginBottom: 10,
  },
  cardTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
  },
  cardTypeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  typeIconWrap: {
    width: 30,
    height: 30,
    borderRadius: 8,
    backgroundColor: '#1E2030',
    alignItems: 'center',
    justifyContent: 'center',
  },
  typeLabel: {
    fontSize: 13,
    fontWeight: '500',
    color: '#8B8FA3',
  },
  priorityBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  urgentBadge: {
    backgroundColor: '#EF4444',
  },
  highBadge: {
    backgroundColor: '#F59E0B',
  },
  priorityText: {
    fontSize: 11,
    fontWeight: '600',
    color: '#FFFFFF',
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
  statusText: {
    fontSize: 12,
    fontWeight: '600',
  },
  address: {
    fontSize: 17,
    fontWeight: '600',
    color: '#FFFFFF',
    marginBottom: 3,
  },
  cityState: {
    fontSize: 13,
    color: '#565B7E',
    marginBottom: 14,
  },
  cardFooter: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#1E2030',
  },
  timeWrap: {
    flex: 1,
  },
  timeWindow: {
    fontSize: 14,
    fontWeight: '600',
    color: '#3B82F6',
  },
  progressWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginRight: 12,
  },
  progressTrack: {
    width: 48,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#1E2030',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
    backgroundColor: '#3B82F6',
  },
  progressText: {
    fontSize: 12,
    fontWeight: '500',
    color: '#565B7E',
  },
  emptyWrap: {
    paddingVertical: 60,
    alignItems: 'center',
  },
  emptyText: {
    fontSize: 14,
    color: '#565B7E',
  },
});
