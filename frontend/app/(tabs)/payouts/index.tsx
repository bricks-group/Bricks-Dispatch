import { useRouter } from 'expo-router';
import { fmtMoneyCompact, fmtMoneyExact } from '../../../lib/money';
import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, Share } from 'react-native';
import { CreditCard, FileText, Wallet } from 'lucide-react-native';
import { actorHeaders } from '../../../lib/api';
import { color, type as T, space } from '../../../theme';
import { Screen, PageHeader, Section, Group, Row, Pill, Dot, Overline, EmptyState, LoadingState } from '../../../components/ui';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

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

const fmtMoney = (v: number) => fmtMoneyExact(v);

function fmtDate(iso: string) {
  if (!iso) return '—';
  // Date-only strings parse as UTC; read them as local days.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
  if (isNaN(d.getTime())) return iso;
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 86400000).toDateString();
  if (d.toDateString() === today) return 'Today';
  if (d.toDateString() === yesterday) return 'Yesterday';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function PayoutsScreen() {
  const router = useRouter();
  const [data, setData] = useState<PayoutSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchPayouts = useCallback(async () => {
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/payouts`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

  const jobs = (data?.recent_jobs ?? []).slice(0, 8);

  return (
    <Screen>
      <PageHeader title="Payouts" />

      {loading && !data ? (
        <LoadingState />
      ) : (
        <>
          <View style={s.hero}>
            <Overline>This week</Overline>
            <Text style={[T.kpi, s.total]} accessibilityLabel={fmtMoneyExact(data?.week_total ?? 0)}>{fmtMoneyCompact(data?.week_total ?? 0)}</Text>
            <Text style={[T.bodySm, { color: color.textMuted }]}>
              Pays out {data?.payout_day ?? 'Monday'} · {data?.week_job_count ?? 0} job{(data?.week_job_count ?? 0) === 1 ? '' : 's'}
            </Text>
            <View style={s.barTrack}>
              <View style={[s.barPaid, { flex: paidRatio }]} />
              <View style={[s.barPending, { flex: 1 - paidRatio }]} />
            </View>
            <View style={s.legend}>
              <View style={s.legendItem}><Dot tone="accent" /><Text style={[T.dataSm, { color: color.textBody }]}>Paid {fmtMoney(data?.paid_amount ?? 0)}</Text></View>
              <View style={s.legendItem}><Dot tone="neutral" /><Text style={[T.dataSm, { color: color.textBody }]}>Pending {fmtMoney(data?.pending_amount ?? 0)}</Text></View>
            </View>
          </View>

          <Section label="Recent jobs">
            {jobs.length === 0 ? (
              <EmptyState icon={Wallet} title="No jobs this period" body="Delivered jobs show up here." />
            ) : (
              <Group>
                {jobs.map((job) => (
                  <View key={job.id} style={s.jobRow}>
                    <View style={s.flex}>
                      <Text style={[T.bodyMedium, { color: color.text }]}>{job.address}</Text>
                      <Text style={[T.caption, { color: color.textMuted }]}>{fmtDate(job.scheduled_date)}</Text>
                    </View>
                    <View style={s.jobRight}>
                      <Text style={[T.data, { color: color.text }]}>{fmtMoney(job.payout_amount)}</Text>
                      <Pill label={job.payout_status} tone={job.payout_status === 'Paid' ? 'ok' : 'warn'} />
                    </View>
                  </View>
                ))}
              </Group>
            )}
          </Section>

          <Section label="Account">
            <Group>
              <Row icon={CreditCard} title="Payout account" subtitle="Setup and status" chevron onPress={() => router.push('/settings' as any)} />
              <Row icon={FileText} title="Share earnings summary" chevron onPress={handleExport} disabled={!data} />
            </Group>
          </Section>
        </>
      )}
    </Screen>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1, gap: 2 },
  hero: { gap: space[8] },
  total: { color: color.text, marginTop: space[4] },
  barTrack: { height: 4, flexDirection: 'row', backgroundColor: color.hair, marginTop: space[16] },
  barPaid: { backgroundColor: color.accent },
  barPending: { backgroundColor: color.surfaceDeep },
  legend: { flexDirection: 'row', gap: space[20], marginTop: space[4] },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: space[8] },
  jobRow: { flexDirection: 'row', alignItems: 'center', gap: space[12], paddingHorizontal: space[16], paddingVertical: space[14] },
  jobRight: { alignItems: 'flex-end', gap: space[6] },
});
