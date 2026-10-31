import { useState, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, Alert, Linking, Modal, KeyboardAvoidingView, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnterFadeDown, SheetEnter } from '../../components/motion';
import {
  NavBar, Footer, Section, Group, Row, Button, IconButton, TextLink, Field, Pill, Notice, StatGrid,
  Overline, EmptyState, LoadingState, Check,
} from '../../components/ui';
import { User, MapPin, CheckCircle, Navigation, Phone, AlertTriangle, X, Home, Box, LayoutGrid, Video, Camera, Upload as UploadIcon } from 'lucide-react-native';
import { useNoShow, formatCountdown, LOCATION_REQUIRED_COPY, NOSHOW_RULE_COPY, NOSHOW_REVIEW_COPY } from '../../lib/noshow';
import { actorHeaders } from '../../lib/api';
import { isPickerMember, isVendorAdmin, TeamMember } from '../../lib/vendor';
import { getCaptureSession } from '../../lib/capture-api';
import type { CaptureKind } from '../../lib/capture-api';
import { captureNeitherCopy, captureStatusCopy, jobCaptureModes } from '../../lib/capture-modes';
import { formatShootWindow } from '../../lib/shoot-window';
import {
  BRACKET_HINT,
  validateHdrUploadsForComplete,
} from '../../lib/bracket-require';
import { color, type as T, space, radius, layout, hairline, type Tone } from '../../theme';


const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

type ChecklistItem = { id: string; label: string; completed: boolean };
type ListingHit = { id: string; address: string; city?: string; deal_type?: string | null };
type Job = {
  id: string; address: string; city: string; state: string;
  timezone?: string | null;
  zip_code?: string | null;
  time_window_start: string; time_window_end?: string | null;
  preferred_window?: string | null;
  confirmed_arrive_at?: string | null;
  job_type: string; status: string;
  scheduled_date: string; package_name: string | null; package_price: number | null;
  agent_name: string | null; agent_phone: string | null;
  client_instructions: string | null;
  checklist: ChecklistItem[];
  package_id?: string | null;
  add_ons?: unknown;
  capture?: { tour?: boolean; floorplan?: boolean; visit?: boolean } | null;
  vendor_payout?: number | null;
  lat?: number | null;
  lng?: number | null;
  transaction_id?: string | null;
  listing_address?: string | null;
  listing?: ListingHit | null;
  assignee_id?: string | null;
  assignee_name?: string | null;
};

function fmtDate(s: string) {
  if (!s) return '—';
  const d = new Date(s + 'T00:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function fmtJobType(t: string) {
  return t.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const STATUS_TONE: Record<string, Tone> = {
  pending: 'warn',
  assigned: 'accent',
  in_progress: 'accent',
  completed: 'ok',
  cancelled: 'bad',
  agent_no_show: 'bad',
};

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState(false);
  const [arriveAt, setArriveAt] = useState('');
  const [confirmingArrive, setConfirmingArrive] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [escalationSentAt, setEscalationSentAt] = useState<number | null>(null);
  const [noShowVisible, setNoShowVisible] = useState(false);
  const [noShowNotes, setNoShowNotes] = useState('');
  const [listingQuery, setListingQuery] = useState('');
  const [listingHits, setListingHits] = useState<ListingHit[]>([]);
  const [listingSearching, setListingSearching] = useState(false);
  const [listingSaving, setListingSaving] = useState(false);
  const [listingError, setListingError] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [photographers, setPhotographers] = useState<TeamMember[]>([]);
  const [pick, setPick] = useState('');
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState('');
  const [captureStatusRows, setCaptureStatusRows] = useState<{ kind: CaptureKind; status: string; line: string }[]>([]);

  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        const headers = await actorHeaders();
        const [jobRes, profileRes, teamRes] = await Promise.all([
          fetch(`${API}/api/vendor-os/jobs/${id}`, { headers }),
          fetch(`${API}/api/vendor-os/profile`, { headers }),
          fetch(`${API}/api/vendor-os/team?page=1&page_size=200`, { headers }),
        ]);
        if (!jobRes.ok) throw new Error(`${jobRes.status}`);
        const jobData: Job = await jobRes.json();
        setJob(jobData);
        setPick(jobData.assignee_id || '');
        if (profileRes.ok) {
          const profile = await profileRes.json();
          const admin = isVendorAdmin(profile);
          setIsAdmin(admin);
          if (admin && teamRes.ok) {
            const team = await teamRes.json();
            const items: TeamMember[] = Array.isArray(team.items) ? team.items : [];
            setPhotographers(items.filter(isPickerMember));
          }
        }
      } catch {
        Alert.alert('Error', 'Could not load job details.');
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  const noShow = useNoShow(job?.id, job?.status);
  const ns = noShow.ns;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!job) { setCaptureStatusRows([]); return; }
      const flags = jobCaptureModes(job);
      const kinds: CaptureKind[] = [];
      if (flags.tour) kinds.push('splat');
      if (flags.floorplan) kinds.push('floorplan');
      if (kinds.length === 0) { setCaptureStatusRows([]); return; }
      const next: { kind: CaptureKind; status: string; line: string }[] = [];
      for (const kind of kinds) {
        const s = await getCaptureSession(job.id, kind);
        const status = (s.status || '').toLowerCase();
        if (!status || (status === 'in_progress' && (s.frameCount || 0) === 0)) continue;
        const line = captureStatusCopy(status, kind);
        if (!line && !status) continue;
        next.push({ kind, status, line: line || status });
      }
      if (!cancelled) setCaptureStatusRows(next);
    })();
    return () => { cancelled = true; };
  }, [job?.id, job?.capture, job?.package_id, job?.package_name, job?.job_type, job?.add_ons, job?.checklist, job?.client_instructions]);

  const searchListings = async (text?: string) => {
    if (!job) return;
    const q = (text ?? listingQuery).trim();
    setListingError('');
    if (!q) {
      setListingHits([]);
      return;
    }
    setListingSearching(true);
    try {
      const headers = await actorHeaders();
      const res = await fetch(
        `${API}/api/vendor-os/listings?q=${encodeURIComponent(q)}&job_id=${encodeURIComponent(job.id)}`,
        { headers },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setListingHits(Array.isArray(data.items) ? data.items : []);
    } catch {
      setListingError('Could not search listings.');
      setListingHits([]);
    } finally {
      setListingSearching(false);
    }
  };

  const assignJob = async (memberId: string) => {
    if (!job || assigning) return;
    setAssigning(true);
    setAssignError('');
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/assign`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ member_id: memberId || 'unassign' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);
      }
      setJob((prev) => prev ? {
        ...prev,
        assignee_id: data.assignee_id ?? null,
        assignee_name: data.assignee_name ?? (data.assignee_id ? prev.assignee_name : 'Unassigned'),
        status: data.status || prev.status,
      } : prev);
      setPick(data.assignee_id || '');
    } catch (e: any) {
      setAssignError(e.message || 'Could not assign.');
    } finally {
      setAssigning(false);
    }
  };

  const attachListing = async (transactionId: string | null) => {
    if (!job) return;
    setListingSaving(true);
    setListingError('');
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/listing`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({ transaction_id: transactionId }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setJob(prev => prev ? {
        ...prev,
        transaction_id: updated.transaction_id ?? null,
        listing_address: updated.listing_address ?? null,
        listing: updated.listing ?? null,
      } : prev);
      setListingHits([]);
      setListingQuery('');
    } catch (e: any) {
      setListingError(e.message || 'Could not attach listing.');
    } finally {
      setListingSaving(false);
    }
  };

  useEffect(() => {
    if (job) setArriveAt((job.confirmed_arrive_at || job.time_window_start || '').slice(0, 5));
  }, [job?.id, job?.confirmed_arrive_at, job?.time_window_start]);

  const confirmArrive = async () => {
    if (!job || confirmingArrive) return;
    const clock = (arriveAt || '').trim().slice(0, 5);
    if (!/^\d{1,2}:\d{2}$/.test(clock)) {
      Alert.alert('Arrive time', 'Enter a time like 10:30');
      return;
    }
    setConfirmingArrive(true);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/confirm-arrive`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ arrive_at: clock }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || `HTTP ${res.status}`);
      }
      const updated = await res.json();
      setJob(prev => prev ? {
        ...prev,
        confirmed_arrive_at: updated.confirmed_arrive_at ?? clock,
        time_window_start: updated.time_window_start ?? prev.time_window_start,
        time_window_end: updated.time_window_end ?? prev.time_window_end,
        preferred_window: updated.preferred_window ?? prev.preferred_window,
      } : prev);
      Alert.alert('Sent', 'Agent emailed with your arrive time.');
    } catch (e: any) {
      Alert.alert('Could not confirm', e.message || 'Try again.');
    } finally {
      setConfirmingArrive(false);
    }
  };

  const markComplete = async () => {
    if (!job) return;
    setActioning(true);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      // Client mirror of web validateHdrUploadsForComplete; API enforces the same gate.
      let uploads: { filename?: string; media_type?: string | null }[] = [];
      try {
        const upRes = await fetch(`${API}/api/uploads?job_id=${encodeURIComponent(job.id)}`, { headers });
        if (upRes.ok) {
          const raw = await upRes.json();
          uploads = Array.isArray(raw) ? raw : (raw.items || raw.data || []);
        }
      } catch {
        // Fall through — API will still reject incomplete brackets.
      }
      const bracketBlock = validateHdrUploadsForComplete(uploads);
      if (bracketBlock) {
        Alert.alert('Incomplete brackets', bracketBlock.message || BRACKET_HINT);
        return;
      }
      Alert.alert('Mark complete', 'Is this shoot finished?', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Mark complete',
          onPress: async () => {
            setActioning(true);
            try {
              const hdrs = await actorHeaders({ 'Content-Type': 'application/json' });
              const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/status`, {
                method: 'PUT',
                headers: hdrs,
                body: JSON.stringify({ status: 'completed' }),
              });
              if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                const detail = err.detail;
                const msg = (detail && typeof detail === 'object' && detail.message)
                  || (typeof detail === 'string' ? detail : null)
                  || `Could not update job status (${res.status}).`;
                throw new Error(msg);
              }
              setJob(prev => prev ? { ...prev, status: 'completed' } : prev);
            } catch (e: any) {
              Alert.alert('Cannot complete', e?.message || 'Could not update job status.');
            } finally {
              setActioning(false);
            }
          },
        },
      ]);
    } catch (e: any) {
      Alert.alert('Cannot complete', e?.message || 'Could not check uploads.');
    } finally {
      setActioning(false);
    }
  };

  const openMaps = () => {
    if (!job) return;
    const addr = encodeURIComponent(`${job.address}, ${job.city}, ${job.state}`);
    Linking.openURL(`https://maps.apple.com/?daddr=${addr}`);
  };

  const callAgent = () => {
    if (job?.agent_phone) Linking.openURL(`tel:${job.agent_phone}`);
  };

  const notifyAgent = async () => {
    if (!job || escalating) return;
    setEscalating(true);
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/escalate-absent-agent`, { method: 'POST', headers });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data.detail && data.detail.message) || (typeof data.detail === 'string' ? data.detail : '') || `HTTP ${res.status}`);
      setEscalationSentAt(Date.now());
      void noShow.refresh();
      Alert.alert(data.agent_notified ? 'Text sent' : 'No text sent', data.agent_notified ? 'We texted the client that you’re on site.' : 'We don’t have a number for this client.');
    } catch (e: any) {
      Alert.alert('Could not text the client', e.message || 'Try again.');
    } finally {
      setEscalating(false);
    }
  };

  const checkIn = async () => {
    if (!job) return;
    try {
      await noShow.checkIn();
      setJob(prev => prev ? { ...prev, status: 'in_progress' } : prev);
    } catch (e: any) {
      Alert.alert('Could not check in', e?.message || 'Try again.');
    }
  };

  const callClient = async () => {
    try { await noShow.callClient(); } catch (e: any) { Alert.alert('Could not log the call', e?.message || 'Try again.'); }
  };

  const takeNoShowPhoto = async () => {
    try { await noShow.takePhoto(); } catch (e: any) { Alert.alert('Photo not saved', e?.message || 'Try again.'); }
  };

  const submitNoShow = async () => {
    if (!job || noShow.busy === 'report') return;
    try {
      const data = await noShow.report(noShowNotes);
      setNoShowVisible(false);
      setNoShowNotes('');
      if (data?.hold_for_review) {
        Alert.alert('Held for review', 'Bricks will check the location and review it within 2 business days.');
      } else {
        Alert.alert('Reported', 'No-show reported. Bricks will review it within 2 business days.');
      }
      setJob(prev => prev ? { ...prev, status: 'agent_no_show' } : prev);
    } catch (e: any) {
      Alert.alert('Can’t report yet', e.message || 'Try again.');
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={s.safe}>
        <NavBar title="Job" onBack={() => router.back()} border={false} />
        <LoadingState />
      </SafeAreaView>
    );
  }

  if (!job) {
    return (
      <SafeAreaView style={s.safe}>
        <NavBar title="Job" onBack={() => router.back()} border={false} />
        <EmptyState icon={AlertTriangle} title="Job not found" body="It may have been reassigned or cancelled." />
      </SafeAreaView>
    );
  }

  const statusTone: Tone = STATUS_TONE[job.status] || 'neutral';
  const isComplete = job.status === 'completed';
  const isActive = ['assigned', 'in_progress'].includes(job.status);
  const captureFlags = jobCaptureModes(job);
  const hasCaptureMode = captureFlags.tour || captureFlags.floorplan || captureFlags.visit;
  const openCapture = (kind: CaptureKind | 'visit') => {
    if (kind === 'visit') {
      router.push({ pathname: '/(capture)/visit', params: { jobId: id } } as any);
      return;
    }
    const pathname = kind === 'floorplan' ? '/(capture)/floorplan' : '/(capture)/capture';
    router.push({ pathname, params: { jobId: id, kind } } as any);
  };
  const startCapture = () => {
    const modes = [captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length;
    if (modes > 1) {
      router.push({ pathname: '/(capture)/mode', params: { jobId: id } } as any);
      return;
    }
    if (captureFlags.tour) { openCapture('splat'); return; }
    if (captureFlags.floorplan) { openCapture('floorplan'); return; }
    if (captureFlags.visit) { openCapture('visit'); }
  };
  // Short labels so the footer never wraps at 390. Same targets as before.
  const captureStartLabel = ([captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length > 1)
    ? 'Capture'
    : captureFlags.visit && !captureFlags.tour && !captureFlags.floorplan
      ? 'Virtual visit'
      : captureFlags.tour
        ? '3D tour'
        : 'Floor plan';
  const captureStartIcon = ([captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length > 1)
    ? Camera
    : captureFlags.visit && !captureFlags.tour && !captureFlags.floorplan
      ? Video
      : captureFlags.tour ? Box : LayoutGrid;
  const checkedIn = !!ns?.checked_in;
  const noShowRows = ns && checkedIn ? [
    { done: true, label: ns.check_in_distance_m != null ? `Checked in, about ${Math.round(ns.check_in_distance_m)} m from the property` : 'Checked in' },
    { done: noShow.waitDone, label: noShow.waitDone ? `Waited ${ns.wait_minutes} min` : Number.isFinite(noShow.leftSecs) ? `Wait ${formatCountdown(noShow.leftSecs)} more` : `Wait ${ns.wait_minutes} min` },
    { done: ns.has_photo, label: ns.has_photo ? 'Photo taken' : 'Take a photo of the property' },
    { done: ns.has_call, label: ns.has_call ? (ns.call_method === 'no_number' ? 'Call attempt logged (no number on file)' : 'Call attempt logged') : 'Call the client' },
  ] : [];

  const multiMode = [captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length > 1;
  const showArrive = ['assigned', 'in_progress', 'pending_assignment'].includes(job.status);

  return (
    <SafeAreaView style={s.safe} edges={['top']}>
      <NavBar
        title="Job"
        onBack={() => router.back()}
        right={<View style={s.navPill}><Pill label={job.status.replace(/_/g, ' ')} tone={statusTone} /></View>}
      />

      <ScrollView style={s.flex} contentContainerStyle={s.scrollContent} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
        <EnterFadeDown>
          {/* Hero */}
          <View style={s.hero}>
            <Overline>{[fmtJobType(job.job_type), job.package_name].filter(Boolean).join(' · ')}</Overline>
            <View style={s.heroTitleRow}>
              <View style={s.flex}>
                <Text style={[T.display, { color: color.text }]} accessibilityRole="header">{job.address}</Text>
                <Text style={[T.body, { color: color.textMuted }]}>{[job.city, job.state].filter(Boolean).join(', ')}</Text>
              </View>
              {job.vendor_payout != null ? (
                <Text style={[T.dataLg, { color: color.text }]}>${job.vendor_payout.toFixed(0)}</Text>
              ) : null}
            </View>
          </View>

          <StatGrid items={[
            { label: 'Date', value: fmtDate(job.scheduled_date) },
            { label: 'Window', value: formatShootWindow(job) },
          ]} />

          {!isComplete && !hasCaptureMode ? (
            <View style={s.blockGap}><Notice>{captureNeitherCopy(captureFlags)}</Notice></View>
          ) : null}

          {job.agent_name ? (
            <Section label="Agent">
              <Group>
                <Row
                  icon={User}
                  title={job.agent_name}
                  subtitle={job.agent_phone ? job.agent_phone : 'No phone on file'}
                  right={job.agent_phone ? <IconButton icon={Phone} label={`Call ${job.agent_name}`} outline onPress={callAgent} /> : undefined}
                />
              </Group>
            </Section>
          ) : null}

          {showArrive ? (
            <Section label="Arrival">
              <View style={s.arriveRow}>
                <View style={s.flex}>
                  <Field
                    boxStyle={s.arriveField}
                    value={arriveAt}
                    onChangeText={setArriveAt}
                    placeholder="10:30"
                    keyboardType="numbers-and-punctuation"
                    accessibilityLabel="Exact arrival time"
                  />
                </View>
                <Button
                  variant="secondary"
                  full={false}
                  style={s.arriveButton}
                  title={job.confirmed_arrive_at ? 'Update agent' : 'Tell agent'}
                  onPress={confirmArrive}
                  loading={confirmingArrive}
                />
              </View>
              <Text style={[T.caption, s.hint]}>We email the agent your exact time.</Text>
            </Section>
          ) : null}

          {job.client_instructions ? (
            <Section label="Instructions">
              <Text style={[T.body, { color: color.textBody }]}>{job.client_instructions}</Text>
            </Section>
          ) : null}

          {job.checklist?.length > 0 ? (
            <Section label="Shoot checklist">
              <Group>
                {job.checklist.map((item) => (
                  <View key={item.id} style={s.checkRow}>
                    <CheckCircle size={layout.icon} color={item.completed ? color.ok : color.textFaint} strokeWidth={layout.iconStroke} />
                    <Text style={[T.body, s.flex, { color: item.completed ? color.textFaint : color.text }, item.completed && s.strike]}>
                      {item.label}
                    </Text>
                  </View>
                ))}
              </Group>
            </Section>
          ) : null}

          {(isActive && hasCaptureMode && multiMode) || ['in_progress', 'completed', 'processing'].includes(job.status) ? (
            <Section label="Capture">
              <Group>
                {isActive && multiMode && captureFlags.tour ? <Row icon={Box} title="360° tour" subtitle="Stand at stations and pan a full circle" chevron onPress={() => openCapture('splat')} /> : null}
                {isActive && multiMode && captureFlags.floorplan ? <Row icon={LayoutGrid} title="Floor plan" subtitle="Scan each room" chevron onPress={() => openCapture('floorplan')} /> : null}
                {isActive && multiMode && captureFlags.visit ? <Row icon={Video} title="Virtual visit" subtitle="Walk-through video, one per floor" chevron onPress={() => openCapture('visit')} /> : null}
                {['in_progress', 'completed', 'processing'].includes(job.status) ? (
                  <Row icon={UploadIcon} title="Upload deliverables" subtitle="Stills, drone, reel, twilight, staging, floor plan" chevron onPress={() => router.push('/(tabs)/uploads' as any)} />
                ) : null}
              </Group>
            </Section>
          ) : null}

          {captureStatusRows.length > 0 ? (
            <Section label="Capture status">
              <Group>
                {captureStatusRows.map((r) => {
                  const failed = r.status === 'failed';
                  const ready = r.status === 'delivered';
                  return (
                    <Row
                      key={r.kind}
                      title={r.line}
                      tone={failed ? 'bad' : 'default'}
                      right={<Pill label={failed ? 'Failed' : ready ? 'Ready' : 'Processing'} tone={failed ? 'bad' : ready ? 'ok' : 'warn'} />}
                      chevron
                      accessibilityLabel={failed ? 'Review or retry capture' : 'Open capture review'}
                      onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId: id, kind: r.kind } } as any)}
                    />
                  );
                })}
              </Group>
            </Section>
          ) : null}

          {(job.status === 'assigned' || (job.status === 'in_progress' && !!ns)) && !checkedIn ? (
            <Section label="Check in">
              <Text style={[T.bodySm, { color: color.textBody }]}>{LOCATION_REQUIRED_COPY}</Text>
              <View style={s.blockGapSm}>
                <Button variant="secondary" icon={MapPin} title="Check in" onPress={checkIn} loading={noShow.busy === 'checkin'} />
              </View>
            </Section>
          ) : null}

          {job.status === 'in_progress' && checkedIn && ns ? (
            <Section label="Client isn’t here?">
              <Text style={[T.bodySm, { color: color.textBody }]}>{NOSHOW_RULE_COPY(ns.wait_minutes)}</Text>
              <Group style={s.blockGapSm}>
                {noShowRows.map((r) => (
                  <View key={r.label} style={s.checkRow}>
                    <CheckCircle size={layout.icon} color={r.done ? color.ok : color.textFaint} strokeWidth={layout.iconStroke} />
                    <Text style={[T.body, s.flex, { color: r.done ? color.text : color.textMuted }]}>{r.label}</Text>
                  </View>
                ))}
              </Group>
              <View style={s.buttonRow}>
                <Button style={s.flex} variant="secondary" icon={Phone} title="Call client" onPress={callClient} loading={noShow.busy === 'call'} disabled={!!noShow.busy && noShow.busy !== 'call'} />
                <Button style={s.flex} variant="secondary" icon={Camera} title={ns.has_photo ? 'Retake photo' : 'Take photo'} onPress={takeNoShowPhoto} loading={noShow.busy === 'photo'} disabled={!!noShow.busy && noShow.busy !== 'photo'} />
              </View>
              {!ns.client_phone ? (
                <Text style={[T.caption, s.hint]}>We don’t have a number for this client. Tapping Call client logs that you tried.</Text>
              ) : null}
              {!escalationSentAt ? (
                <TextLink title="Text the client too" onPress={notifyAgent} />
              ) : null}
              {!ns.can_report && noShow.missing.length ? (
                <View style={s.blockGapSm}>
                  <Notice tone="warn" title="Before you can report a no-show">
                    {noShow.missing.join('\n')}
                  </Notice>
                </View>
              ) : null}
              <View style={s.blockGapSm}>
                <Button variant="danger" icon={AlertTriangle} title="Report no-show" onPress={() => setNoShowVisible(true)} disabled={!ns.can_report} />
              </View>
              <Text style={[T.caption, s.hint]}>{NOSHOW_REVIEW_COPY}</Text>
            </Section>
          ) : null}

          <Section label="Assigned to">
            <Group>
              <Row icon={User} title={job.assignee_name || (job.assignee_id ? job.assignee_id : 'Unassigned')} />
              {isAdmin ? (
                <View style={s.pickBlock}>
                  <Text style={[T.caption, { color: color.textMuted }]}>Active photographers only.</Text>
                </View>
              ) : null}
              {isAdmin ? <Row title="Unassigned" right={<Check checked={!pick} />} onPress={() => setPick('')} /> : null}
              {isAdmin ? photographers.map((p) => (
                <Row key={p.id} title={p.name} subtitle={p.title || p.role || 'Photographer'} right={<Check checked={pick === p.id} />} onPress={() => setPick(p.id)} />
              )) : null}
            </Group>
            {isAdmin ? (
              <View style={s.buttonRow}>
                {assignError ? <Text style={[T.caption, s.err]}>{assignError}</Text> : null}
                <Button style={s.flex} title={job.assignee_id ? 'Reassign' : 'Assign'} onPress={() => assignJob(pick)} loading={assigning} />
                <Button style={s.flex} variant="secondary" title="Auto-assign" onPress={() => assignJob('auto')} disabled={assigning} />
              </View>
            ) : null}
          </Section>

          <Section label="Listing">
            <Group>
              <Row
                icon={Home}
                title={job.listing_address || 'Not attached'}
                subtitle={job.listing_address ? (job.listing?.city || undefined) : 'Link this job to an office listing'}
              />
            </Group>
            <View style={s.listingSearch}>
              <View style={s.flex}>
                <Field
                  placeholder="Search listings or paste an ID"
                  value={listingQuery}
                  onChangeText={setListingQuery}
                  autoCapitalize="none"
                  autoCorrect={false}
                  returnKeyType="search"
                  onSubmitEditing={() => searchListings()}
                  error={listingError || undefined}
                />
              </View>
              <Button variant="secondary" full={false} title="Search" onPress={() => searchListings()} loading={listingSearching} disabled={listingSaving} />
            </View>
            {listingHits.length > 0 ? (
              <Group style={s.blockGapSm}>
                {listingHits.map((hit) => (
                  <Row
                    key={hit.id}
                    title={hit.address || 'Listing'}
                    subtitle={[hit.city, hit.deal_type].filter(Boolean).join(' · ') || 'Office listing'}
                    chevron
                    disabled={listingSaving}
                    onPress={() => attachListing(hit.id)}
                  />
                ))}
              </Group>
            ) : null}
            {job.transaction_id ? (
              <TextLink title="Detach listing" onPress={() => attachListing(null)} />
            ) : null}
          </Section>

        </EnterFadeDown>
      </ScrollView>

      <Footer>
        {/* One primary (Mark complete). Navigate + capture are quiet secondaries, equal width. */}
        <View style={s.buttonRowFooter}>
          <Button style={s.flex} variant="secondary" icon={Navigation} title="Navigate" onPress={openMaps} />
          {!isComplete && hasCaptureMode ? (
            <Button style={s.flex} variant="secondary" icon={captureStartIcon} title={captureStartLabel} onPress={startCapture} disabled={actioning} />
          ) : null}
          {!isComplete && !hasCaptureMode ? (
            <Button style={s.flex} icon={CheckCircle} title="Mark complete" onPress={markComplete} loading={actioning} />
          ) : null}
        </View>
        {!isComplete && hasCaptureMode ? (
          <Button style={s.footerPrimary} icon={CheckCircle} title="Mark complete" onPress={markComplete} loading={actioning} />
        ) : null}
      </Footer>

      <Modal visible={noShowVisible} animationType="fade" transparent onRequestClose={() => setNoShowVisible(false)}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={s.flex}>
          <View style={s.modalOverlay}>
            <SheetEnter style={s.modalSheet}>
              <SafeAreaView edges={['bottom']}>
                <View style={s.modalHeader}>
                  <Text style={[T.headline, s.flex, { color: color.text }]}>Report no-show</Text>
                  <IconButton icon={X} label="Close" tint={color.textMuted} onPress={() => { setNoShowVisible(false); setNoShowNotes(''); }} />
                </View>
                <Text style={[T.bodySm, { color: color.textBody }]}>
                  If the client doesn’t show, you’re paid half the job’s payout. {NOSHOW_REVIEW_COPY}
                </Text>
                <View style={s.blockGapSm}>
                  <Field
                    placeholder="What happened? (optional)"
                    multiline
                    numberOfLines={4}
                    value={noShowNotes}
                    onChangeText={setNoShowNotes}
                    maxLength={500}
                    style={s.notes}
                  />
                </View>
                <Button style={s.blockGapSm} variant="danger" title="Confirm no-show" onPress={submitNoShow} loading={noShow.busy === 'report'} />
              </SafeAreaView>
            </SheetEnter>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1 },
  safe: { flex: 1, backgroundColor: color.bg },
  navPill: { paddingRight: space[12] },
  scrollContent: { paddingHorizontal: layout.gutter, paddingBottom: space[48] },
  hero: { paddingTop: space[20], paddingBottom: space[20], gap: space[10] },
  heroTitleRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space[16] },
  blockGap: { marginTop: space[16] },
  blockGapSm: { marginTop: space[12] },
  arriveRow: { flexDirection: 'row', alignItems: 'stretch' },
  arriveField: { borderTopRightRadius: 0, borderBottomRightRadius: 0, borderRightWidth: 0 },
  arriveButton: { borderTopLeftRadius: 0, borderBottomLeftRadius: 0 },
  footerPrimary: { marginTop: space[10] },
  hint: { color: color.textFaint, marginTop: space[8] },
  checkRow: { flexDirection: 'row', alignItems: 'flex-start', gap: space[12], paddingHorizontal: space[16], paddingVertical: space[14] },
  strike: { textDecorationLine: 'line-through' },
  pickBlock: { paddingHorizontal: space[16], paddingVertical: space[10] },
  buttonRow: { flexDirection: 'row', flexWrap: 'wrap', gap: space[10], marginTop: space[12] },
  buttonRowFooter: { flexDirection: 'row', gap: space[10] },
  err: { color: color.bad, width: '100%' },
  listingSearch: { flexDirection: 'row', alignItems: 'flex-start', gap: space[10], marginTop: space[12] },
  modalOverlay: { flex: 1, backgroundColor: color.scrim, justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: color.surface, borderTopWidth: hairline, borderColor: color.hairStrong,
    borderTopLeftRadius: radius.sm, borderTopRightRadius: radius.sm,
    paddingHorizontal: layout.gutter, paddingTop: space[8], paddingBottom: space[16],
  },
  modalHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: space[8], marginRight: -space[12] },
  notes: { minHeight: 96, textAlignVertical: 'top' },
});
