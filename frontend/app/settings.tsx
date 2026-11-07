import { useCallback, useState } from 'react';
import { View, Text, StyleSheet, Alert, Linking } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { Bell, Calendar, CreditCard } from 'lucide-react-native';
import { actorHeaders } from '../lib/api';
import {
  connectGoogleCalendar,
  disconnectVendorCalendar,
  fetchVendorCalendars,
  googleConnection,
  type CalendarConnection,
} from '../lib/googleCalendarConnect';
import { color, type as T, space } from '../theme';
import { Screen, NavBar, Section, Group, Row, Pill } from '../components/ui';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function SettingsScreen() {
  const router = useRouter();
  const [stripe, setStripe] = useState<{ connected: boolean; connect_status: string } | null>(null);
  const [stripeVisible, setStripeVisible] = useState(false);
  const [stripeBusy, setStripeBusy] = useState(false);
  const [stripeErr, setStripeErr] = useState('');
  const [google, setGoogle] = useState<CalendarConnection | null>(null);
  const [calCopy, setCalCopy] = useState('');
  const [calBusy, setCalBusy] = useState(false);
  const [calErr, setCalErr] = useState('');

  const loadStripe = useCallback(async () => {
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
  }, []);

  const loadCalendars = useCallback(async () => {
    try {
      const data = await fetchVendorCalendars();
      setGoogle(googleConnection(data));
      setCalCopy(data.copy || '');
    } catch {
      setGoogle(null);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadStripe();
      loadCalendars();
    }, [loadStripe, loadCalendars]),
  );

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

  const onConnectGoogle = async () => {
    setCalBusy(true);
    setCalErr('');
    try {
      const result = await connectGoogleCalendar();
      await loadCalendars();
      if (result.connected) return;
      if (result.error) {
        setCalErr(result.error);
        Alert.alert('Google Calendar', result.error);
      }
    } catch (e: any) {
      setCalErr(e.message || 'Could not connect Google Calendar.');
      Alert.alert('Google Calendar', e.message || 'Could not connect Google Calendar.');
    } finally {
      setCalBusy(false);
    }
  };

  const onDisconnectGoogle = () => {
    Alert.alert(
      'Disconnect Google Calendar',
      'Auto-assign will fall back to your manual hours and existing Bricks jobs.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: async () => {
            setCalBusy(true);
            setCalErr('');
            try {
              await disconnectVendorCalendar('google');
              await loadCalendars();
            } catch (e: any) {
              setCalErr(e.message || 'Could not disconnect.');
              Alert.alert('Google Calendar', e.message || 'Could not disconnect.');
            } finally {
              setCalBusy(false);
            }
          },
        },
      ],
    );
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

  const googleConnected = !!google?.connected;
  const googleSubtitle = googleConnected
    ? 'Connected. Auto-assign skips slots that are busy on this calendar.'
    : google?.status === 'needs_env'
      ? (google.detail || 'Google Calendar is not configured on this environment.')
      : (google?.detail || 'Connect so auto-assign respects your real busy time.');

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
      <Section first={!stripeVisible} label="Calendar">
        <Group>
          <Row
            icon={Calendar}
            title="Google Calendar"
            subtitle={googleSubtitle}
            right={googleConnected ? <Pill label="Connected" tone="ok" /> : undefined}
          />
          {googleConnected ? (
            <Row title="Disconnect Google Calendar" tone="bad" onPress={onDisconnectGoogle} loading={calBusy} />
          ) : (
            <Row
              title="Connect Google Calendar"
              tone="accent"
              chevron
              onPress={onConnectGoogle}
              loading={calBusy}
              disabled={google?.status === 'needs_env'}
            />
          )}
        </Group>
        {calCopy ? <Text style={[T.caption, s.hint]}>{calCopy}</Text> : null}
        {calErr ? <Text style={[T.caption, s.err]}>{calErr}</Text> : null}
      </Section>
      <Section label="Notifications">
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
  hint: { color: color.textMuted, marginTop: space[8] },
});
