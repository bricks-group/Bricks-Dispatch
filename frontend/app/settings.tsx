import { useEffect, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Switch, ActivityIndicator, Alert, Linking } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { ArrowLeft, Bell, Vibrate, CreditCard } from 'lucide-react-native';
import { useTheme } from '../context/ThemeContext';
import { actorHeaders } from '../lib/api';
import { TOKENS, TYPOGRAPHY } from '../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

export default function SettingsScreen() {
  const router = useRouter();
  const { theme } = useTheme();
  const c = theme;
  const [jobAlerts, setJobAlerts] = useState(true);
  const [chatNotifs, setChatNotifs] = useState(true);
  const [payoutNotifs, setPayoutNotifs] = useState(true);
  const [vibrate, setVibrate] = useState(true);
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

  return (
    <SafeAreaView style={[s.container, { backgroundColor: c.bg }]} edges={['top']}>
      <View style={[s.header, { borderBottomColor: c.border }]}>
        <TouchableOpacity onPress={() => router.back()} activeOpacity={0.7} style={s.back}>
          <ArrowLeft size={18} color={c.text} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={[s.title, { color: c.text }]}>Settings</Text>
        <View style={{ width: 36 }} />
      </View>

      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
        {stripeVisible ? (
          <View style={s.section}>
            <Text style={[s.sectionLabel, { color: c.textFaint }]}>PAYOUTS</Text>
            <View style={[s.menuCard, { backgroundColor: c.surface, borderColor: c.border }]}>
              <View style={[s.row, { borderBottomWidth: 1, borderBottomColor: c.border }]}>
                <CreditCard size={15} color={c.textMuted} strokeWidth={1.5} />
                <View style={{ flex: 1 }}>
                  <Text style={[s.rowLabel, { color: c.text }]}>Stripe Connect</Text>
                  <Text style={s.rowSub}>
                    {stripe == null
                      ? (stripeErr || 'Status unavailable')
                      : stripe.connected
                        ? 'Connected — job payouts go to this account.'
                        : 'Not connected. Owner payouts are the full package with $0 deduction.'}
                  </Text>
                </View>
              </View>
              <TouchableOpacity
                style={s.row}
                onPress={startStripe}
                disabled={stripeBusy}
                activeOpacity={0.7}
              >
                {stripeBusy
                  ? <ActivityIndicator size="small" color={TOKENS.brick} />
                  : <Text style={[s.rowLabel, { color: TOKENS.brick }]}>
                      {stripe?.connected ? 'Update Stripe account' : 'Connect Stripe'}
                    </Text>}
              </TouchableOpacity>
            </View>
            {stripeErr ? <Text style={{ color: TOKENS.bad, fontSize: 12, marginTop: 8 }}>{stripeErr}</Text> : null}
          </View>
        ) : null}
        <View style={s.section}>
          <Text style={[s.sectionLabel, { color: c.textFaint }]}>NOTIFICATIONS</Text>
          <View style={[s.menuCard, { backgroundColor: c.surface, borderColor: c.border }]}>
            {[
              { label: 'Job alerts', value: jobAlerts, set: setJobAlerts, Icon: Bell },
              { label: 'Messages', value: chatNotifs, set: setChatNotifs, Icon: Bell },
              { label: 'Payout updates', value: payoutNotifs, set: setPayoutNotifs, Icon: Bell },
            ].map(({ label, value, set, Icon }, i, arr) => (
              <View key={label} style={[s.row, i < arr.length - 1 && { borderBottomWidth: 1, borderBottomColor: c.border }]}>
                <Icon size={15} color={c.textMuted} strokeWidth={1.5} />
                <Text style={[s.rowLabel, { color: c.text }]}>{label}</Text>
                <Switch
                  value={value}
                  onValueChange={set}
                  trackColor={{ false: c.surfaceDeep, true: '#4A6B3C66' }}
                  thumbColor={value ? '#4A6B3C' : c.textFaint}
                  ios_backgroundColor={c.surfaceDeep}
                />
              </View>
            ))}
            <View style={s.row}>
              <Vibrate size={15} color={c.textMuted} strokeWidth={1.5} />
              <Text style={[s.rowLabel, { color: c.text }]}>Vibration</Text>
              <Switch
                value={vibrate}
                onValueChange={setVibrate}
                trackColor={{ false: c.surfaceDeep, true: '#4A6B3C66' }}
                thumbColor={vibrate ? '#4A6B3C' : c.textFaint}
                ios_backgroundColor={c.surfaceDeep}
              />
            </View>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  header: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  back: { width: 36, height: 36, justifyContent: 'center' },
  title: { flex: 1, fontSize: 16, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, textAlign: 'center' },
  scroll: { padding: 24 },
  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 12 },
  menuCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 14, gap: 12 },
  rowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  rowLabel: { flex: 1, fontSize: 14, color: TOKENS.moon },
  rowSub: { fontSize: 11, color: TOKENS.moonFaint, marginTop: 2 },
});
