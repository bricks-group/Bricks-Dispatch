import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, Pressable, RefreshControl } from 'react-native';
import { useRouter } from 'expo-router';
import { ChevronLeft, ChevronRight, CalendarDays } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { cachedJson } from '../../lib/offline';
import { color, type as T, space, radius, hairline, type Tone } from '../../theme';
import { Screen, PageHeader, Section, Group, IconButton, Pill, EmptyState, LoadingState } from '../../components/ui';
import { formatShootWindow } from '../../lib/shoot-window';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

type Job = {
  id: string; address: string; city: string; state: string;
  timezone?: string | null;
  zip_code?: string | null;
  time_window_start: string; time_window_end?: string | null;
  preferred_window?: string | null;
  job_type: string; status: string; scheduled_date: string;
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

function pillForStatus(status: string): { label: string; tone: Tone } {
  if (status === 'completed') return { label: 'Delivered', tone: 'ok' };
  if (status === 'in_progress') return { label: 'Next', tone: 'accent' };
  return { label: 'Scheduled', tone: 'neutral' };
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
      const { data } = await cachedJson<Job[]>('jobs', `${API}/api/jobs`, { headers });
      setAllJobs(Array.isArray(data) ? data : []);
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

  const weekEnd = weekDays[6];
  const rangeLabel = `${weekMonday.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${weekEnd.toLocaleDateString('en-US', weekEnd.getMonth() === weekMonday.getMonth() ? { day: 'numeric' } : { month: 'short', day: 'numeric' })}`;
  const shiftWeek = (days: number) => {
    const d = new Date(weekMonday);
    d.setDate(d.getDate() + days);
    setWeekMonday(d);
  };
  const typeLabel = (t: string) => t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

  return (
    <Screen
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchJobs(); }} tintColor={color.accent} />}
    >
      <PageHeader
        overline={`Week ${weekNum}`}
        title={rangeLabel}
        right={
          <>
            <IconButton icon={ChevronLeft} label="Previous week" outline onPress={() => shiftWeek(-7)} />
            <IconButton icon={ChevronRight} label="Next week" outline onPress={() => shiftWeek(7)} />
          </>
        }
      />

      <View style={s.dayGrid}>
        {weekDays.map((day, i) => {
          const iso = toISO(day);
          const isSelected = iso === selected;
          const isToday = iso === toISO(today);
          const count = jobsForDate(iso).length;
          const fg = isSelected ? color.textInverse : isToday ? color.accentText : color.text;
          return (
            <Pressable
              key={iso}
              style={[s.dayCell, isSelected && s.dayCellActive]}
              onPress={() => setSelected(iso)}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={`${day.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}, ${count} shoot${count === 1 ? '' : 's'}`}
            >
              <Text style={[T.caption, { color: isSelected ? color.textInverse : color.textMuted }]}>{DAY_LABELS[i]}</Text>
              <Text style={[T.dataMd, { color: fg }]}>{day.getDate()}</Text>
              <View style={s.dotRow}>
                {Array.from({ length: Math.min(count, 3) }).map((_, j) => (
                  <View key={j} style={[s.dot, { backgroundColor: isSelected ? color.textInverse : color.accent }]} />
                ))}
              </View>
            </Pressable>
          );
        })}
      </View>

      <Section label={`${selectedLabel} · ${selectedJobs.length} shoot${selectedJobs.length !== 1 ? 's' : ''}`}>
        {loading && allJobs.length === 0 ? (
          <LoadingState />
        ) : selectedJobs.length === 0 ? (
          <EmptyState icon={CalendarDays} title="Nothing booked" body="Pick another day." />
        ) : (
          <Group>
            {selectedJobs.map((job) => {
              const pill = pillForStatus(job.status);
              return (
                <Pressable
                  key={job.id}
                  style={({ pressed }) => [s.row, pressed && s.rowPressed]}
                  onPress={() => router.push(`/job/${job.id}`)}
                  accessibilityRole="button"
                  accessibilityLabel={`Open job at ${job.address}`}
                >
                  <Text style={[T.dataSm, s.time]}>{formatShootWindow(job)}</Text>
                  <View style={s.info}>
                    <Text style={[T.bodyMedium, { color: color.text }]}>{job.address}</Text>
                    <Text style={[T.caption, { color: color.textMuted }]}>{typeLabel(job.job_type)}</Text>
                    <View style={s.pill}><Pill label={pill.label} tone={pill.tone} /></View>
                  </View>
                </Pressable>
              );
            })}
          </Group>
        )}
      </Section>
    </Screen>
  );
}

const s = StyleSheet.create({
  dayGrid: { flexDirection: 'row', gap: space[4] },
  dayCell: {
    flex: 1, minHeight: 68, alignItems: 'center', justifyContent: 'center', gap: space[4],
    borderWidth: hairline, borderColor: color.hair, borderRadius: radius.sm,
  },
  dayCellActive: { backgroundColor: color.inverse, borderColor: color.inverse },
  dotRow: { flexDirection: 'row', gap: 3, height: 4, alignItems: 'center' },
  dot: { width: 4, height: 4, borderRadius: radius.pill },
  row: { flexDirection: 'row', gap: space[14], paddingHorizontal: space[16], paddingVertical: space[16] },
  rowPressed: { backgroundColor: color.surface },
  time: { color: color.textBody, width: 88, paddingTop: 2 },
  info: { flex: 1, gap: 2 },
  pill: { marginTop: space[8] },
});
