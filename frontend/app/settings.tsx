import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, Alert, Linking } from 'react-native';
import { useRouter } from 'expo-router';
import { Bell, CreditCard } from 'lucide-react-native';
import { actorHeaders } from '../lib/api';
import { color, type as T, space } from '../theme';
import { Screen, NavBar, Section, Group, Row, Pill } from '../components/ui';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function SettingsScreen() {
  const router = useRouter();
  const [stripe, setStripe] = useState<{ connected: boolean; connect_status: string } | null>(null);
  const [stripeVisible, setStripeVisible] = useState(false);
  const [stripeBusy, setStripeBusy] = useState(false);
  const [stripeErr, setStripeErr] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const headers = await actorHeaders();
        const res = await fetch(`${API}/api/vendor-os/stripe-connect/status`, { headers });
        if (res.status === 403) {
          setStripeVisible(false);
          return;
        }
        if (!res.ok) {
          setStripeVisible(true);
          setStripeErr('Could not load payout status.');
          return;
        }
        const data = await res.json();
        setStripe({ connected: !!data.connected, connect_status: data.connect_status || 'not_connected' });
        setStripeVisible(true);
      } catch {
        setStripeVisible(false);
      }
    })();
  }, []);

  const startStripe = async () => {
    setStripeBusy(true);
    setStripeErr('');
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/stripe-connect/onboard`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ return_url: 'https://dispatch.brxs.ca/settings?stripe=return' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);
      if (!data.url) throw new Error('Stripe did not return an onboarding URL.');
      await Linking.openURL(data.url);
    } catch (e: any) {
      setStripeErr(e.message || 'Could not start Stripe onboarding.');
      Alert.alert('Payouts', e.message || 'Could not start Stripe onboarding.');
    } finally {
      setStripeBusy(false);
    }
  };

  const stripeStatus = stripe == null
    ? (stripeErr || 'Status unavailable')
    : stripe.connected
      ? 'Connected. Job payouts go to this account.'
      : stripe.connect_status === 'pending'
        ? 'Pending. Finish Stripe setup to enable payouts.'
        : stripe.connect_status === 'restricted'
          ? 'Restricted. Open Stripe to fix requirements.'
          : 'Not connected. Owner payouts are the full package, no deduction.';
  const stripeTone = stripe?.connected ? 'ok' : stripe?.connect_status === 'restricted' ? 'bad' : stripe?.connect_status === 'pending' ? 'warn' : 'neutral';
  const stripeAction = stripe?.connected
    ? 'Update Stripe account'
    : stripe?.connect_status === 'pending' || stripe?.connect_status === 'restricted'
      ? 'Finish Stripe setup'
      : 'Connect Stripe';

  return (
    <Screen header={<NavBar title="Settings" onBack={() => router.back()} />}>
      <View style={s.top} />
      {stripeVisible ? (
        <Section first label="Payouts">
          <Group>
            <Row
              icon={CreditCard}
              title="Stripe Connect"
              subtitle={stripeStatus}
              right={stripe && (stripe.connected || stripe.connect_status !== 'not_connected') ? <Pill label={stripe.connected ? 'Connected' : stripe.connect_status.replace(/_/g, ' ')} tone={stripeTone as any} /> : undefined}
            />
            <Row title={stripeAction} tone="accent" chevron onPress={startStripe} loading={stripeBusy} />
          </Group>
          {stripeErr && stripe ? <Text style={[T.caption, s.err]}>{stripeErr}</Text> : null}
        </Section>
      ) : null}
      <Section first={!stripeVisible} label="Notifications">
        <Group>
          <Row
            icon={Bell}
            title="Notification settings"
            subtitle="Job and payout alerts are managed in your phone’s Settings."
            chevron
            onPress={() => Linking.openSettings().catch(() => {})}
          />
        </Group>
      </Section>
    </Screen>
  );
}

const s = StyleSheet.create({
  top: { height: space[24] },
  err: { color: color.bad, marginTop: space[8] },
});
