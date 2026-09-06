import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  RefreshControl, Linking,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Bell, Navigation, Phone, Check } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { captureNeitherCopy, jobCaptureModes } from '../../lib/capture-modes';
import { formatShootWindow, minutesUntilShoot } from '../../lib/shoot-window';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';
const VENDOR_RATE = 0.62;

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = {
  id: string; address: string; city: string; state: string;
  time_window_start: string; time_window_end?: string | null;
  preferred_window?: string | null;
  job_type: string; status: string;
  scheduled_date: string; package_name: string | null; package_price: number | null;
  agent_name: string | null; client_instructions: string | null;
  checklist: ChecklistItem[];
  package_id?: string | null;
  add_ons?: unknown;
  capture?: { tour?: boolean; floorplan?: boolean; visit?: boolean } | null;
  property_details?: string | null;
};

function todayISO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Toronto' });
}

function fmtMoney(amount: number) {
  return `$${amount.toLocaleString('en-US')}`;
}

function initials(name: string | null) {
  if (!name) return '??';
  return name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
}

function TodayCaptureEntry({ job, onOpen }: { job: Job; onOpen: (kind?: 'splat' | 'floorplan' | 'visit' | 'mode') => void }) {
  const flags = jobCaptureModes(job);
  const hasMode = flags.tour || flags.floorplan || flags.visit;
  if (!hasMode) {
    return (
      <View style={s.todayCaptureNone}>
        <Text style={s.todayCaptureNoneText}>{captureNeitherCopy(flags)}</Text>
      </View>
    );
  }
  const modes = [flags.tour, flags.floorplan, flags.visit].filter(Boolean).length;
  if (modes > 1) {
    return (
      <View style={s.todayCaptureRow}>
        {flags.tour ? (
          <TouchableOpacity style={s.todayCaptureBtn} onPress={() => onOpen('splat')} activeOpacity={0.8}>
            <Text style={s.todayCaptureBtnText}>Start Capture · 3D tour</Text>
          </TouchableOpacity>
        ) : null}
        {flags.floorplan ? (
          <TouchableOpacity style={s.todayCaptureBtn} onPress={() => onOpen('floorplan')} activeOpacity={0.8}>
            <Text style={s.todayCaptureBtnText}>Start Capture · Floor plan</Text>
          </TouchableOpacity>
        ) : null}
        {flags.visit ? (
          <TouchableOpacity style={s.todayCaptureBtn} onPress={() => onOpen('visit')} activeOpacity={0.8}>
            <Text style={s.todayCaptureBtnText}>Deliver Visit</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    );
  }
  if (flags.visit) {
    return (
      <View style={s.todayCaptureRow}>
        <TouchableOpacity style={s.todayCaptureBtn} onPress={() => onOpen('visit')} activeOpacity={0.8}>
          <Text style={s.todayCaptureBtnText}>Deliver Visit</Text>
        </TouchableOpacity>
      </View>
    );
  }
  const kind = flags.tour ? 'splat' : 'floorplan';
  const label = flags.tour ? 'Start Capture' : 'Start Capture · Floor plan';
  return (
    <View style={s.todayCaptureRow}>
      <TouchableOpacity style={s.todayCaptureBtn} onPress={() => onOpen(kind)} activeOpacity={0.8}>
        <Text style={s.todayCaptureBtnText}>{label}</Text>
      </TouchableOpacity>
    </View>
  );
}

export default function TodayScreen() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [unread, setUnread] = useState(0);
  const today = todayISO();

  const fetchData = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const [jobsRes, notifRes] = await Promise.all([
        fetch(`${API}/api/vendor-os/jobs`, { headers }),
        fetch(`${API}/api/notifications/unread-count`, { headers }),
      ]);
      if (!jobsRes.ok) throw new Error(`Jobs API error: ${jobsRes.status}`);
      if (!notifRes.ok) throw new Error(`Notifications API error: ${notifRes.status}`);
      const payload = await jobsRes.json();
      const allJobs: Job[] = Array.isArray(payload) ? payload : (payload.items || []);
      const notifData = await notifRes.json();
      const todayJobs = allJobs
        .filter(j => (j.scheduled_date || '').slice(0, 10) === today)
        .sort((a, b) => a.time_window_start.localeCompare(b.time_window_start));
      setJobs(todayJobs);
      setUnread(notifData.count || 0);
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

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <ScrollView
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchData(); }} tintColor={TOKENS.brick} />}
        contentContainerStyle={s.scroll}
      >
        {/* Header */}
        <View style={s.header}>
          <View>
            <Text style={s.dateLabel}>{dateLabel.toUpperCase()}</Text>
            <Text style={s.title}>
              {loading ? '—' : jobs.length > 0 ? `${jobs.length} shoot${jobs.length !== 1 ? 's' : ''}.` : 'Clear day.'}
            </Text>
          </View>
          <TouchableOpacity style={s.bellWrap} onPress={() => router.push('/notifications')} activeOpacity={0.7}>
            <Bell size={18} color={TOKENS.moonMute} strokeWidth={1.5} />
            {unread > 0 && (
              <View style={s.unreadBadge}>
                <Text style={s.unreadText}>{unread > 9 ? '9+' : unread}</Text>
              </View>
            )}
          </TouchableOpacity>
        </View>

        {!loading && jobs.length === 0 ? (
          <View style={s.empty}>
            <Text style={s.emptyText}>No jobs scheduled for today.</Text>
          </View>
        ) : (
          <>
            {/* Next up card */}
            {nextJob && (
              <View style={s.section}>
                <Text style={s.sectionLabel}>
                  {minsUntil ? `NEXT UP · IN ${minsUntil} MIN` : 'NEXT UP · NOW'}
                </Text>
                <TouchableOpacity
                  style={s.nextCard}
                  onPress={() => router.push(`/job/${nextJob.id}`)}
                  activeOpacity={0.85}
                >
                  <Text style={s.nextAddress}>{nextJob.address.toLowerCase()}</Text>
                  <Text style={s.nextMeta}>
                    {[nextJob.city, nextJob.state].filter(Boolean).join(' · ').toUpperCase()}
                    {nextJob.job_type ? ` · ${nextJob.job_type.replace(/_/g, ' ').toUpperCase()}` : ''}
                  </Text>

                  {/* Info grid */}
                  <View style={s.nextGrid}>
                    {[
                      ['TIME', formatShootWindow(nextJob)],
                      ['AGENT', nextJob.agent_name ? initials(nextJob.agent_name) : '—'],
                      ['PACKAGE', nextJob.package_name || '—'],
                    ].map(([label, value], i) => (
                      <View key={label} style={[s.nextGridCell, i > 0 && s.nextGridDivider]}>
                        <Text style={s.nextGridLabel}>{label}</Text>
                        <Text style={s.nextGridValue}>{value}</Text>
                      </View>
                    ))}
                  </View>

                  {/* CTAs */}
                  <View style={s.nextActions}>
                    <TouchableOpacity style={s.accentBtn} onPress={() => openMaps(nextJob)} activeOpacity={0.8}>
                      <Navigation size={13} color="#FFF" strokeWidth={2} />
                      <Text style={s.accentBtnText}>NAVIGATE</Text>
                    </TouchableOpacity>
                    <TouchableOpacity style={s.ghostBtn} activeOpacity={0.7}>
                      <Phone size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
                    </TouchableOpacity>
                  </View>
                </TouchableOpacity>
                {['assigned', 'in_progress'].includes(nextJob.status || '') ? (
                  <View style={s.nextCaptureWrap}>
                    <TodayCaptureEntry
                      job={nextJob}
                      onOpen={(kind) => openCapture(nextJob, kind)}
                    />
                  </View>
                ) : null}
              </View>
            )}

            {/* Pre-shoot checklist for next job */}
            {nextJob && (nextJob.checklist?.length ?? 0) > 0 && (
              <View style={s.section}>
                <Text style={s.sectionLabel}>PRE-SHOOT CHECKLIST</Text>
                <View style={s.checkCard}>
                  {(nextJob.checklist || []).slice(0, 5).map((item, i, arr) => (
                    <View key={item.id} style={[s.checkRow, i < arr.length - 1 && s.checkRowBorder]}>
                      <View style={item.completed ? s.chkFilled : s.chkEmpty}>
                        {item.completed && <Check size={9} color="#FFF" strokeWidth={3} />}
                      </View>
                      <Text style={item.completed ? s.checkLabelDone : s.checkLabel} numberOfLines={1}>
                        {item.label}
                      </Text>
                    </View>
                  ))}
                </View>
              </View>
            )}

            {/* Later today */}
            {laterJobs.length > 0 && (
              <View style={s.section}>
                <Text style={s.sectionLabel}>LATER TODAY</Text>
                <View style={s.laterList}>
                  {laterJobs.map((job, i) => {
                    const payout = job.package_price ? Math.round(job.package_price * VENDOR_RATE) : null;
                    const active = ['assigned', 'in_progress'].includes(job.status || '');
                    return (
                      <View key={job.id} style={[i < laterJobs.length - 1 && s.laterRowBorder]}>
                        <TouchableOpacity
                          style={s.laterRow}
                          onPress={() => router.push(`/job/${job.id}`)}
                          activeOpacity={0.7}
                        >
                          <Text style={s.laterTime} numberOfLines={2}>{formatShootWindow(job)}</Text>
                          <View style={s.laterDivider} />
                          <View style={s.laterInfo}>
                            <Text style={s.laterAddress}>{job.address.toLowerCase()}</Text>
                            <Text style={s.laterType}>{job.job_type.replace(/_/g, ' ')}</Text>
                          </View>
                          {payout && <Text style={s.laterPayout}>{fmtMoney(payout)}</Text>}
                        </TouchableOpacity>
                        {active ? (
                          <View style={s.laterCaptureWrap}>
                            <TodayCaptureEntry job={job} onOpen={(kind) => openCapture(job, kind)} />
                          </View>
                        ) : null}
                      </View>
                    );
                  })}
                </View>
              </View>
            )}
          </>
        )}

        <View style={{ height: 120 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  scroll: { paddingHorizontal: 24, paddingTop: 16 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  empty: { paddingTop: 60, alignItems: 'center' },
  emptyText: { fontFamily: TYPOGRAPHY.body, color: TOKENS.moonFaint, fontSize: 13 },

  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 },
  dateLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 10, color: TOKENS.moonFaint, letterSpacing: 1.4, marginBottom: 4 },
  title: { fontSize: 26, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.5 },
  bellWrap: { position: 'relative', padding: 4, marginTop: 4 },
  unreadBadge: { position: 'absolute', top: 0, right: 0, backgroundColor: TOKENS.brick, borderRadius: 8, minWidth: 15, height: 15, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 3 },
  unreadText: { color: '#FFF', fontSize: 8, fontFamily: TYPOGRAPHY.monoMedium },

  section: { marginBottom: 28 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.brick, letterSpacing: 1.8, marginBottom: 10 },

  nextCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, padding: 18 },
  nextAddress: { fontSize: 22, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.4, marginBottom: 4 },
  nextMeta: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint, letterSpacing: 0.6, marginBottom: 16 },

  nextGrid: { flexDirection: 'row', borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, marginBottom: 14, overflow: 'hidden' },
  nextGridCell: { flex: 1, padding: 10 },
  nextGridDivider: { borderLeftWidth: 1, borderLeftColor: TOKENS.nightHair },
  nextGridLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 8, color: TOKENS.moonFaint, letterSpacing: 1.4, marginBottom: 5 },
  nextGridValue: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moon, lineHeight: 15 },

  nextActions: { flexDirection: 'row', gap: 8 },
  accentBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: TOKENS.brick, paddingVertical: 12, borderRadius: 2 },
  accentBtnText: { color: '#FFF', fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, letterSpacing: 1.2 },
  ghostBtn: { width: 44, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },

  checkCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 11 },
  checkRowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  chkFilled: { width: 17, height: 17, borderRadius: 2, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  chkEmpty: { width: 17, height: 17, borderRadius: 2, borderWidth: 1.5, borderColor: TOKENS.nightHairS, flexShrink: 0 },
  checkLabel: { fontFamily: TYPOGRAPHY.body, flex: 1, fontSize: 13, color: TOKENS.moon },
  checkLabelDone: { fontFamily: TYPOGRAPHY.body, flex: 1, fontSize: 13, color: TOKENS.moonFaint, textDecorationLine: 'line-through' },

  laterList: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  laterRow: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 14 },
  laterRowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  laterTime: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moon, width: 92, lineHeight: 14 },
  laterDivider: { width: 1, height: 28, backgroundColor: TOKENS.nightHair },
  laterInfo: { flex: 1, paddingLeft: 4 },
  laterAddress: { fontFamily: TYPOGRAPHY.heading, fontSize: 14, color: TOKENS.moon, marginBottom: 2 },
  laterType: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint, textTransform: 'capitalize' },
  laterPayout: { fontFamily: TYPOGRAPHY.mono, fontSize: 13, color: TOKENS.moon },
  laterCaptureWrap: { paddingHorizontal: 14, paddingBottom: 12 },
  todayCaptureRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  todayCaptureBtn: {
    backgroundColor: TOKENS.moon, borderRadius: 2, paddingHorizontal: 12, paddingVertical: 10,
  },
  todayCaptureBtnText: { color: TOKENS.ink, fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, letterSpacing: 0.4 },
  todayCaptureNone: { marginTop: 12, paddingTop: 10, borderTopWidth: 1, borderTopColor: TOKENS.nightHair },
  todayCaptureNoneText: { fontFamily: TYPOGRAPHY.body, fontSize: 12, color: TOKENS.moonMute, lineHeight: 17 },
});
