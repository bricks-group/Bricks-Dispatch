import { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import {
  ChevronLeft,
  ChevronRight,
  Camera,
  Video,
  Plane,
  Box,
  Ruler,
  AlertTriangle,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type Job = {
  id: string;
  address: string;
  city: string;
  time_window_start: string;
  time_window_end: string;
  job_type: string;
  status: string;
  priority: string;
  scheduled_date: string;
};

const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = {
  photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler,
};

const STATUS_COLORS: Record<string, string> = {
  assigned: '#3B82F6', in_progress: '#F59E0B', completed: '#22C55E',
};

function getDatesForWeek(baseDate: Date): string[] {
  const dates: string[] = [];
  const start = new Date(baseDate);
  start.setDate(start.getDate() - start.getDay());
  for (let i = 0; i < 7; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    dates.push(d.toISOString().split('T')[0]);
  }
  return dates;
}

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export default function ScheduleScreen() {
  const router = useRouter();
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().split('T')[0]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const weekDates = getDatesForWeek(currentDate);

  const fetchSchedule = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/schedule?date=${selectedDate}`);
      const data = await res.json();
      setJobs(data);
    } catch (e) {
      console.error('Failed to fetch schedule:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedDate]);

  useEffect(() => {
    setLoading(true);
    fetchSchedule();
  }, [fetchSchedule]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchSchedule();
  }, [fetchSchedule]);

  const today = new Date().toISOString().split('T')[0];

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Schedule</Text>
      </View>

      {/* Week Navigation */}
      <View style={styles.weekNav}>
        <TouchableOpacity testID="prev-week-btn" onPress={() => {
          const d = new Date(currentDate);
          d.setDate(d.getDate() - 7);
          setCurrentDate(d);
        }} style={styles.navBtn}>
          <ChevronLeft size={18} color="#8B8FA3" strokeWidth={2.5} />
        </TouchableOpacity>
        <Text style={styles.weekLabel}>
          {new Date(weekDates[0]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
          {' – '}
          {new Date(weekDates[6]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
        </Text>
        <TouchableOpacity testID="next-week-btn" onPress={() => {
          const d = new Date(currentDate);
          d.setDate(d.getDate() + 7);
          setCurrentDate(d);
        }} style={styles.navBtn}>
          <ChevronRight size={18} color="#8B8FA3" strokeWidth={2.5} />
        </TouchableOpacity>
      </View>

      {/* Day Selector */}
      <View style={styles.dayRow}>
        {weekDates.map((date, idx) => {
          const isSelected = date === selectedDate;
          const isToday = date === today;
          const dayNum = new Date(date + 'T12:00:00').getDate();
          return (
            <TouchableOpacity
              key={date}
              testID={`day-${date}`}
              style={[styles.dayBtn, isSelected && styles.dayBtnSelected]}
              onPress={() => setSelectedDate(date)}
            >
              <Text style={[styles.dayName, isSelected && styles.dayNameSelected]}>{DAY_NAMES[idx]}</Text>
              <Text style={[styles.dayNum, isSelected && styles.dayNumSelected, isToday && !isSelected && styles.dayNumToday]}>{dayNum}</Text>
              {isToday && <View style={[styles.todayDot, isSelected && styles.todayDotSelected]} />}
            </TouchableOpacity>
          );
        })}
      </View>

      {/* Timeline */}
      <ScrollView
        style={styles.timeline}
        contentContainerStyle={styles.timelineContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}
      >
        {loading ? (
          <View style={styles.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View>
        ) : jobs.length === 0 ? (
          <View style={styles.emptyWrap}>
            <Text style={styles.emptyText}>No jobs scheduled</Text>
            <Text style={styles.emptySubtext}>{selectedDate}</Text>
          </View>
        ) : (
          jobs.map((job, idx) => {
            const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera;
            const statusColor = STATUS_COLORS[job.status] || '#3B82F6';
            const isUrgent = job.priority === 'urgent';
            const isHigh = job.priority === 'high';
            return (
              <TouchableOpacity
                key={job.id}
                testID={`schedule-job-${job.id}`}
                style={styles.timelineItem}
                onPress={() => router.push(`/job/${job.id}`)}
                activeOpacity={0.7}
              >
                <View style={styles.timelineLeft}>
                  <View style={[styles.timelineDot, { backgroundColor: statusColor }]} />
                  {idx < jobs.length - 1 && <View style={styles.timelineLine} />}
                </View>
                <View style={styles.timelineCard}>
                  <View style={styles.timelineCardHeader}>
                    <Text style={styles.timelineTime}>{job.time_window_start} – {job.time_window_end}</Text>
                    {(isUrgent || isHigh) && (
                      <View style={[styles.priorityTag, isUrgent ? styles.urgentTag : styles.highTag]}>
                        {isUrgent && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
                        <Text style={styles.priorityTagText}>{isUrgent ? 'Urgent' : 'High'}</Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.timelineAddress} numberOfLines={1}>{job.address}</Text>
                  <View style={styles.timelineFooter}>
                    <TypeIcon size={14} color="#565B7E" strokeWidth={2} />
                    <Text style={styles.timelineType}>{job.job_type.replace('_', ' ')}</Text>
                    <View style={[styles.statusDotSmall, { backgroundColor: statusColor }]} />
                    <Text style={[styles.statusLabelSmall, { color: statusColor }]}>
                      {job.status.replace('_', ' ')}
                    </Text>
                  </View>
                </View>
              </TouchableOpacity>
            );
          })
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  headerTitle: { fontSize: 26, fontWeight: '700', color: '#FFFFFF', letterSpacing: -0.5 },
  weekNav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, marginBottom: 12 },
  navBtn: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', backgroundColor: '#151722', borderRadius: 10, borderWidth: 1, borderColor: '#1E2030' },
  weekLabel: { fontSize: 14, fontWeight: '600', color: '#8B8FA3' },
  dayRow: { flexDirection: 'row', paddingHorizontal: 12, marginBottom: 16, gap: 6 },
  dayBtn: { flex: 1, alignItems: 'center', paddingVertical: 10, backgroundColor: '#151722', borderRadius: 12, borderWidth: 1, borderColor: '#1E2030' },
  dayBtnSelected: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  dayName: { fontSize: 10, fontWeight: '500', color: '#565B7E', marginBottom: 4 },
  dayNameSelected: { color: '#FFFFFF' },
  dayNum: { fontSize: 16, fontWeight: '700', color: '#FFFFFF' },
  dayNumSelected: { color: '#FFFFFF' },
  dayNumToday: { color: '#3B82F6' },
  todayDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: '#3B82F6', marginTop: 4 },
  todayDotSelected: { backgroundColor: '#FFFFFF' },
  timeline: { flex: 1 },
  timelineContent: { paddingHorizontal: 16, paddingBottom: 100 },
  timelineItem: { flexDirection: 'row', marginBottom: 0 },
  timelineLeft: { width: 28, alignItems: 'center' },
  timelineDot: { width: 10, height: 10, borderRadius: 5, marginTop: 18 },
  timelineLine: { width: 2, flex: 1, backgroundColor: '#1E2030' },
  timelineCard: { flex: 1, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 14, marginLeft: 10, marginBottom: 8 },
  timelineCardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  timelineTime: { fontSize: 14, fontWeight: '600', color: '#3B82F6' },
  priorityTag: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  urgentTag: { backgroundColor: '#EF4444' },
  highTag: { backgroundColor: '#F59E0B' },
  priorityTagText: { fontSize: 11, fontWeight: '600', color: '#FFFFFF' },
  timelineAddress: { fontSize: 16, fontWeight: '600', color: '#FFFFFF', marginBottom: 8 },
  timelineFooter: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  timelineType: { fontSize: 12, fontWeight: '500', color: '#565B7E', textTransform: 'capitalize', marginRight: 8 },
  statusDotSmall: { width: 6, height: 6, borderRadius: 3 },
  statusLabelSmall: { fontSize: 12, fontWeight: '600', textTransform: 'capitalize' },
  loadingWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyText: { fontSize: 14, color: '#565B7E', marginBottom: 4 },
  emptySubtext: { fontSize: 12, color: '#3D4160' },
});
