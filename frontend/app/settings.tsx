import { useCallback, useState } from 'react';
import { View, Text, StyleSheet, Alert, Linking } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { Bell, Calendar, CreditCard } from 'lucide-react-native';
import { actorHeaders } from '../lib/api';
import {
  CALENDAR_EDU_LINES,
  CALENDAR_INTRO_TITLE,
  CALENDAR_PROVIDER_HINTS,
  calendarRowSubtitle,
  connectAppleCalendar,
  connectGoogleCalendar,
  connectIcsCalendar,
  connectMicrosoftCalendar,
  connectionFor,
  disconnectVendorCalendar,
  fetchVendorCalendars,
  PROVIDER_LABELS,
  type CalendarConnection,
  type CalendarProvider,
} from '../lib/calendarConnect';
import { color, type as T, space } from '../theme';
import { Screen, NavBar, Section, Group, Row, Pill, Field } from '../components/ui';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function SettingsScreen() {
  const router = useRouter();
  const [stripe, setStripe] = useState<{ connected: boolean; connect_status: string } | null>(null);
  const [stripeVisible, setStripeVisible] = useState(false);
  const [stripeBusy, setStripeBusy] = useState(false);
  const [stripeErr, setStripeErr] = useState('');
  const [connections, setConnections] = useState<CalendarConnection[]>([]);
  const [calBusy, setCalBusy] = useState('');
  const [calErr, setCalErr] = useState('');
  const [appleUrl, setAppleUrl] = useState('');
  const [applePass, setApplePass] = useState('');
  const [appleUser, setAppleUser] = useState('');
  const [icsUrl, setIcsUrl] = useState('');

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
      setConnections(data.connections || []);
    } catch {
      setConnections([]);
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

  const row = (provider: CalendarProvider) =>
    connections.find((c) => c.provider === provider) || connectionFor({ connections }, provider);

  const runConnect = async (provider: CalendarProvider, fn: () => Promise<{ connected: boolean; error?: string }>) => {
    setCalBusy(provider);
    setCalErr('');
    try {
      const result = await fn();
      await loadCalendars();
      if (result.connected) return;
      if (result.error) {
        setCalErr(result.error);
        Alert.alert(PROVIDER_LABELS[provider] || 'Calendar', result.error);
      }
    } catch (e: any) {
      const msg = e.message || 'Could not connect.';
      setCalErr(msg);
      Alert.alert(PROVIDER_LABELS[provider] || 'Calendar', msg);
    } finally {
      setCalBusy('');
    }
  };

  const onDisconnect = (provider: CalendarProvider) => {
    Alert.alert(
      `Disconnect ${PROVIDER_LABELS[provider]}`,
      'Auto-assign will skip this calendar. Manual hours and other connected calendars still apply.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Disconnect',
          style: 'destructive',
          onPress: async () => {
            setCalBusy(provider);
            setCalErr('');
            try {
              await disconnectVendorCalendar(provider);
              await loadCalendars();
            } catch (e: any) {
              setCalErr(e.message || 'Could not disconnect.');
              Alert.alert('Calendar', e.message || 'Could not disconnect.');
            } finally {
              setCalBusy('');
            }
          },
        },
      ],
    );
  };

  const oauthBlock = (provider: 'google' | 'microsoft') => {
    const c = row(provider);
    const connected = !!c?.connected;
    const title = PROVIDER_LABELS[provider];
    const subtitle = calendarRowSubtitle(c, title);
    return (
      <>
        <Row
          icon={Calendar}
          title={title}
          subtitle={subtitle}
          right={connected ? <Pill label="Connected" tone="ok" /> : undefined}
        />
        {connected ? (
          <Row
            title={`Disconnect ${title}`}
            tone="bad"
            onPress={() => onDisconnect(provider)}
            loading={calBusy === provider}
          />
        ) : (
          <Row
            title="Connect"
            tone="accent"
            chevron
            onPress={() =>
              runConnect(provider, provider === 'google' ? connectGoogleCalendar : connectMicrosoftCalendar)
            }
            loading={calBusy === provider}
            disabled={c?.status === 'needs_env'}
          />
        )}
      </>
    );
  };

  const apple = row('apple');
  const ics = row('ics');

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
      <Section first={!stripeVisible} label="Calendars">
        <View style={s.edu} testID="calendar-edu">
          <Text style={[T.bodySm, s.introTitle]}>{CALENDAR_INTRO_TITLE}</Text>
          <Text style={T.bodySm}>{CALENDAR_EDU_LINES.join(' ')}</Text>
        </View>
        <Group style={s.square}>
          {oauthBlock('google')}
          {oauthBlock('microsoft')}
        </Group>
        <Group style={[s.gap, s.square]}>
          <Row
            icon={Calendar}
            title="Apple Calendar"
            subtitle={
              apple?.connected
                ? calendarRowSubtitle(apple, 'Apple Calendar')
                : CALENDAR_PROVIDER_HINTS.apple
            }
            right={apple?.connected ? <Pill label="Connected" tone="ok" /> : undefined}
          />
          {apple?.connected ? (
            <Row title="Disconnect Apple Calendar" tone="bad" onPress={() => onDisconnect('apple')} loading={calBusy === 'apple'} />
          ) : (
            <>
              <View style={s.fields}>
                <Field boxStyle={s.square} label="iCloud calendar address" value={appleUrl} onChangeText={setAppleUrl} autoCapitalize="none" autoCorrect={false} placeholder="Paste your calendar address" />
                <Field boxStyle={s.square} label="Apple ID email" value={appleUser} onChangeText={setAppleUser} autoCapitalize="none" keyboardType="email-address" placeholder="name@icloud.com" />
                <Field boxStyle={s.square} label="App-specific password" value={applePass} onChangeText={setApplePass} secureTextEntry autoCapitalize="none" placeholder="Your app-specific password" />
              </View>
              <Row
                title="Connect"
                tone="accent"
                onPress={() =>
                  runConnect('apple', () =>
                    connectAppleCalendar({ caldav_url: appleUrl, app_password: applePass, username: appleUser }),
                  )
                }
                loading={calBusy === 'apple'}
                disabled={!appleUrl.trim() || !applePass.trim()}
              />
            </>
          )}
        </Group>
        <Group style={[s.gap, s.square]}>
          <Row
            icon={Calendar}
            title="Calendar link"
            subtitle={
              ics?.connected
                ? calendarRowSubtitle(ics, 'Calendar link')
                : CALENDAR_PROVIDER_HINTS.ics
            }
            right={ics?.connected ? <Pill label="Connected" tone="ok" /> : undefined}
          />
          {ics?.connected ? (
            <Row title="Disconnect" tone="bad" onPress={() => onDisconnect('ics')} loading={calBusy === 'ics'} />
          ) : (
            <>
              <View style={s.fields}>
                <Field boxStyle={s.square} label="Calendar link" value={icsUrl} onChangeText={setIcsUrl} autoCapitalize="none" autoCorrect={false} placeholder="Paste your calendar link" />
              </View>
              <Row
                title="Connect"
                tone="accent"
                onPress={() => runConnect('ics', () => connectIcsCalendar(icsUrl))}
                loading={calBusy === 'ics'}
                disabled={!icsUrl.trim()}
              />
            </>
          )}
        </Group>
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
  gap: { marginTop: space[16] },
  edu: { gap: space[8], marginBottom: space[16], padding: space[16], borderWidth: 1, borderColor: color.hair, borderRadius: 0 },
  introTitle: { color: color.textBody, fontWeight: '600' },
  square: { borderRadius: 0 },
  fields: { gap: space[12], paddingHorizontal: space[16], paddingTop: space[8] },
});
