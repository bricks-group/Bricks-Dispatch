import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, RefreshControl, Linking, Alert,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChevronLeft, Phone, Navigation, Check, MapPin } from 'lucide-react-native';
import { actorHeaders } from '../../lib/api';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8002';
const VENDOR_RATE = 0.62;

type ChecklistItem = { id: string; label: string; completed: boolean };
type Job = {
  id: string; address: string; city: string; state: string; zip_code: string;
  time_window_start: string; time_window_end: string; job_type: string;
  status: string; property_details: string; client_instructions: string;
  checklist: ChecklistItem[]; deliverables_count: number; uploaded_count: number;
  scheduled_date: string; package_name: string | null; package_price: number | null;
  agent_name: string | null; agent_email: string | null; agent_brokerage: string | null;
};

const STATUS_BADGE: Record<string, { bg: string; fg: string }> = {
  assigned:    { bg: TOKENS.nightDeep,  fg: TOKENS.moonSoft },
  in_progress: { bg: TOKENS.brickTint, fg: TOKENS.brickDeep },
  completed:   { bg: TOKENS.okTint,    fg: TOKENS.ok },
};

function fmtTime(iso: string) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
}

function agentInitials(name: string | null) {
  if (!name) return '??';
  return name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
}

function fmtMoney(amount: number) {
  return `$${amount.toLocaleString('en-US')}`;
}

export default function JobDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [togglingItem, setTogglingItem] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/jobs/${id}`, { headers });
      setJob(await res.json());
    } catch (e) { console.error(e); }
    finally { setLoading(false); setRefreshing(false); }
  }, [id]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleStatus = async () => {
    if (!job || actionLoading) return;
    const next = job.status === 'assigned' ? 'in_progress' : 'completed';
    setActionLoading(true);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const r = await fetch(`${API}/api/jobs/${id}/status`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ status: next }),
      });
      if (r.ok) setJob(await r.json());
    } finally { setActionLoading(false); }
  };

  const toggleChecklist = async (itemId: string, current: boolean) => {
    if (togglingItem) return;
    setTogglingItem(itemId);
    setJob(prev => prev ? {
      ...prev,
      checklist: prev.checklist.map(c => c.id === itemId ? { ...c, completed: !current } : c),
    } : prev);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      await fetch(`${API}/api/jobs/${id}/checklist/${itemId}`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ completed: !current }),
      });
    } catch {
      setJob(prev => prev ? {
        ...prev,
        checklist: prev.checklist.map(c => c.id === itemId ? { ...c, completed: current } : c),
      } : prev);
    } finally { setTogglingItem(null); }
  };

  const openMaps = () => {
    if (!job) return;
    const addr = encodeURIComponent(`${job.address}, ${job.city}, ${job.state}`);
    Linking.openURL(`maps://?q=${addr}`).catch(() =>
      Linking.openURL(`https://maps.google.com/?q=${addr}`)
    );
  };

  const callAgent = () => {
    if (!job?.agent_email) {
      Alert.alert('No contact', 'Agent contact information is unavailable.');
      return;
    }
    Linking.openURL(`mailto:${job.agent_email}`);
  };

  if (loading || !job) {
    return (
      <SafeAreaView style={s.container} edges={['top']}>
        <View style={s.center}><ActivityIndicator size="small" color={TOKENS.brick} /></View>
      </SafeAreaView>
    );
  }

  const badge = STATUS_BADGE[job.status] || STATUS_BADGE.assigned;
  const payout = job.package_price ? Math.round(job.package_price * VENDOR_RATE) : null;
  const completedCount = job.checklist.filter(c => c.completed).length;

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      {/* Top bar */}
      <View style={s.topBar}>
        <TouchableOpacity onPress={() => router.back()} style={s.backBtn} activeOpacity={0.7}>
          <ChevronLeft size={20} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={s.topId}>#{job.id.slice(0, 5).toUpperCase()}</Text>
        <View style={[s.badge, { backgroundColor: badge.bg }]}>
          <Text style={[s.badgeText, { color: badge.fg }]}>
            {job.status.replace('_', ' ').toUpperCase()}
          </Text>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={s.scroll}
        showsVerticalScrollIndicator={false}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchData(); }} tintColor={TOKENS.brick} />}
      >
        {/* Hero */}
        <View style={s.hero}>
          <Text style={s.address}>{job.address.toLowerCase()}</Text>
          <Text style={s.addressMeta}>
            {[job.city, job.state].filter(Boolean).join(', ')} · {job.job_type.replace(/_/g, ' ')}
          </Text>
        </View>

        {/* Info grid */}
        <View style={s.grid}>
          {[
            ['ARRIVE', fmtTime(job.time_window_start)],
            ['PACKAGE', job.package_name || '—'],
            ['DATE', job.scheduled_date || '—'],
            ['PAYOUT', payout ? fmtMoney(payout) : '—'],
          ].map(([label, value], i) => (
            <View key={label} style={[s.gridCell, i % 2 === 1 && s.gridCellRight, i >= 2 && s.gridCellBottom]}>
              <Text style={s.gridLabel}>{label}</Text>
              <Text style={s.gridValue}>{value}</Text>
            </View>
          ))}
        </View>

        {/* Agent contact */}
        {job.agent_name && (
          <View style={s.section}>
            <Text style={s.sectionLabel}>CONTACT</Text>
            <View style={s.contactCard}>
              <View style={s.contactAvatar}>
                <Text style={s.contactAvatarText}>{agentInitials(job.agent_name)}</Text>
              </View>
              <View style={s.contactInfo}>
                <Text style={s.contactName}>{job.agent_name}</Text>
                <Text style={s.contactSub}>{job.agent_brokerage || 'Agent'}</Text>
              </View>
              <TouchableOpacity style={s.phoneBtn} onPress={callAgent} activeOpacity={0.7}>
                <Phone size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </TouchableOpacity>
            </View>
          </View>
        )}

        {/* Access */}
        {job.client_instructions ? (
          <View style={s.section}>
            <Text style={s.sectionLabel}>ACCESS</Text>
            <View style={s.accessCard}>
              <MapPin size={13} color={TOKENS.moonFaint} strokeWidth={1.5} style={{ marginTop: 1 }} />
              <Text style={s.accessText}>{job.client_instructions}</Text>
            </View>
          </View>
        ) : null}

        {/* Checklist */}
        {job.checklist.length > 0 && (
          <View style={s.section}>
            <View style={s.sectionHeader}>
              <Text style={s.sectionLabel}>CHECKLIST</Text>
              <Text style={s.checklistCount}>
                {completedCount}/{job.checklist.length}
              </Text>
            </View>
            <View style={s.card}>
              {job.checklist.map((item, i) => (
                <TouchableOpacity
                  key={item.id}
                  style={[s.checkRow, i < job.checklist.length - 1 && s.checkRowBorder]}
                  onPress={() => toggleChecklist(item.id, item.completed)}
                  activeOpacity={0.7}
                  disabled={togglingItem === item.id}
                >
                  <View style={item.completed ? s.chk : s.chkEmpty}>
                    {item.completed && <Check size={10} color="#FFF" strokeWidth={3} />}
                  </View>
                  <Text style={item.completed ? s.checkLabelDone : s.checkLabel}>
                    {item.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        )}

        {/* Property details */}
        {job.property_details ? (
          <View style={s.section}>
            <Text style={s.sectionLabel}>PROPERTY</Text>
            <View style={s.card}>
              <Text style={s.bodyText}>{job.property_details}</Text>
            </View>
          </View>
        ) : null}

        <View style={{ height: 120 }} />
      </ScrollView>

      {/* Sticky footer */}
      {job.status !== 'completed' && (
        <View style={s.footer}>
          <TouchableOpacity style={s.navBtn} onPress={openMaps} activeOpacity={0.7}>
            <Phone size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
          </TouchableOpacity>
          <TouchableOpacity style={s.navBtn} onPress={openMaps} activeOpacity={0.7}>
            <Navigation size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
          </TouchableOpacity>
          <TouchableOpacity
            style={s.primaryBtn}
            onPress={handleStatus}
            disabled={actionLoading}
            activeOpacity={0.8}
          >
            {actionLoading
              ? <ActivityIndicator color="#FFF" size="small" />
              : <Text style={s.primaryBtnText}>
                  {job.status === 'assigned' ? 'START JOB' : 'MARK COMPLETE'}
                </Text>
            }
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },

  topBar: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 24, paddingVertical: 14, borderBottomWidth: 1, borderColor: TOKENS.nightHair },
  backBtn: { marginRight: 12, padding: 2 },
  topId: { flex: 1, fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, color: TOKENS.moonMute, letterSpacing: 1.4 },
  badge: { paddingHorizontal: 8, paddingVertical: 4, borderRadius: 2 },
  badgeText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, letterSpacing: 1 },

  scroll: { paddingHorizontal: 24, paddingTop: 24 },
  hero: { marginBottom: 24 },
  address: { fontSize: 26, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.5, marginBottom: 6 },
  addressMeta: { fontSize: 12, color: TOKENS.moonMute, fontFamily: TYPOGRAPHY.mono, textTransform: 'uppercase', letterSpacing: 0.6 },

  grid: { flexDirection: 'row', flexWrap: 'wrap', backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, marginBottom: 28 },
  gridCell: { width: '50%', padding: 14 },
  gridCellRight: { borderLeftWidth: 1, borderLeftColor: TOKENS.nightHair },
  gridCellBottom: { borderTopWidth: 1, borderTopColor: TOKENS.nightHair },
  gridLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.4, marginBottom: 6 },
  gridValue: { fontFamily: TYPOGRAPHY.mono, fontSize: 14, color: TOKENS.moon },

  section: { marginBottom: 24 },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8 },
  checklistCount: { fontFamily: TYPOGRAPHY.mono, fontSize: 10, color: TOKENS.moonFaint },

  card: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },

  contactCard: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, padding: 14 },
  contactAvatar: { width: 36, height: 36, borderRadius: 2, backgroundColor: TOKENS.brickSoft + '33', alignItems: 'center', justifyContent: 'center' },
  contactAvatarText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 11, color: TOKENS.brick },
  contactInfo: { flex: 1 },
  contactName: { fontSize: 14, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, marginBottom: 2 },
  contactSub: { fontSize: 11, color: TOKENS.moonFaint },
  phoneBtn: { padding: 8, backgroundColor: TOKENS.nightDeep, borderRadius: 2 },

  accessCard: { flexDirection: 'row', gap: 10, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, padding: 14 },
  accessText: { flex: 1, fontSize: 13, color: TOKENS.moonSoft, lineHeight: 20 },

  checkRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 13 },
  checkRowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  chk: { width: 18, height: 18, borderRadius: 2, backgroundColor: TOKENS.brick, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  chkEmpty: { width: 18, height: 18, borderRadius: 2, borderWidth: 1.5, borderColor: TOKENS.nightHairS, flexShrink: 0 },
  checkLabel: { fontSize: 13, color: TOKENS.moon, flex: 1 },
  checkLabelDone: { fontSize: 13, color: TOKENS.moonFaint, textDecorationLine: 'line-through', flex: 1 },

  bodyText: { fontSize: 13, color: TOKENS.moonSoft, lineHeight: 20, padding: 14 },

  footer: { position: 'absolute', bottom: 0, left: 0, right: 0, flexDirection: 'row', gap: 10, paddingHorizontal: 24, paddingVertical: 16, paddingBottom: 32, backgroundColor: TOKENS.night, borderTopWidth: 1, borderColor: TOKENS.nightHair },
  navBtn: { width: 48, height: 48, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, alignItems: 'center', justifyContent: 'center' },
  primaryBtn: { flex: 1, height: 48, backgroundColor: TOKENS.brick, borderRadius: 2, alignItems: 'center', justifyContent: 'center' },
  primaryBtnText: { color: '#FFF', fontFamily: TYPOGRAPHY.monoMedium, fontSize: 13, letterSpacing: 1 },
});
