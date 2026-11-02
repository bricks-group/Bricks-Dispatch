import { useState, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, Linking, Modal, TextInput, KeyboardAvoidingView, Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { EnterFadeDown, PressScale, SheetEnter } from '../../components/motion';
import { ArrowLeft, MapPin, Clock, Camera, User, CheckCircle, Navigation, Phone, AlertTriangle, X, Home } from 'lucide-react-native';
import { useNoShow, LOCATION_REQUIRED_COPY, NOSHOW_RULE_COPY, NOSHOW_REVIEW_COPY, formatCountdown } from '../../lib/noshow';
import { actorHeaders } from '../../lib/api';
import { CANCEL_REASONS, HANDBACK_RULE_COPY, IMMEDIATE_FAMILY_COPY, handbackBody } from '../../lib/legal-terms';
import { isPickerMember, isVendorAdmin, TeamMember } from '../../lib/vendor';
import { getCaptureSession } from '../../lib/capture-api';
import type { CaptureKind } from '../../lib/capture-api';
import { captureNeitherCopy, captureStatusCopy, jobCaptureModes } from '../../lib/capture-modes';
import { formatShootWindow } from '../../lib/shoot-window';
import {
  BRACKET_HINT,
  validateHdrUploadsForComplete,
} from '../../lib/bracket-require';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';
import { TradeChecklist, ChecklistPalette } from '../../components/TradeChecklist';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

const CHECKLIST_PALETTE: ChecklistPalette = {
  text: TOKENS.moon,
  muted: TOKENS.moonMute,
  faint: TOKENS.moonFaint,
  line: TOKENS.nightHairS,
  surface: TOKENS.nightDeep,
  accent: TOKENS.brick,
  accentText: TOKENS.moon,
  ok: TOKENS.ok,
  error: TOKENS.warn,
};

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
  checklist_title?: string | null;
  trade_job?: boolean;
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

const STATUS_COLOR: Record<string, string> = {
  pending: TOKENS.warn,
  assigned: TOKENS.brick,
  in_progress: TOKENS.brick,
  completed: TOKENS.ok,
  cancelled: TOKENS.bad,
  agent_no_show: TOKENS.bad,
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
  const [submittingNoShow, setSubmittingNoShow] = useState(false);
  const [handbackVisible, setHandbackVisible] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelNote, setCancelNote] = useState('');
  const [handingBack, setHandingBack] = useState(false);
  const [handbackErr, setHandbackErr] = useState('');
  const noShow = useNoShow(id, job?.status);
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
      const bracketBlock = job.trade_job ? null : validateHdrUploadsForComplete(uploads);
      if (bracketBlock) {
        Alert.alert('Incomplete HDR brackets', bracketBlock.message || BRACKET_HINT);
        return;
      }
      Alert.alert('Mark complete', job.trade_job ? 'Is this job finished?' : 'Confirm that this shoot is finished?', [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Mark Complete',
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
      if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.detail || `HTTP ${res.status}`); }
      setEscalationSentAt(Date.now());
    } catch (e: any) {
      Alert.alert('Could not notify agent', e.message || 'Try again.');
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
    try { await noShow.callClient(); } catch (e: any) {
      Alert.alert('Could not log the call', e?.message || 'Try again.');
    }
  };

  const takeNoShowPhoto = async () => {
    try { await noShow.takePhoto(); } catch (e: any) {
      Alert.alert('Could not save the photo', e?.message || 'Try again.');
    }
  };

  const submitNoShow = async () => {
    if (!job || submittingNoShow) return;
    setSubmittingNoShow(true);
    try {
      const data = await noShow.report(noShowNotes);
      setNoShowVisible(false);
      setNoShowNotes('');
      setJob(prev => prev ? { ...prev, status: 'agent_no_show' } : prev);
      Alert.alert(
        data?.hold_for_review ? 'Sent for review' : 'No-show reported',
        `You’ll be paid half the job’s payout if it’s approved. ${NOSHOW_REVIEW_COPY}`,
      );
    } catch (e: any) {
      Alert.alert('Could not report the no-show', e.message || 'Try again.');
    } finally {
      setSubmittingNoShow(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={s.safe}>
        <ActivityIndicator size="large" color={TOKENS.brick} style={{ flex: 1 }} />
      </SafeAreaView>
    );
  }

  if (!job) {
    return (
      <SafeAreaView style={s.safe}>
        <TouchableOpacity style={s.backBtn} onPress={() => router.back()}>
          <ArrowLeft size={20} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
          <Text style={s.emptyText}>Job not found.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const statusColor = STATUS_COLOR[job.status] || TOKENS.moonMute;
  const closeHandback = () => { setHandbackVisible(false); setCancelReason(''); setCancelNote(''); setHandbackErr(''); };
  const submitHandback = async () => {
    if (!cancelReason || handingBack) return;
    setHandingBack(true); setHandbackErr('');
    try {
      const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/status`, {
        method: 'PUT',
        headers: await actorHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(handbackBody(cancelReason, cancelNote)),
      });
      if (!res.ok) {
        let msg = "Couldn't hand back this job. Try again.";
        try { const b = await res.json(); const d = b?.detail ?? b?.message; if (typeof d === 'string' && d) msg = d; else if (typeof d?.message === 'string') msg = d.message; } catch {}
        throw new Error(msg);
      }
      setJob(prev => prev ? { ...prev, status: 'declined' } : prev);
      closeHandback();
    } catch (e: any) {
      setHandbackErr(e?.message || "Couldn't hand back this job. Try again.");
    } finally {
      setHandingBack(false);
    }
  };

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
  const captureStartLabel = ([captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length > 1)
    ? 'Start Capture'
    : captureFlags.visit && !captureFlags.tour && !captureFlags.floorplan
      ? 'Deliver Visit'
      : captureFlags.tour
        ? 'Start Capture'
        : 'Start Capture · Floor plan';
  const ns = noShow.ns;
  const checkedIn = !!ns?.checked_in;
  const showCheckIn = (job.status === 'assigned' || (job.status === 'in_progress' && !!ns)) && !checkedIn;
  const showNoShow = job.status === 'in_progress' && checkedIn;
  const distanceLine = ns?.check_in_distance_m != null
    ? `Checked in ${Math.round(ns.check_in_distance_m)} m from the property`
    : 'Checked in';
  const waitLine = noShow.waitDone
    ? `Waited ${ns?.wait_minutes ?? 20} minutes`
    : Number.isFinite(noShow.leftSecs)
      ? `Wait ${formatCountdown(noShow.leftSecs)} more`
      : `Wait ${ns?.wait_minutes ?? 20} minutes`;
  const noShowChecks: { done: boolean; label: string }[] = [
    { done: checkedIn, label: distanceLine },
    { done: noShow.waitDone, label: waitLine },
    { done: !!ns?.has_photo, label: ns?.has_photo ? 'Photo of the property taken' : 'Take a photo of the property' },
    { done: !!ns?.has_call, label: ns?.has_call ? 'Call attempt logged' : 'Try to call the client' },
  ];
  const canReportNoShow = !!ns?.can_report || (noShow.waitDone && checkedIn && !!ns?.has_photo && !!ns?.has_call && !ns?.expired);

  return (
    <SafeAreaView style={s.safe}>
      <View style={s.header}>
        <TouchableOpacity style={s.backBtn} onPress={() => router.back()}>
          <ArrowLeft size={20} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.headerTitle}>JOB DETAIL</Text>
        <View style={[s.statusBadge, { backgroundColor: statusColor + '22', borderColor: statusColor }]}>
          <Text style={[s.statusText, { color: statusColor }]}>{job.status.toUpperCase()}</Text>
        </View>
      </View>

      <EnterFadeDown distance={10} duration={260} style={{ flex: 1 }}>
      <ScrollView style={s.scroll} contentContainerStyle={s.scrollContent} showsVerticalScrollIndicator={false}>
        <View style={s.heroRow}>
          <View style={s.heroIcon}>
            <Camera size={22} color={TOKENS.brick} strokeWidth={1.5} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={s.jobType}>{fmtJobType(job.job_type)}</Text>
            {job.package_name && <Text style={s.packageName}>{job.package_name}</Text>}
          </View>
          {job.vendor_payout != null && (
            <Text style={s.payoutAmount}>${job.vendor_payout.toFixed(0)}</Text>
          )}
        </View>

        <View style={s.card}>
          <InfoRow icon={<MapPin size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="ADDRESS">
            <Text style={s.infoValue}>{job.address}</Text>
            <Text style={s.infoSub}>{job.city}, {job.state}</Text>
          </InfoRow>
          <View style={s.divider} />
          <InfoRow icon={<Clock size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="SCHEDULED">
            <Text style={s.infoValue}>{fmtDate(job.scheduled_date)}</Text>
            <Text style={s.infoSub}>{formatShootWindow(job)}</Text>
          </InfoRow>
          {['assigned', 'in_progress', 'pending_assignment'].includes(job.status) ? (
            <>
              <View style={s.divider} />
              <InfoRow icon={<Clock size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="CONFIRM ARRIVE">
                <TextInput
                  value={arriveAt}
                  onChangeText={setArriveAt}
                  placeholder="10:30"
                  placeholderTextColor={TOKENS.moonMute}
                  keyboardType="numbers-and-punctuation"
                  style={{
                    marginTop: 6,
                    borderWidth: 1,
                    borderColor: '#E5E0D8',
                    borderRadius: 2,
                    paddingHorizontal: 10,
                    paddingVertical: 8,
                    color: TOKENS.moon,
                    fontSize: 16,
                  }}
                />
                <TouchableOpacity
                  onPress={confirmArrive}
                  disabled={confirmingArrive}
                  style={{
                    marginTop: 8,
                    backgroundColor: TOKENS.brick,
                    borderRadius: 2,
                    paddingVertical: 10,
                    alignItems: 'center',
                    opacity: confirmingArrive ? 0.6 : 1,
                  }}
                >
                  {confirmingArrive ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={{ color: '#fff', fontWeight: '600' }}>
                      {job.confirmed_arrive_at ? 'Update agent' : 'Tell agent'}
                    </Text>
                  )}
                </TouchableOpacity>
                <Text style={[s.infoSub, { marginTop: 6 }]}>
                  Exact time inside the window — we email the agent.
                </Text>
              </InfoRow>
            </>
          ) : null}
          {job.agent_name && (
            <>
              <View style={s.divider} />
              <InfoRow icon={<User size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="AGENT">
                <Text style={s.infoValue}>{job.agent_name}</Text>
              </InfoRow>
            </>
          )}
        </View>

        <View style={s.card}>
          <InfoRow icon={<User size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="ASSIGNED TO">
            <Text style={s.infoValue}>{job.assignee_name || (job.assignee_id ? job.assignee_id : 'Unassigned')}</Text>
          </InfoRow>
          {isAdmin ? (
            <>
              <View style={s.divider} />
              <Text style={s.assignHint}>Active photographers. Pending applicants are not listed.</Text>
              <TouchableOpacity
                style={[s.photogRow, !pick && s.photogRowOn]}
                onPress={() => setPick('')}
                activeOpacity={0.7}
              >
                <Text style={s.infoValue}>Unassigned</Text>
              </TouchableOpacity>
              {photographers.map((p) => (
                <TouchableOpacity
                  key={p.id}
                  style={[s.photogRow, pick === p.id && s.photogRowOn]}
                  onPress={() => setPick(p.id)}
                  activeOpacity={0.7}
                >
                  <Text style={s.infoValue}>{p.name}</Text>
                  <Text style={s.infoSub}>{p.title || p.role || 'Photographer'}</Text>
                </TouchableOpacity>
              ))}
              {assignError ? <Text style={s.listingErr}>{assignError}</Text> : null}
              <View style={s.listingActions}>
                <TouchableOpacity
                  style={s.listingBtn}
                  onPress={() => assignJob(pick)}
                  disabled={assigning}
                  activeOpacity={0.7}
                >
                  {assigning
                    ? <ActivityIndicator size="small" color={TOKENS.moon} />
                    : <Text style={s.listingBtnText}>{job.assignee_id ? 'Reassign' : 'Assign'}</Text>}
                </TouchableOpacity>
                <TouchableOpacity
                  style={s.listingBtnGhost}
                  onPress={() => assignJob('auto')}
                  disabled={assigning}
                  activeOpacity={0.7}
                >
                  <Text style={s.listingBtnGhostText}>Auto</Text>
                </TouchableOpacity>
              </View>
            </>
          ) : null}
        </View>

        <View style={s.card}>
          <InfoRow icon={<Home size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="LISTING">
            {job.listing_address ? (
              <>
                <Text style={s.infoValue}>{job.listing_address}</Text>
                {job.listing?.city ? <Text style={s.infoSub}>{job.listing.city}</Text> : null}
              </>
            ) : (
              <Text style={s.infoSub}>Not attached</Text>
            )}
          </InfoRow>
          <View style={s.divider} />
          <TextInput
            style={s.listingInput}
            placeholder="Search office listings or paste id"
            placeholderTextColor={TOKENS.moonFaint}
            value={listingQuery}
            onChangeText={setListingQuery}
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="search"
            onSubmitEditing={() => searchListings()}
          />
          <View style={s.listingActions}>
            <TouchableOpacity
              style={s.listingBtn}
              onPress={() => searchListings()}
              disabled={listingSearching || listingSaving}
              activeOpacity={0.7}
            >
              {listingSearching
                ? <ActivityIndicator size="small" color={TOKENS.moon} />
                : <Text style={s.listingBtnText}>Search</Text>}
            </TouchableOpacity>
            {job.transaction_id ? (
              <TouchableOpacity
                style={s.listingBtnGhost}
                onPress={() => attachListing(null)}
                disabled={listingSaving}
                activeOpacity={0.7}
              >
                <Text style={s.listingBtnGhostText}>Detach</Text>
              </TouchableOpacity>
            ) : null}
          </View>
          {listingError ? <Text style={s.listingErr}>{listingError}</Text> : null}
          {listingHits.map((hit) => (
            <TouchableOpacity
              key={hit.id}
              style={s.listingHit}
              onPress={() => attachListing(hit.id)}
              disabled={listingSaving}
              activeOpacity={0.7}
            >
              <Text style={s.infoValue}>{hit.address || 'Listing'}</Text>
              <Text style={s.infoSub}>{[hit.city, hit.deal_type].filter(Boolean).join(' · ') || 'Office listing'}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {job.client_instructions ? (
          <View style={s.card}>
            <Text style={s.sectionEye}>INSTRUCTIONS</Text>
            <Text style={s.instructions}>{job.client_instructions}</Text>
          </View>
        ) : null}

        {job.trade_job ? (
          <View style={s.card}>
            <Text style={s.sectionEye}>{(job.checklist_title || 'Job checklist').toUpperCase()}</Text>
            <TradeChecklist jobId={job.id} status={job.status} palette={CHECKLIST_PALETTE} />
          </View>
        ) : job.checklist?.length > 0 ? (
          <View style={s.card}>
            <Text style={s.sectionEye}>{job.checklist_title ? `${job.checklist_title.toUpperCase()} · ONE VISIT` : 'SHOOT CHECKLIST · ONE VISIT'}</Text>
            {job.checklist.map((item) => (
              <View key={item.id} style={s.checkRow}>
                <CheckCircle
                  size={16}
                  color={item.completed ? TOKENS.ok : TOKENS.moonMute}
                  strokeWidth={1.5}
                />
                <Text style={[s.checkLabel, item.completed && s.checkLabelDone]}>
                  {item.label}
                </Text>
              </View>
            ))}
          </View>
        ) : null}

        {showCheckIn && (
          <View style={s.noShowCard}>
            <Text style={s.sectionEye}>CHECK IN</Text>
            <Text style={s.noShowDesc}>Check in when you’re at the property. {LOCATION_REQUIRED_COPY}</Text>
            <TouchableOpacity style={s.notifyBtn} onPress={checkIn} disabled={noShow.busy === 'checkin'} activeOpacity={0.7}>
              {noShow.busy === 'checkin'
                ? <ActivityIndicator size="small" color={TOKENS.moon} />
                : <><MapPin size={14} color={TOKENS.moon} strokeWidth={1.5} /><Text style={s.notifyBtnText}>Check in</Text></>
              }
            </TouchableOpacity>
          </View>
        )}

        {showNoShow && (
          <View style={s.noShowCard}>
            <Text style={s.sectionEye}>CLIENT ISN’T HERE?</Text>
            <Text style={s.noShowDesc}>{NOSHOW_RULE_COPY(ns?.wait_minutes ?? 20)}</Text>
            {!noShow.waitDone && Number.isFinite(noShow.leftSecs) ? (
              <Text style={s.countdown}>{formatCountdown(noShow.leftSecs)}</Text>
            ) : null}
            {noShowChecks.map((c) => (
              <View key={c.label} style={s.checkRow}>
                <CheckCircle size={16} color={c.done ? TOKENS.ok : TOKENS.moonMute} strokeWidth={1.5} />
                <Text style={[s.checkLabel, c.done && { color: TOKENS.moon }]}>{c.label}</Text>
              </View>
            ))}
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 10, marginBottom: 10 }}>
              <TouchableOpacity style={[s.notifyBtn, { flex: 1 }]} onPress={callClient} disabled={noShow.busy === 'call'} activeOpacity={0.7}>
                {noShow.busy === 'call'
                  ? <ActivityIndicator size="small" color={TOKENS.moon} />
                  : <><Phone size={14} color={TOKENS.moon} strokeWidth={1.5} /><Text style={s.notifyBtnText}>Call client</Text></>
                }
              </TouchableOpacity>
              <TouchableOpacity style={[s.notifyBtn, { flex: 1 }]} onPress={takeNoShowPhoto} disabled={noShow.busy === 'photo'} activeOpacity={0.7}>
                {noShow.busy === 'photo'
                  ? <ActivityIndicator size="small" color={TOKENS.moon} />
                  : <><Camera size={14} color={TOKENS.moon} strokeWidth={1.5} /><Text style={s.notifyBtnText}>{ns?.has_photo ? 'Retake photo' : 'Take photo'}</Text></>
                }
              </TouchableOpacity>
            </View>
            {ns && !ns.client_phone ? (
              <Text style={s.noShowDesc}>There’s no phone number for this client. Tap Call client to note that you tried, and Bricks will see it.</Text>
            ) : null}
            <TouchableOpacity onPress={notifyAgent} disabled={escalating || !!escalationSentAt} activeOpacity={0.7} style={{ marginBottom: 12 }}>
              <Text style={[s.noShowDesc, { marginBottom: 0, textDecorationLine: 'underline' }]}>
                {escalationSentAt ? 'Text sent to the client' : escalating ? 'Sending text…' : 'Text the client too'}
              </Text>
            </TouchableOpacity>
            {noShow.missing.length > 0 ? (
              <Text style={[s.noShowDesc, { color: TOKENS.warn }]}>Still needed: {noShow.missing.join(' ')}</Text>
            ) : null}
            <TouchableOpacity
              style={[s.reportBtn, !canReportNoShow && { opacity: 0.4 }]}
              onPress={() => setNoShowVisible(true)}
              disabled={!canReportNoShow}
              activeOpacity={0.7}
            >
              <AlertTriangle size={14} color={TOKENS.bad} strokeWidth={1.5} />
              <Text style={s.reportBtnText}>Report no-show</Text>
            </TouchableOpacity>
            <Text style={[s.noShowDesc, { marginTop: 12, marginBottom: 0 }]}>{NOSHOW_REVIEW_COPY}</Text>
          </View>
        )}

        {job.status === 'assigned' ? (
          <View style={s.noShowCard}>
            <Text style={s.handbackLabel}>Can't make it?</Text>
            <Text style={s.noShowDesc}>{HANDBACK_RULE_COPY}</Text>
            <TouchableOpacity style={s.notifyBtn} onPress={() => setHandbackVisible(true)} activeOpacity={0.7} testID="handback-open">
              <Text style={s.notifyBtnText}>Can't make it</Text>
            </TouchableOpacity>
          </View>
        ) : job.status === 'declined' ? (
          <View style={s.noShowCard}>
            <Text style={[s.noShowDesc, { marginBottom: 0 }]}>You handed back this job.</Text>
          </View>
        ) : null}

        {['assigned', 'in_progress'].includes(job.status) && hasCaptureMode && ([captureFlags.tour, captureFlags.floorplan, captureFlags.visit].filter(Boolean).length > 1) ? (
          <View style={s.captureStartBlock}>
            {captureFlags.tour ? (
              <PressScale style={s.captureStartChip} onPress={() => openCapture('splat')}>
                <Text style={s.captureStartChipText}>Start Capture · 3D tour</Text>
              </PressScale>
            ) : null}
            {captureFlags.floorplan ? (
              <PressScale style={s.captureStartChip} onPress={() => openCapture('floorplan')}>
                <Text style={s.captureStartChipText}>Start Capture · Floor plan</Text>
              </PressScale>
            ) : null}
            {captureFlags.visit ? (
              <PressScale style={s.captureStartChip} onPress={() => openCapture('visit')}>
                <Text style={s.captureStartChipText}>Deliver Visit</Text>
              </PressScale>
            ) : null}
          </View>
        ) : null}
        {!job.trade_job && ['in_progress', 'completed', 'processing'].includes(job.status) ? (
          <PressScale
            style={s.captureStartChip}
            onPress={() => router.push('/(tabs)/uploads' as any)}
          >
            <Text style={s.captureStartChipText}>Upload stills · drone · reel</Text>
          </PressScale>
        ) : null}

        {captureStatusRows.length > 0 ? (
          <View style={s.captureStatusBlock}>
            <Text style={s.sectionEye}>CAPTURE STATUS</Text>
            {captureStatusRows.map((r) => {
              const failed = r.status === 'failed';
              const ready = r.status === 'delivered';
              const compiling = ['submitted', 'splatting', 'tracing'].includes(r.status);
              const tone = failed ? s.captureStatusFailed : ready ? s.captureStatusReady : compiling ? s.captureStatusCompiling : s.captureStatusRow;
              return (
                <View key={r.kind} style={[s.captureStatusRow, tone]}>
                  <Text style={s.captureStatusLine}>{r.line}</Text>
                  <TouchableOpacity
                    onPress={() => router.push({ pathname: '/(capture)/review', params: { jobId: id, kind: r.kind } } as any)}
                    activeOpacity={0.7}
                  >
                    <Text style={s.captureStatusLink}>{failed ? 'Review / retry' : 'Open review'}</Text>
                  </TouchableOpacity>
                </View>
              );
            })}
          </View>
        ) : null}

      </ScrollView>
      </EnterFadeDown>

      <View style={s.footer}>
        <PressScale style={s.secondaryBtn} onPress={openMaps} scaleTo={0.97}>
          <Navigation size={16} color={TOKENS.moon} strokeWidth={1.5} />
          <Text style={s.secondaryBtnText}>Navigate</Text>
        </PressScale>
        {job.agent_phone ? (
          <PressScale style={s.secondaryBtn} onPress={callAgent} scaleTo={0.97}>
            <Phone size={16} color={TOKENS.moon} strokeWidth={1.5} />
            <Text style={s.secondaryBtnText}>Call Agent</Text>
          </PressScale>
        ) : null}
        {!isComplete && job.status !== 'declined' ? (
          <>
            <PressScale
              style={[s.primaryBtn, actioning && { opacity: 0.5 }]}
              onPress={markComplete}
              disabled={actioning}
            >
              {actioning
                ? <ActivityIndicator size="small" color={TOKENS.moon} />
                : <Text style={s.primaryBtnText}>Mark Complete</Text>
              }
            </PressScale>
            {hasCaptureMode ? (
              <PressScale
                style={[s.captureBtn, actioning && { opacity: 0.5 }]}
                onPress={startCapture}
                disabled={actioning}
              >
                {actioning
                  ? <ActivityIndicator size="small" color={TOKENS.moon} />
                  : <Text style={s.captureBtnText}>{captureStartLabel}</Text>
                }
              </PressScale>
            ) : job.trade_job ? null : (
              <View style={s.captureNone}>
                <Text style={s.captureNoneText}>{captureNeitherCopy(captureFlags)}</Text>
              </View>
            )}
          </>
        ) : null}
      </View>

      <Modal visible={noShowVisible} animationType="fade" transparent>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
          <View style={s.modalOverlay}>
            <SheetEnter style={s.modalSheet}>
              <View style={s.modalHeader}>
                <Text style={s.sectionEye}>REPORT NO-SHOW</Text>
                <TouchableOpacity onPress={() => { setNoShowVisible(false); setNoShowNotes(''); }} hitSlop={12}>
                  <X size={18} color={TOKENS.moonMute} strokeWidth={1.5} />
                </TouchableOpacity>
              </View>
              <Text style={s.noShowDesc}>
                Bricks will review your check-in, photo and call attempt. If it’s approved, you’re paid half the job’s payout.
              </Text>
              <TextInput
                style={s.notesInput}
                placeholder="What happened? (optional)"
                placeholderTextColor={TOKENS.moonFaint}
                multiline
                numberOfLines={4}
                value={noShowNotes}
                onChangeText={setNoShowNotes}
                maxLength={500}
              />
              <PressScale
                style={[s.submitBtn, submittingNoShow && { opacity: 0.5 }]}
                onPress={submitNoShow}
                disabled={submittingNoShow}
              >
                {submittingNoShow
                  ? <ActivityIndicator size="small" color={TOKENS.moon} />
                  : <Text style={s.submitBtnText}>Confirm no-show</Text>
                }
              </PressScale>
            </SheetEnter>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      <Modal visible={handbackVisible} animationType="fade" transparent onRequestClose={closeHandback}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
          <View style={s.modalOverlay}>
            <SheetEnter style={[s.modalSheet, { maxHeight: '90%' }]}>
              <ScrollView keyboardShouldPersistTaps="handled">
                <View style={s.modalHeader}>
                  <Text style={s.handbackLabel}>Hand back this job</Text>
                  <TouchableOpacity onPress={closeHandback} hitSlop={12} accessibilityLabel="Close">
                    <X size={18} color={TOKENS.moonMute} strokeWidth={1.5} />
                  </TouchableOpacity>
                </View>
                <Text style={s.noShowDesc}>{HANDBACK_RULE_COPY}</Text>
                <Text style={s.handbackPick}>{cancelReason ? 'Reason' : 'Pick a reason'}</Text>
                <View style={s.reasonList} accessibilityRole="radiogroup">
                  {CANCEL_REASONS.map(([value, label], i) => {
                    const on = cancelReason === value;
                    return (
                      <TouchableOpacity
                        key={value}
                        style={[s.reasonRow, i > 0 && s.reasonSep]}
                        onPress={() => setCancelReason(value)}
                        accessibilityRole="radio"
                        accessibilityState={{ checked: on }}
                        testID={`handback-reason-${value}`}
                      >
                        <View style={[s.radio, on && s.radioOn]}>{on ? <View style={s.radioDot} /> : null}</View>
                        <Text style={[s.reasonText, on && { color: TOKENS.moon }]}>{label}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
                {cancelReason === 'medical' || cancelReason === 'bereavement' ? (
                  <Text style={s.noShowDesc}>{IMMEDIATE_FAMILY_COPY}</Text>
                ) : null}
                <TextInput
                  style={s.notesInput}
                  placeholder="Anything Bricks should know (optional)"
                  placeholderTextColor={TOKENS.moonFaint}
                  multiline
                  numberOfLines={3}
                  value={cancelNote}
                  onChangeText={setCancelNote}
                  maxLength={1000}
                  testID="handback-note"
                />
                {handbackErr ? <Text style={[s.noShowDesc, { color: TOKENS.bad }]} accessibilityRole="alert">{handbackErr}</Text> : null}
                <TouchableOpacity
                  style={[s.notifyBtn, { height: 48 }, (!cancelReason || handingBack) && { opacity: 0.4 }]}
                  onPress={submitHandback}
                  disabled={!cancelReason || handingBack}
                  activeOpacity={0.7}
                  accessibilityState={{ disabled: !cancelReason || handingBack }}
                  testID="handback-submit"
                >
                  {handingBack
                    ? <ActivityIndicator size="small" color={TOKENS.moon} />
                    : <Text style={s.notifyBtnText}>Hand back job</Text>}
                </TouchableOpacity>
              </ScrollView>
            </SheetEnter>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

function InfoRow({ icon, label, children }: {
  icon: React.ReactNode; label: string; children: React.ReactNode;
}) {
  return (
    <View style={s.infoRow}>
      <View style={s.infoIcon}>{icon}</View>
      <View style={{ flex: 1 }}>
        <Text style={s.infoLabel}>{label}</Text>
        {children}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: TOKENS.night },

  header: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 20, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair,
  },
  backBtn: { width: 36, height: 36, justifyContent: 'center', alignItems: 'flex-start' },
  headerTitle: {
    flex: 1, fontFamily: TYPOGRAPHY.mono, fontSize: 11,
    color: TOKENS.moonMute, letterSpacing: 1.6,
  },
  statusBadge: {
    borderWidth: 1, borderRadius: TOKENS.radius,
    paddingHorizontal: 8, paddingVertical: 3,
  },
  statusText: { fontFamily: TYPOGRAPHY.mono, fontSize: 10, letterSpacing: 1 },

  scroll: { flex: 1 },
  scrollContent: { padding: 20, paddingBottom: 120, gap: 12 },

  heroRow: { flexDirection: 'row', alignItems: 'center', gap: 14, marginBottom: 4 },
  heroIcon: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: TOKENS.nightSoft, justifyContent: 'center', alignItems: 'center',
  },
  jobType: { fontSize: 20, fontWeight: '500' as const, color: TOKENS.moon, letterSpacing: -0.4 },
  packageName: { fontSize: 13, color: TOKENS.moonMute, marginTop: 2 },
  payoutAmount: { fontFamily: TYPOGRAPHY.mono, fontSize: 22, color: TOKENS.moon },

  card: {
    backgroundColor: TOKENS.nightSoft, borderRadius: TOKENS.radius,
    borderWidth: 1, borderColor: TOKENS.nightHair, padding: 16,
  },
  sectionEye: {
    fontFamily: TYPOGRAPHY.mono, fontSize: 10, color: TOKENS.moonMute,
    letterSpacing: 1.6, marginBottom: 12,
  },

  infoRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, paddingVertical: 10 },
  infoIcon: { width: 20, marginTop: 2 },
  infoLabel: {
    fontFamily: TYPOGRAPHY.mono, fontSize: 9, color: TOKENS.moonMute,
    letterSpacing: 1.4, marginBottom: 4,
  },
  infoValue: { fontSize: 15, fontWeight: '500' as const, color: TOKENS.moon },
  infoSub: { fontSize: 13, color: TOKENS.moonSoft, marginTop: 2 },
  divider: { height: 1, backgroundColor: TOKENS.nightHair },

  instructions: { fontSize: 14, color: TOKENS.moonSoft, lineHeight: 22 },

  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 7 },
  checkLabel: { fontSize: 14, color: TOKENS.moonSoft, flex: 1 },
  checkLabelDone: { color: TOKENS.moonFaint, textDecorationLine: 'line-through' },

  emptyText: { fontSize: 15, color: TOKENS.moonMute },

  footer: {
    flexDirection: 'row', gap: 10,
    paddingHorizontal: 20, paddingTop: 12, paddingBottom: 34,
    borderTopWidth: 1, borderTopColor: TOKENS.nightHair,
    backgroundColor: TOKENS.night,
  },
  secondaryBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 14, height: 48, borderRadius: TOKENS.radius,
    borderWidth: 1, borderColor: TOKENS.nightHairS,
  },
  secondaryBtnText: { fontSize: 14, color: TOKENS.moon, fontWeight: '500' as const },
  primaryBtn: {
    flex: 1, height: 48, borderRadius: TOKENS.radius,
    backgroundColor: TOKENS.brick, justifyContent: 'center', alignItems: 'center',
  },
  primaryBtnText: { fontSize: 15, color: TOKENS.moon, fontWeight: '600' as const },
  noshowBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 6,
    paddingHorizontal: 14, height: 48, borderRadius: TOKENS.radius,
    borderWidth: 1, borderColor: TOKENS.bad + '66',
    backgroundColor: TOKENS.bad + '11',
  },
  noshowBtnText: { fontSize: 14, color: TOKENS.bad, fontWeight: '500' as const },

  noShowCard: {
    backgroundColor: TOKENS.nightSoft, borderRadius: TOKENS.radius,
    borderWidth: 1, borderColor: TOKENS.nightHairS,
    padding: 16, marginBottom: 8,
  },
  noShowDesc: { fontSize: 13, color: TOKENS.moonSoft, lineHeight: 20, marginBottom: 14 },
  notifyBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    justifyContent: 'center',
    borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius,
    paddingVertical: 11, paddingHorizontal: 14,
  },
  notifyBtnText: { fontSize: 14, color: TOKENS.moon, fontWeight: '500' as const },
  countdown: {
    fontFamily: TYPOGRAPHY.mono, fontSize: 28, color: TOKENS.moon,
    letterSpacing: 2, textAlign: 'center' as const, marginBottom: 4,
  },
  reportBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderWidth: 1, borderColor: TOKENS.bad + '66',
    backgroundColor: TOKENS.bad + '11', borderRadius: TOKENS.radius,
    paddingVertical: 11, paddingHorizontal: 14,
  },
  reportBtnText: { fontSize: 14, color: TOKENS.bad, fontWeight: '500' as const },
  handbackLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 15, color: TOKENS.moon, marginBottom: 8 },
  handbackPick: { fontSize: 13, color: TOKENS.moonMute, marginBottom: 8 },
  reasonList: { borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius, marginBottom: 14 },
  reasonRow: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 46, paddingHorizontal: 14, paddingVertical: 10 },
  reasonSep: { borderTopWidth: 1, borderTopColor: TOKENS.nightHair },
  reasonText: { flex: 1, fontSize: 14, color: TOKENS.moonSoft },
  radio: { width: 18, height: 18, borderRadius: 9, borderWidth: 1, borderColor: TOKENS.moonMute, alignItems: 'center', justifyContent: 'center' },
  radioOn: { borderColor: TOKENS.moon },
  radioDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: TOKENS.moon },
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)', justifyContent: 'flex-end' },
  modalSheet: {
    backgroundColor: TOKENS.nightSoft, borderTopLeftRadius: TOKENS.radius, borderTopRightRadius: TOKENS.radius,
    padding: 24, paddingBottom: 40,
    borderTopWidth: 1, borderColor: TOKENS.nightHairS,
  },
  modalHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  notesInput: {
    borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius,
    backgroundColor: TOKENS.night, color: TOKENS.moon,
    padding: 12, fontSize: 14, lineHeight: 22,
    minHeight: 100, textAlignVertical: 'top' as const, marginBottom: 16,
  },
  submitBtn: {
    height: 48, borderRadius: TOKENS.radius, backgroundColor: TOKENS.bad,
    alignItems: 'center', justifyContent: 'center',
  },
  submitBtnText: { fontSize: 15, color: TOKENS.moon, fontWeight: '600' as const },


  listingInput: {
    borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius,
    backgroundColor: TOKENS.night, color: TOKENS.moon,
    paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, marginTop: 8,
  },
  listingActions: { flexDirection: 'row', gap: 8, marginTop: 10, marginBottom: 4 },
  listingBtn: {
    height: 40, paddingHorizontal: 14, borderRadius: TOKENS.radius,
    backgroundColor: TOKENS.brick, justifyContent: 'center', alignItems: 'center',
  },
  listingBtnText: { fontSize: 13, color: TOKENS.moon, fontWeight: '600' as const },
  listingBtnGhost: {
    height: 40, paddingHorizontal: 14, borderRadius: TOKENS.radius,
    borderWidth: 1, borderColor: TOKENS.nightHairS, justifyContent: 'center',
  },
  listingBtnGhostText: { fontSize: 13, color: TOKENS.moonMute, fontWeight: '500' as const },
  listingErr: { fontSize: 13, color: TOKENS.bad, marginTop: 8 },
  listingHit: {
    marginTop: 8, paddingVertical: 10, paddingHorizontal: 4,
    borderTopWidth: 1, borderTopColor: TOKENS.nightHair,
  },
  photogRow: {
    marginTop: 8, paddingVertical: 10, paddingHorizontal: 10,
    borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: TOKENS.radius,
  },
  photogRowOn: { borderColor: TOKENS.brick, backgroundColor: TOKENS.brick + '14' },
  assignHint: { fontSize: 12, color: TOKENS.moonFaint, marginTop: 10, marginBottom: 4 },
  // Capture flow — Start Capture button shown on active jobs
  captureBtn: {
    flex: 1, height: 48, borderRadius: TOKENS.radius,
    backgroundColor: TOKENS.moon, justifyContent: 'center', alignItems: 'center',
    marginLeft: 10,
  },
  captureBtnText: { fontSize: 15, color: TOKENS.ink, fontWeight: '600' as const },
  captureNone: {
    flex: 1, minHeight: 48, justifyContent: 'center', marginLeft: 10,
  },
  captureNoneText: { fontSize: 12, color: TOKENS.moonMute, lineHeight: 16 },
  captureStartBlock: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 10 },
  captureStartChip: {
    backgroundColor: TOKENS.moon, borderRadius: TOKENS.radius, paddingHorizontal: 12, paddingVertical: 10,
  },
  captureStartChipText: { fontSize: 12, color: TOKENS.ink, fontWeight: '600' as const },
  captureStatusBlock: {
    marginTop: 8, marginBottom: 8, padding: 14,
    backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: TOKENS.radius,
  },
  captureStatusRow: {
    marginTop: 8, padding: 12, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: TOKENS.radius,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10,
  },
  captureStatusCompiling: { borderColor: 'rgba(210, 84, 43, 0.55)' },
  captureStatusReady: { borderColor: 'rgba(47, 107, 79, 0.55)' },
  captureStatusFailed: { borderColor: 'rgba(139, 46, 31, 0.7)', backgroundColor: 'rgba(139, 46, 31, 0.08)' },
  captureStatusLine: { flex: 1, fontSize: 13, color: TOKENS.moon, lineHeight: 18 },
  captureStatusLink: { fontSize: 12, color: TOKENS.brick, fontWeight: '600' as const },
});
