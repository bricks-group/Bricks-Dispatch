import { useState, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, Linking,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { ArrowLeft, MapPin, Clock, Camera, User, CheckCircle, Navigation, Phone } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://localhost:8002';

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = {
  id: string; address: string; city: string; state: string;
  time_window_start: string; job_type: string; status: string;
  scheduled_date: string; package_name: string | null; package_price: number | null;
  agent_name: string | null; agent_phone: string | null;
  client_instructions: string | null;
  checklist: ChecklistItem[];
  vendor_payout?: number | null;
};

function fmtTime(iso: string) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
}

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
};

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [actioning, setActioning] = useState(false);

  useEffect(() => {
    if (!id) return;
    (async () => {
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/vendor-os/jobs/${id}`, { headers });
        if (!res.ok) throw new Error(`${res.status}`);
        setJob(await res.json());
      } catch {
        Alert.alert('Error', 'Could not load job details.');
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  const markComplete = async () => {
    if (!job) return;
    Alert.alert('Mark Complete', 'Confirm that this shoot is finished?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Mark Complete',
        onPress: async () => {
          setActioning(true);
          try {
            const headers = await actorHeaders({ 'Content-Type': 'application/json' });
            const res = await fetch(`${API}/api/vendor-os/jobs/${job.id}/status`, {
              method: 'PATCH',
              headers,
              body: JSON.stringify({ status: 'completed' }),
            });
            if (!res.ok) throw new Error(`${res.status}`);
            setJob(prev => prev ? { ...prev, status: 'completed' } : prev);
          } catch {
            Alert.alert('Error', 'Could not update job status.');
          } finally {
            setActioning(false);
          }
        },
      },
    ]);
  };

  const openMaps = () => {
    if (!job) return;
    const addr = encodeURIComponent(`${job.address}, ${job.city}, ${job.state}`);
    Linking.openURL(`https://maps.apple.com/?daddr=${addr}`);
  };

  const callAgent = () => {
    if (job?.agent_phone) Linking.openURL(`tel:${job.agent_phone}`);
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
  const isComplete = job.status === 'completed';

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
            <Text style={s.infoSub}>{fmtTime(job.time_window_start)}</Text>
          </InfoRow>
          {job.agent_name && (
            <>
              <View style={s.divider} />
              <InfoRow icon={<User size={16} color={TOKENS.moonMute} strokeWidth={1.5} />} label="AGENT">
                <Text style={s.infoValue}>{job.agent_name}</Text>
              </InfoRow>
            </>
          )}
        </View>

        {job.client_instructions ? (
          <View style={s.card}>
            <Text style={s.sectionEye}>INSTRUCTIONS</Text>
            <Text style={s.instructions}>{job.client_instructions}</Text>
          </View>
        ) : null}

        {job.checklist?.length > 0 ? (
          <View style={s.card}>
            <Text style={s.sectionEye}>SHOOT CHECKLIST</Text>
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
      </ScrollView>

      <View style={s.footer}>
        <TouchableOpacity style={s.secondaryBtn} onPress={openMaps}>
          <Navigation size={16} color={TOKENS.moon} strokeWidth={1.5} />
          <Text style={s.secondaryBtnText}>Navigate</Text>
        </TouchableOpacity>
        {job.agent_phone ? (
          <TouchableOpacity style={s.secondaryBtn} onPress={callAgent}>
            <Phone size={16} color={TOKENS.moon} strokeWidth={1.5} />
            <Text style={s.secondaryBtnText}>Call Agent</Text>
          </TouchableOpacity>
        ) : null}
        {!isComplete ? (
          <TouchableOpacity
            style={[s.primaryBtn, actioning && { opacity: 0.5 }]}
            onPress={markComplete}
            disabled={actioning}
          >
            {actioning
              ? <ActivityIndicator size="small" color={TOKENS.moon} />
              : <Text style={s.primaryBtnText}>Mark Complete</Text>
            }
          </TouchableOpacity>
        ) : null}
      </View>
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
});
