import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Share, Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { CreditCard, FileText, ChevronRight } from 'lucide-react-native';
import { actorHeaders } from '../../../lib/api';
import { TOKENS, TYPOGRAPHY } from '../../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://10.0.0.206:8002';

type PayoutSummary = {
  week_total: number;
  paid_amount: number;
  pending_amount: number;
  payout_day: string;
  week_job_count: number;
  recent_jobs: RecentJob[];
};

type RecentJob = {
  id: string;
  address: string;
  scheduled_date: string;
  status: string;
  payout_amount: number;
  payout_status: 'Paid' | 'Pending';
};

function fmtMoney(cents: number) {
  return `$${cents.toLocaleString('en-US')}`;
}

function fmtDate(iso: string) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();
  if (d.toDateString() === today) return 'Today';
  if (d.toDateString() === yesterday) return 'Yesterday';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function PayoutsScreen() {
  const [data, setData] = useState<PayoutSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchPayouts = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/payouts`, { headers: actorHeaders() });
      const json: PayoutSummary = await res.json();
      setData(json);
    } catch (e) {
      console.error('Payouts fetch error', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchPayouts(); }, [fetchPayouts]);

  const handleExport = async () => {
    if (!data) return;
    const lines = [
      'Bricks Payout Export',
      `Week total: ${fmtMoney(data.week_total)}`,
      `Paid: ${fmtMoney(data.paid_amount)} · Pending: ${fmtMoney(data.pending_amount)}`,
      '',
      ...data.recent_jobs.map(j =>
        `${fmtDate(j.scheduled_date)} · ${j.address} · ${fmtMoney(j.payout_amount)} · ${j.payout_status}`
      ),
    ];
    await Share.share({ message: lines.join('\n') });
  };

  const paidRatio = data && data.week_total > 0
    ? data.paid_amount / data.week_total
    : 0;

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      {loading ? (
        <View style={s.center}>
          <ActivityIndicator size="small" color={TOKENS.brick} />
        </View>
      ) : (
        <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
          <Text style={s.screenTitle}>Payouts</Text>

          {/* Weekly earnings card */}
          <View style={s.card}>
            <Text style={s.cardLabel}>THIS WEEK</Text>
            <Text style={s.weekTotal}>{fmtMoney(data?.week_total ?? 0)}</Text>
            <Text style={s.cardMeta}>
              Pays out {data?.payout_day ?? 'Monday'} · {data?.week_job_count ?? 0} jobs
            </Text>
            <View style={s.barTrack}>
              <View style={[s.barPaid, { flex: paidRatio }]} />
              <View style={[s.barPending, { flex: 1 - paidRatio }]} />
            </View>
            <View style={s.barLabels}>
              <Text style={s.barLabelText}>PAID {fmtMoney(data?.paid_amount ?? 0)}</Text>
              <Text style={s.barLabelText}>PENDING {fmtMoney(data?.pending_amount ?? 0)}</Text>
            </View>
          </View>

          {/* Recent jobs */}
          <View style={s.section}>
            <Text style={s.sectionLabel}>RECENT JOBS</Text>
            <View style={s.menuCard}>
              {(data?.recent_jobs ?? []).slice(0, 8).map((job, i, arr) => (
                <View
                  key={job.id}
                  style={[s.jobRow, i < arr.length - 1 && s.rowBorder]}
                >
                  <View style={s.jobLeft}>
                    <Text style={s.jobAddress}>{job.address.toLowerCase()}</Text>
                    <Text style={s.jobDate}>{fmtDate(job.scheduled_date)}</Text>
                  </View>
                  <View style={s.jobRight}>
                    <Text style={s.jobAmount}>{fmtMoney(job.payout_amount)}</Text>
                    <Text style={[
                      s.jobStatus,
                      { color: job.payout_status === 'Paid' ? TOKENS.ok : TOKENS.warn },
                    ]}>
                      {job.payout_status.toUpperCase()}
                    </Text>
                  </View>
                </View>
              ))}
              {(data?.recent_jobs?.length ?? 0) === 0 && (
                <View style={s.emptyRow}>
                  <Text style={s.emptyText}>No jobs yet this period</Text>
                </View>
              )}
            </View>
          </View>

          {/* Payout account actions */}
          <View style={s.section}>
            <Text style={s.sectionLabel}>PAYOUT</Text>
            <View style={s.menuCard}>
              {[
                { Icon: CreditCard, label: 'Payout method', sub: '···· 9012' },
                { Icon: FileText,   label: 'Tax documents',  sub: null },
              ].map(({ Icon, label, sub }, i) => (
                <TouchableOpacity
                  key={label}
                  style={[s.menuRow, s.rowBorder]}
                  activeOpacity={0.7}
                >
                  <View style={s.menuIconWrap}>
                    <Icon size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
                  </View>
                  <View style={s.menuText}>
                    <Text style={s.menuLabel}>{label}</Text>
                    {sub && <Text style={s.menuSub}>{sub}</Text>}
                  </View>
                  <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* Export */}
          <View style={s.section}>
            <TouchableOpacity style={s.exportRow} onPress={handleExport} activeOpacity={0.7}>
              <FileText size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              <Text style={s.exportLabel}>Export earnings history</Text>
            </TouchableOpacity>
          </View>

          <View style={{ height: 120 }} />
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  scroll: { paddingHorizontal: 24, paddingTop: 16 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  screenTitle: { fontSize: 26, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.4, marginBottom: 20 },

  card: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHairS, borderRadius: 2, padding: 20, marginBottom: 28 },
  cardLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonMute, letterSpacing: 1.8, marginBottom: 10 },
  weekTotal: { fontFamily: TYPOGRAPHY.mono, fontSize: 36, color: TOKENS.moon, letterSpacing: -0.8, marginBottom: 4 },
  cardMeta: { fontSize: 11, color: TOKENS.moonFaint, marginBottom: 16 },
  barTrack: { height: 3, borderRadius: 2, overflow: 'hidden', flexDirection: 'row', backgroundColor: TOKENS.nightHair, marginBottom: 8 },
  barPaid: { backgroundColor: TOKENS.brick, height: '100%' },
  barPending: { backgroundColor: TOKENS.moonFaint, height: '100%' },
  barLabels: { flexDirection: 'row', justifyContent: 'space-between' },
  barLabelText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1 },

  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 12 },

  menuCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },

  jobRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 14 },
  jobLeft: { flex: 1 },
  jobAddress: { fontSize: 13, color: TOKENS.moon, marginBottom: 3 },
  jobDate: { fontFamily: TYPOGRAPHY.mono, fontSize: 11, color: TOKENS.moonFaint },
  jobRight: { alignItems: 'flex-end' },
  jobAmount: { fontFamily: TYPOGRAPHY.mono, fontSize: 13, color: TOKENS.moon, marginBottom: 3 },
  jobStatus: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, letterSpacing: 1 },

  emptyRow: { padding: 20, alignItems: 'center' },
  emptyText: { fontSize: 13, color: TOKENS.moonFaint },

  menuRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 14, gap: 12 },
  menuIconWrap: { width: 28, alignItems: 'center' },
  menuText: { flex: 1 },
  menuLabel: { fontSize: 14, color: TOKENS.moon, marginBottom: 2 },
  menuSub: { fontSize: 11, color: TOKENS.moonFaint },

  exportRow: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 14 },
  exportLabel: { fontSize: 14, color: TOKENS.moonSoft },
});
