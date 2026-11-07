import { useState, useEffect, useCallback } from 'react';
import { fmtMoneyExact } from '../../lib/money';
import { View, Text, StyleSheet, RefreshControl, Linking } from 'react-native';
import { useRouter } from 'expo-router';
import { Bell, Navigation, Phone, Check as CheckIcon, Sun, Box, LayoutGrid, Video } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { cachedJson } from '../../lib/offline';
import { anyCalendarConnected, fetchVendorCalendars } from '../../lib/calendarConnect';
import { captureNeitherCopy, jobCaptureModes } from '../../lib/capture-modes';
import { formatShootWindow, minutesUntilShoot } from '../../lib/shoot-window';
import { color, type as T, space, radius, layout, hairline } from '../../theme';
import { Screen, PageHeader, Section, Group, Row, Button, IconButton, StatGrid, Avatar, EmptyState, LoadingState } from '../../components/ui';
import { EnterFadeDown, PressScale } from '../../components/motion';
import { CalendarConnectNudge } from '../../components/CalendarConnectNudge';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
const VENDOR_RATE = 0.62;

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = {
  id: string; address: string; city: string; state: string;
  timezone?: string | null;
  zip_code?: string | null;
  time_window_start: string; time_window_end?: string | null;
  preferred_window?: string | null;
  job_type: string; status: string;
  scheduled_date: string; package_name: string | null; package_price: number | null;
  agent_name: string | null; agent_phone?: string | null; client_instructions: string | null;
  checklist: ChecklistItem[];
  checklist_title?: string | null;
  package_id?: string | null;
  add_ons?: unknown;
  capture?: { tour?: boolean; floorplan?: boolean; visit?: boolean } | null;
  property_details?: string | null;
};

function todayISO() {
  return new Date().toLocaleDateString('en-CA');
}

const fmtMoney = (v: number) => fmtMoneyExact(v);

function TodayCaptureEntry({ job, onOpen }: { job: Job; onOpen: (kind?: 'splat' | 'floorplan' | 'visit' | 'mode') => void }) {
  const flags = jobCaptureModes(job);
  const hasMode = flags.tour || flags.floorplan || flags.visit;
  if (!hasMode) {
    return <Text style={[T.bodySm, s.captureNone]}>{captureNeitherCopy(flags)}</Text>;
  }
  const modes = [flags.tour, flags.floorplan, flags.visit].filter(Boolean).length;
  if (modes > 1) {
    return (
      <Group style={s.captureStack}>
        {flags.tour ? <Row icon={Box} title="360° tour" chevron onPress={() => onOpen('splat')} /> : null}
        {flags.floorplan ? <Row icon={LayoutGrid} title="Floor plan" chevron onPress={() => onOpen('floorplan')} /> : null}
        {flags.visit ? <Row icon={Video} title="Virtual visit" chevron onPress={() => onOpen('visit')} /> : null}
      </Group>
    );
  }
  if (flags.visit) {
    return <View style={s.captureStack}><Button variant="secondary" icon={Video} title="Virtual visit" onPress={() => onOpen('visit')} /></View>;
  }
  const kind = flags.tour ? 'splat' : 'floorplan';
  const label = flags.tour ? '3D tour' : 'Floor plan';
  return <View style={s.captureStack}><Button variant="secondary" icon={flags.tour ? Box : LayoutGrid} title={label} onPress={() => onOpen(kind)} /></View>;
}

export default function TodayScreen() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [unread, setUnread] = useState(0);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [needCalendar, setNeedCalendar] = useState(false);
  const today = todayISO();

  const fetchData = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      // Jobs are network-first with an on-device fallback so the day's addresses and
      // access notes stay readable without signal. Unread count is best-effort.
      const [jobsResult, notifData, calData] = await Promise.all([
        cachedJson<any>('vendor-os-jobs', `${API}/api/vendor-os/jobs`, { headers }),
        fetch(`${API}/api/notifications/unread-count`, { headers })
          .then(r => (r.ok ? r.json() : { count: 0 }))
          .catch(() => ({ count: 0 })),
        fetchVendorCalendars().catch(() => null),
      ]);
      const payload = jobsResult.data;
      setSavedAt(jobsResult.fromCache ? jobsResult.savedAt : null);
      const allJobs: Job[] = Array.isArray(payload) ? payload : (payload?.items || []);
      const todayJobs = allJobs
        .filter(j => (j.scheduled_date || '').slice(0, 10) === today)
        .sort((a, b) => a.time_window_start.localeCompare(b.time_window_start));
      setJobs(todayJobs);
      setUnread(notifData.count || 0);
      setNeedCalendar(calData ? !anyCalendarConnected(calData) : false);
    } catch (e) {
      console.error('Today fetch error', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [today]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const dateLabel = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
  const nextJob = jobs.find(j => j.status !== 'completed');
  const laterJobs = nextJob ? jobs.filter(j => j.id !== nextJob.id) : [];
  const minsUntil = nextJob ? minutesUntilShoot(nextJob) : null;

  const openMaps = (job: Job) => {
    const addr = encodeURIComponent(`${job.address}, ${job.city}, ${job.state}`);
    Linking.openURL(`maps://?q=${addr}`).catch(() =>
      Linking.openURL(`https://maps.google.com/?q=${addr}`)
    );
  };

  const openCapture = (job: Job, kind?: 'splat' | 'floorplan' | 'visit' | 'mode') => {
    if (kind === 'mode' || !kind) {
      router.push({ pathname: '/(capture)/mode', params: { jobId: job.id } } as any);
      return;
    }
    if (kind === 'visit') {
      router.push({ pathname: '/(capture)/visit', params: { jobId: job.id } } as any);
      return;
    }
    const pathname = kind === 'floorplan' ? '/(capture)/floorplan' : '/(capture)/capture';
    router.push({ pathname, params: { jobId: job.id, kind } } as any);
  };

  const typeLabel = (t?: string) => (t ? t.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '');
  const savedLabel = savedAt
    ? `Offline · saved ${new Date(savedAt).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`
    : undefined;

  return (
    <Screen
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchData(); }} tintColor={color.accent} />}
    >
      <EnterFadeDown>
        <PageHeader
          overline={dateLabel}
          title={loading ? 'Today' : jobs.length > 0 ? `${jobs.length} shoot${jobs.length !== 1 ? 's' : ''} today` : 'Clear day'}
          subtitle={savedLabel}
          right={
            <IconButton icon={Bell} label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'} badge={unread > 0} onPress={() => router.push('/notifications')} />
          }
        />

        {needCalendar ? (
          <CalendarConnectNudge onPress={() => router.push('/settings')} />
        ) : null}

        {loading && jobs.length === 0 ? (
          <LoadingState />
        ) : jobs.length === 0 ? (
          <EmptyState icon={Sun} title="Nothing on the books" body="Pull down to refresh." />
        ) : (
          <>
            {nextJob && (
              <Section first label={minsUntil ? `Next up · in ${minsUntil} min` : 'Next up'}>
                <PressScale
                  style={s.nextCard}
                  scaleTo={0.99}
                  onPress={() => router.push(`/job/${nextJob.id}`)}
                  accessibilityRole="button"
                  accessibilityLabel={`Open job at ${nextJob.address}`}
                >
                  <Text style={[T.title, { color: color.text }]}>{nextJob.address}</Text>
                  <Text style={[T.bodySm, s.nextMeta]}>
                    {[[nextJob.city, nextJob.state].filter(Boolean).join(', '), typeLabel(nextJob.job_type)].filter(Boolean).join(' · ')}
                  </Text>
                  <StatGrid
                    items={[
                      { label: 'Window', value: formatShootWindow(nextJob) },
                      { label: 'Package', value: nextJob.package_name || '—' },
                    ]}
                  />
                  {nextJob.agent_name ? (
                    <View style={s.agentLine}>
                      <Avatar name={nextJob.agent_name} size={32} />
                      <View style={s.flex}>
                        <Text style={[T.callout, { color: color.text }]}>{nextJob.agent_name}</Text>
                        <Text style={[T.caption, { color: color.textMuted }]}>Listing agent</Text>
                      </View>
                      {nextJob.agent_phone ? (
                        <IconButton icon={Phone} label={`Call ${nextJob.agent_name}`} outline onPress={() => Linking.openURL(`tel:${nextJob.agent_phone}`)} />
                      ) : null}
                    </View>
                  ) : null}
                  <Button icon={Navigation} title="Navigate" onPress={() => openMaps(nextJob)} style={s.navigate} />
                </PressScale>
                {['assigned', 'in_progress'].includes(nextJob.status || '') ? (
                  <TodayCaptureEntry job={nextJob} onOpen={(kind) => openCapture(nextJob, kind)} />
                ) : null}
              </Section>
            )}

            {nextJob && (nextJob.checklist?.length ?? 0) > 0 && (
              <Section label={nextJob.checklist_title || 'Shoot checklist'}>
                <Group>
                  {(nextJob.checklist || []).slice(0, 12).map((item) => (
                    <View key={item.id} style={s.checkRow}>
                      <View style={[s.check, item.completed && s.checkOn]}>
                        {item.completed && <CheckIcon size={12} color={color.textOnAccent} strokeWidth={2.5} />}
                      </View>
                      <Text style={[T.body, s.flex, { color: item.completed ? color.textFaint : color.text }, item.completed && s.strike]}>
                        {item.label}
                      </Text>
                    </View>
                  ))}
                </Group>
              </Section>
            )}

            {laterJobs.length > 0 && (
              <Section label="Later today">
                <Group>
                  {laterJobs.map((job) => {
                    const payout = job.package_price ? Math.round(job.package_price * VENDOR_RATE) : null;
                    const active = ['assigned', 'in_progress'].includes(job.status || '');
                    return (
                      <View key={job.id}>
                        <PressScale
                          style={s.laterRow}
                          scaleTo={0.99}
                          onPress={() => router.push(`/job/${job.id}`)}
                          accessibilityRole="button"
                          accessibilityLabel={`Open job at ${job.address}`}
                        >
                          <Text style={[T.dataSm, s.laterTime]}>{formatShootWindow(job)}</Text>
                          <View style={s.flex}>
                            <Text style={[T.bodyMedium, { color: color.text }]}>{job.address}</Text>
                            <Text style={[T.caption, { color: color.textMuted }]}>{typeLabel(job.job_type)}</Text>
                          </View>
                          {payout ? <Text style={[T.data, { color: color.text }]}>{fmtMoney(payout)}</Text> : null}
                        </PressScale>
                        {active ? (
                          <View style={s.laterCapture}>
                            <TodayCaptureEntry job={job} onOpen={(kind) => openCapture(job, kind)} />
                          </View>
                        ) : null}
                      </View>
                    );
                  })}
                </Group>
              </Section>
            )}
          </>
        )}
      </EnterFadeDown>
    </Screen>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  nextCard: {
    backgroundColor: color.surface, borderWidth: hairline, borderColor: color.hairStrong,
    borderRadius: radius.sm, padding: space[18], gap: space[16],
  },
  nextMeta: { color: color.textMuted, marginTop: -space[10] },
  agentLine: { flexDirection: 'row', alignItems: 'center', gap: space[12] },
  navigate: { marginTop: space[4] },
  captureStack: { gap: space[8], marginTop: space[10] },
  captureNone: { color: color.textMuted, marginTop: space[12] },

  checkRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space[12], paddingHorizontal: space[16], paddingVertical: space[14] },
  check: {
    width: 20, height: 20, marginTop: 1, borderRadius: radius.sm, borderWidth: 1.5, borderColor: color.hairStrong,
    alignItems: 'center', justifyContent: 'center',
  },
  checkOn: { backgroundColor: color.accent, borderColor: color.accent },
  strike: { textDecorationLine: 'line-through' },

  laterRow: { flexDirection: 'row', alignItems: 'center', gap: space[14], paddingHorizontal: space[16], paddingVertical: space[14], minHeight: layout.rowMin },
  laterTime: { color: color.textBody, width: 88 },
  laterCapture: { paddingHorizontal: space[16], paddingBottom: space[14] },
});
