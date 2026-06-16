import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, RefreshControl } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChevronLeft, ChevronRight } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

const DAY_LABELS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];

type Job = {
  id: string; address: string; city: string; state: string;
  time_window_start: string; job_type: string; status: string; scheduled_date: string;
};

function getWeekNumber(d: Date) {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const dayNum = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return Math.ceil((((date.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
}

function getMondayOf(date: Date) {
  const d = new Date(date);
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  d.setHours(0, 0, 0, 0);
  return d;
}

function toISO(d: Date) {
  return d.toISOString().slice(0, 10);
}

function fmtTime(iso: string) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const h = d.getHours().toString().padStart(2, '0');
  const m = d.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
}

function pillForStatus(status: string): { label: string; bg: string; fg: string; dot: string } {
  if (status === 'completed') return { label: 'Delivered', bg: TOKENS.ok + '33', fg: '#B8CC9E', dot: TOKENS.ok };
  if (status === 'in_progress') return { label: 'Next', bg: TOKENS.brick + '33', fg: TOKENS.brickSoft, dot: TOKENS.brick };
  return { label: 'Scheduled', bg: TOKENS.nightDeep, fg: TOKENS.moonSoft, dot: TOKENS.nightHairS };
}

export default function ScheduleScreen() {
  const router = useRouter();
  const today = new Date();
  const [weekMonday, setWeekMonday] = useState(getMondayOf(today));
  const [selected, setSelected] = useState(toISO(today));
  const [allJobs, setAllJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const weekDays = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekMonday);
    d.setDate(weekMonday.getDate() + i);
    return d;
  });

  const fetchJobs = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/jobs`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAllJobs(await res.json());
    } catch (e) {
      console.error('Schedule fetch error', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  const jobsForDate = (iso: string) => allJobs.filter(j => j.scheduled_date === iso);
  const selectedJobs = jobsForDate(selected)
    .sort((a, b) => a.time_window_start.localeCompare(b.time_window_start));

  const selectedDate = new Date(selected + 'T00:00:00');
  const selectedLabel = selectedDate.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });

  const weekNum = getWeekNumber(weekMonday);

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchJobs(); }} tintColor={TOKENS.brick} />}
        stickyHeaderIndices={[0]}
      >
        {/* Sticky header + week grid */}
        <View style={s.stickyTop}>
          {/* Week header */}
          <View style={s.weekHeader}>
            <Text style={s.weekTitle}>Week {weekNum}</Text>
            <View style={s.weekNav}>
              <TouchableOpacity
                style={s.navBtn}
                onPress={() => {
                  const prev = new Date(weekMonday);
                  prev.setDate(prev.getDate() - 7);
                  setWeekMonday(prev);
                }}
                activeOpacity={0.7}
              >
                <ChevronLeft size={14} color={TOKENS.moonMute} strokeWidth={1.5} />
              </TouchableOpacity>
              <TouchableOpacity
                style={s.navBtn}
                onPress={() => {
                  const next = new Date(weekMonday);
                  next.setDate(next.getDate() + 7);
                  setWeekMonday(next);
                }}
                activeOpacity={0.7}
              >
                <ChevronRight size={14} color={TOKENS.moonMute} strokeWidth={1.5} />
              </TouchableOpacity>
            </View>
          </View>

          {/* 7-day grid */}
          <View style={s.dayGrid}>
            {weekDays.map((day, i) => {
              const iso = toISO(day);
              const isSelected = iso === selected;
              const isToday = iso === toISO(today);
              const count = jobsForDate(iso).length;
              return (
                <TouchableOpacity
                  key={iso}
                  style={[s.dayCell, isSelected && s.dayCellActive]}
                  onPress={() => setSelected(iso)}
                  activeOpacity={0.8}
                >
                  <Text style={[s.dayName, isSelected && s.dayNameActive]}>
                    {DAY_LABELS[i]}
                  </Text>
                  <Text style={[s.dayNum, isSelected && s.dayNumActive, isToday && !isSelected && { color: TOKENS.brick }]}>
                    {day.getDate()}
                  </Text>
                  <View style={s.dotRow}>
                    {count > 0
                      ? Array.from({ length: Math.min(count, 4) }).map((_, j) => (
                          <View key={j} style={[s.dot, { backgroundColor: isSelected ? TOKENS.moon : TOKENS.brick }]} />
                        ))
                      : <View style={{ height: 4 }} />
                    }
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        {/* Day label */}
        <View style={s.dayLabelRow}>
          <Text style={s.dayLabelText}>
            {selectedLabel.toUpperCase()} · {selectedJobs.length} SHOOT{selectedJobs.length !== 1 ? 'S' : ''}
          </Text>
        </View>

        {/* Timeline */}
        {!loading && selectedJobs.length === 0 ? (
          <View style={s.empty}><Text style={s.emptyText}>No jobs on this day</Text></View>
        ) : (
          <View style={s.timeline}>
            {selectedJobs.map((job, i) => {
              const pill = pillForStatus(job.status);
              return (
                <TouchableOpacity
                  key={job.id}
                  style={[s.timelineRow, i < selectedJobs.length - 1 && s.timelineRowBorder]}
                  onPress={() => router.push(`/job/${job.id}`)}
                  activeOpacity={0.7}
                >
                  <Text style={s.timelineTime}>{fmtTime(job.time_window_start)}</Text>
                  <View style={s.timelineLine}>
                    <View style={[s.timelineDot, { backgroundColor: pill.dot }]} />
                  </View>
                  <View style={s.timelineInfo}>
                    <Text style={s.timelineAddr}>{job.address.toLowerCase()}</Text>
                    <Text style={s.timelineType}>{job.job_type.replace(/_/g, ' ')}</Text>
                    <View style={[s.pill, { backgroundColor: pill.bg, marginTop: 8 }]}>
                      <Text style={[s.pillText, { color: pill.fg }]}>{pill.label.toUpperCase()}</Text>
                    </View>
                  </View>
                </TouchableOpacity>
              );
            })}
          </View>
        )}

        <View style={{ height: 120 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  center: { paddingTop: 60, alignItems: 'center' },
  empty: { paddingTop: 60, alignItems: 'center' },
  emptyText: { fontFamily: TYPOGRAPHY.body, color: TOKENS.moonFaint, fontSize: 13 },

  stickyTop: { backgroundColor: TOKENS.night, paddingHorizontal: 24, paddingTop: 16, paddingBottom: 0 },
  weekHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  weekTitle: { fontSize: 22, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.4 },
  weekNav: { flexDirection: 'row', gap: 6 },
  navBtn: { padding: 8, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },

  dayGrid: { flexDirection: 'row', gap: 4, marginBottom: 16 },
  dayCell: { flex: 1, alignItems: 'center', paddingVertical: 10, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  dayCellActive: { backgroundColor: TOKENS.moon, borderColor: TOKENS.moon },
  dayName: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 8, color: TOKENS.moonFaint, letterSpacing: 1, marginBottom: 4, opacity: 0.7 },
  dayNameActive: { color: TOKENS.night, opacity: 0.7 },
  dayNum: { fontFamily: TYPOGRAPHY.mono, fontSize: 14, color: TOKENS.moonFaint },
  dayNumActive: { color: TOKENS.night },
  dotRow: { flexDirection: 'row', gap: 2, justifyContent: 'center', marginTop: 5, height: 4 },
  dot: { width: 3, height: 3, borderRadius: 2 },

  dayLabelRow: { paddingHorizontal: 24, paddingVertical: 14 },
  dayLabelText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.6 },

  timeline: { paddingHorizontal: 24 },
  timelineRow: { flexDirection: 'row', gap: 14, paddingVertical: 16 },
  timelineRowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  timelineTime: { fontFamily: TYPOGRAPHY.mono, fontSize: 12, color: TOKENS.moonFaint, width: 44, paddingTop: 2 },
  timelineLine: { width: 1, backgroundColor: TOKENS.nightHair, position: 'relative' },
  timelineDot: { position: 'absolute', top: 6, left: -3, width: 7, height: 7, borderRadius: 4 },
  timelineInfo: { flex: 1, paddingLeft: 8 },
  timelineAddr: { fontSize: 14, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, marginBottom: 3 },
  timelineType: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint },
  pill: { alignSelf: 'flex-start', paddingHorizontal: 9, paddingVertical: 4, borderRadius: 2 },
  pillText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, letterSpacing: 1.2 },
});
