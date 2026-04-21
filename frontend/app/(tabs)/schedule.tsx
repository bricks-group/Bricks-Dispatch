import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight, Camera, Video, Plane, Box, Ruler } from 'lucide-react-native';
import { TOKENS } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || "http://10.0.0.206:8000";
type Job = { id: string; address: string; city: string; time_window_start: string; time_window_end: string; job_type: string; status: string; priority: string; scheduled_date: string };
const JOB_TYPE_ICONS: Record<string, React.ComponentType<any>> = { photo: Camera, video: Video, drone: Plane, '3d_tour': Box, floor_plan: Ruler };
const STATUS_COLORS: Record<string, string> = { assigned: TOKENS.moonMute, in_progress: TOKENS.brick, completed: TOKENS.ok };
function getDatesForWeek(baseDate: Date): string[] { const dates: string[] = []; const start = new Date(baseDate); start.setDate(start.getDate() - start.getDay()); for (let i = 0; i < 7; i++) { const d = new Date(start); d.setDate(d.getDate() + i); dates.push(d.toISOString().split('T')[0]); } return dates; }
const DAY_NAMES = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

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
      <View style={s.header}>
        <View>
          <Text style={s.overline}>Schedule · Week {Math.ceil(new Date().getDate() / 7)}</Text>
          <Text style={s.headerTitle}>Timeline</Text>
        </View>
      </View>
      <View style={s.weekNav}>
        <TouchableOpacity testID="prev-week-btn" onPress={() => { const d = new Date(currentDate); d.setDate(d.getDate() - 7); setCurrentDate(d); }} style={s.navBtn}><ChevronLeft size={16} color={TOKENS.moonMute} strokeWidth={1.5} /></TouchableOpacity>
        <Text style={s.weekLabel}>{new Date(weekDates[0]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()} – {new Date(weekDates[6]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()}</Text>
        <TouchableOpacity testID="next-week-btn" onPress={() => { const d = new Date(currentDate); d.setDate(d.getDate() + 7); setCurrentDate(d); }} style={s.navBtn}><ChevronRight size={16} color={TOKENS.moonMute} strokeWidth={1.5} /></TouchableOpacity>
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
      <ScrollView style={s.timeline} contentContainerStyle={s.timelineContent} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={TOKENS.brick} />}>
        {loading ? <View style={s.loadingWrap}><ActivityIndicator size="small" color={TOKENS.brick} /></View>
        : jobs.length === 0 ? <View style={s.emptyWrap}><Text style={s.emptyText}>no jobs scheduled</Text><Text style={s.emptySub}>{selectedDate}</Text></View>
        : <>
          {jobs.map((job, idx) => {
          const TypeIcon = JOB_TYPE_ICONS[job.job_type] || Camera; const color = STATUS_COLORS[job.status] || TOKENS.moonMute;
          return (
            <TouchableOpacity key={job.id} testID={`schedule-job-${job.id}`} style={s.tlItem} onPress={() => router.push(`/job/${job.id}`)} activeOpacity={0.7}>
              <View style={s.tlLeft}><View style={[s.tlDot, { backgroundColor: color }]} />{idx < jobs.length - 1 && <View style={s.tlLine} />}</View>
              <View style={s.tlCard}>
                <View style={s.tlCardHead}>
                  <Text style={s.tlTime}>{job.time_window_start}</Text>
                  {(job.priority === 'urgent' || job.priority === 'high') && (
                    <View style={[s.priTag, { backgroundColor: job.priority === 'urgent' ? TOKENS.bad : TOKENS.warn }]}>
                      <Text style={s.priTagText}>{job.priority.toUpperCase()}</Text>
                    </View>
                  )}
                </View>
                <Text style={s.tlAddr} numberOfLines={1}>{job.address.toLowerCase()}</Text>
                <View style={s.tlFoot}>
                  <TypeIcon size={14} color={TOKENS.moonMute} strokeWidth={1.5} />
                  <Text style={s.tlType}>{job.job_type.toUpperCase().replace('_', ' ')}</Text>
                  <Text style={[s.sLabel, { color }]}>{job.status.toUpperCase().replace('_', ' ')}</Text>
                </View>
              </View>
            </TouchableOpacity>
          );
        })}
        </>}
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  header: { paddingHorizontal: 36, paddingTop: 12, paddingBottom: 16 },
  overline: { fontFamily: 'JetBrainsMono-Medium', fontSize: 10, color: TOKENS.moonMute, letterSpacing: 1.6, textTransform: 'uppercase' },
  headerTitle: { fontSize: 34, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -1.1, marginTop: 4 },
  weekNav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 36, marginBottom: 20 },
  navBtn: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center', backgroundColor: TOKENS.nightSoft, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHair },
  weekLabel: { fontSize: 11, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonMute, letterSpacing: 1.2 },
  dayRow: { flexDirection: 'row', paddingHorizontal: 36, marginBottom: 24, gap: 8 },
  dayBtn: { flex: 1, alignItems: 'center', paddingVertical: 12, backgroundColor: TOKENS.nightSoft, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHair },
  dayBtnSel: { borderBottomColor: TOKENS.brick },
  dayName: { fontSize: 9, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonFaint, marginBottom: 4 },
  dayNameSel: { color: TOKENS.moon },
  dayNum: { fontSize: 14, fontFamily: 'JetBrainsMono-Regular', color: TOKENS.moon },
  dayNumSel: { color: TOKENS.moon },
  dayNumToday: { color: TOKENS.brick },
  todayDot: { width: 4, height: 4, borderRadius: 2, backgroundColor: TOKENS.brick, marginTop: 4 },
  todayDotSel: { backgroundColor: TOKENS.moon },
  timeline: { flex: 1 },
  timelineContent: { paddingHorizontal: 36, paddingBottom: 100 },
  tlItem: { flexDirection: 'row' },
  tlLeft: { width: 20, alignItems: 'center', marginRight: 16 },
  tlDot: { width: 6, height: 6, borderRadius: 3, marginTop: 22 },
  tlLine: { width: 1, flex: 1, backgroundColor: TOKENS.nightHair, marginTop: 4 },
  tlCard: { flex: 1, paddingVertical: 16, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair, marginBottom: 4 },
  tlCardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 },
  tlTime: { fontSize: 12, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moon },
  priTag: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 2 },
  priTagText: { fontSize: 9, fontFamily: 'JetBrainsMono-Medium', color: '#FFF' },
  tlAddr: { fontSize: 16, fontFamily: 'System', color: TOKENS.moonSoft, marginBottom: 10 },
  tlFoot: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  tlType: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonFaint, marginRight: 8 },
  sLabel: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium' },
  loadingWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyWrap: { paddingVertical: 60, alignItems: 'center' },
  emptyText: { fontSize: 13, fontFamily: 'System', color: TOKENS.moonMute, marginBottom: 8 },
  emptySub: { fontSize: 11, fontFamily: 'JetBrainsMono-Regular', color: TOKENS.moonFaint },
});
