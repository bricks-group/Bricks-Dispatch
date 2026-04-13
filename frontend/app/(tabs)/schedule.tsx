import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight, Camera, Video, Plane, Box, Ruler, AlertTriangle } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
type Job = { id: string; address: string; city: string; time_window_start: string; time_window_end: string; job_type: string; status: string; priority: string; scheduled_date: string };
const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = { photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler };
const STATUS_COLORS: Record<string, string> = { assigned: '#3B82F6', in_progress: '#F59E0B', completed: '#22C55E' };
function getDatesForWeek(baseDate: Date): string[] { const dates: string[] = []; const start = new Date(baseDate); start.setDate(start.getDate() - start.getDay()); for (let i = 0; i < 7; i++) { const d = new Date(start); d.setDate(d.getDate() + i); dates.push(d.toISOString().split('T')[0]); } return dates; }
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
    try { setJobs(await (await fetch(`${API}/api/schedule?date=${selectedDate}`)).json()); }
    catch (e) { console.error('Fetch error:', e); } finally { setLoading(false); setRefreshing(false); }
  }, [selectedDate]);
  useEffect(() => { setLoading(true); fetchSchedule(); }, [fetchSchedule]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchSchedule(); }, [fetchSchedule]);
  const today = new Date().toISOString().split('T')[0];

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <View style={s.header}><Text style={s.headerTitle}>Schedule</Text></View>
      <View style={s.weekNav}>
        <TouchableOpacity testID="prev-week-btn" onPress={() => { const d = new Date(currentDate); d.setDate(d.getDate() - 7); setCurrentDate(d); }} style={s.navBtn}><ChevronLeft size={18} color="#8B8FA3" strokeWidth={2.5} /></TouchableOpacity>
        <Text style={s.weekLabel}>{new Date(weekDates[0]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – {new Date(weekDates[6]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</Text>
        <TouchableOpacity testID="next-week-btn" onPress={() => { const d = new Date(currentDate); d.setDate(d.getDate() + 7); setCurrentDate(d); }} style={s.navBtn}><ChevronRight size={18} color="#8B8FA3" strokeWidth={2.5} /></TouchableOpacity>
      </View>
      <View style={s.dayRow}>
        {weekDates.map((date, idx) => {
          const isSelected = date === selectedDate; const isToday = date === today;
          return (
            <TouchableOpacity key={date} testID={`day-${date}`} style={[s.dayBtn, isSelected && s.dayBtnSel]} onPress={() => setSelectedDate(date)}>
              <Text style={[s.dayName, isSelected && s.dayNameSel]}>{DAY_NAMES[idx]}</Text>
              <Text style={[s.dayNum, isSelected && s.dayNumSel, isToday && !isSelected && s.dayNumToday]}>{new Date(date + 'T12:00:00').getDate()}</Text>
              {isToday && <View style={[s.todayDot, isSelected && s.todayDotSel]} />}
            </TouchableOpacity>
          );
        })}
      </View>
      <ScrollView style={s.timeline} contentContainerStyle={s.timelineContent} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}>
        {loading ? <View style={s.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View>
        : jobs.length === 0 ? <View style={s.emptyWrap}><Text style={s.emptyText}>No jobs scheduled</Text><Text style={s.emptySub}>{selectedDate}</Text></View>
        : jobs.map((job, idx) => {
          const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera; const color = STATUS_COLORS[job.status] || '#3B82F6';
          return (
            <TouchableOpacity key={job.id} testID={`schedule-job-${job.id}`} style={s.tlItem} onPress={() => router.push(`/job/${job.id}`)} activeOpacity={0.7}>
              <View style={s.tlLeft}><View style={[s.tlDot, { backgroundColor: color }]} />{idx < jobs.length - 1 && <View style={s.tlLine} />}</View>
              <View style={s.tlCard}>
                <View style={s.tlCardHead}>
                  <Text style={s.tlTime}>{job.time_window_start} – {job.time_window_end}</Text>
                  {(job.priority === 'urgent' || job.priority === 'high') && (
                    <View style={[s.priTag, job.priority === 'urgent' ? s.urgTag : s.highTag]}>
                      {job.priority === 'urgent' && <AlertTriangle size={10} color="#FFF" strokeWidth={2.5} />}
                      <Text style={s.priTagText}>{job.priority === 'urgent' ? 'Urgent' : 'High'}</Text>
                    </View>
                  )}
                </View>
                <Text style={s.tlAddr} numberOfLines={1}>{job.address}</Text>
                <View style={s.tlFoot}>
                  <TypeIcon size={14} color="#565B7E" strokeWidth={2} />
                  <Text style={s.tlType}>{job.job_type.replace('_', ' ')}</Text>
                  <View style={[s.sDot, { backgroundColor: color }]} />
                  <Text style={[s.sLabel, { color }]}>{job.status.replace('_', ' ')}</Text>
                </View>
              </View>
            </TouchableOpacity>
          );
        })}
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  headerTitle: { fontSize: 26, fontFamily: 'Outfit_600SemiBold', color: '#FFFFFF', letterSpacing: -0.5 },
  weekNav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, marginBottom: 12 },
  navBtn: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', backgroundColor: '#151722', borderRadius: 10, borderWidth: 1, borderColor: '#1E2030' },
  weekLabel: { fontSize: 14, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  dayRow: { flexDirection: 'row', paddingHorizontal: 12, marginBottom: 16, gap: 6 },
  dayBtn: { flex: 1, alignItems: 'center', paddingVertical: 10, backgroundColor: '#151722', borderRadius: 12, borderWidth: 1, borderColor: '#1E2030' },
  dayBtnSel: { backgroundColor: '#3B82F6', borderColor: '#3B82F6' },
  dayName: { fontSize: 10, fontFamily: 'Manrope_500Medium', color: '#565B7E', marginBottom: 4 },
  dayNameSel: { color: '#FFFFFF' },
  dayNum: { fontSize: 16, fontFamily: 'Outfit_700Bold', color: '#FFFFFF' },
  dayNumSel: { color: '#FFFFFF' },
  dayNumToday: { color: '#3B82F6' },
  todayDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: '#3B82F6', marginTop: 4 },
  todayDotSel: { backgroundColor: '#FFFFFF' },
  timeline: { flex: 1 },
  timelineContent: { paddingHorizontal: 16, paddingBottom: 100 },
  tlItem: { flexDirection: 'row' },
  tlLeft: { width: 28, alignItems: 'center' },
  tlDot: { width: 10, height: 10, borderRadius: 5, marginTop: 18 },
  tlLine: { width: 2, flex: 1, backgroundColor: '#1E2030' },
  tlCard: { flex: 1, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 14, marginLeft: 10, marginBottom: 8 },
  tlCardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
  tlTime: { fontSize: 14, fontFamily: 'JetBrainsMono_500Medium', color: '#3B82F6' },
  priTag: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  urgTag: { backgroundColor: '#EF4444' },
  highTag: { backgroundColor: '#F59E0B' },
  priTagText: { fontSize: 11, fontFamily: 'Manrope_600SemiBold', color: '#FFFFFF' },
  tlAddr: { fontSize: 16, fontFamily: 'Outfit_600SemiBold', color: '#FFFFFF', marginBottom: 8 },
  tlFoot: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  tlType: { fontSize: 12, fontFamily: 'Manrope_500Medium', color: '#565B7E', textTransform: 'capitalize', marginRight: 8 },
  sDot: { width: 6, height: 6, borderRadius: 3 },
  sLabel: { fontSize: 12, fontFamily: 'Manrope_600SemiBold', textTransform: 'capitalize' },
  loadingWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyText: { fontSize: 14, fontFamily: 'Manrope_400Regular', color: '#565B7E', marginBottom: 4 },
  emptySub: { fontSize: 12, fontFamily: 'JetBrainsMono_400Regular', color: '#3D4160' },
});
