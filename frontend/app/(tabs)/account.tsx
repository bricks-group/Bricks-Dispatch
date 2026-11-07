import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, Alert, Image } from 'react-native';
import { Calendar, Download, LogOut, Pencil, Settings } from 'lucide-react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { actorHeaders } from '../../lib/api';
import { supabase } from '../../lib/supabase';
import { VendorProfile, mediaUrl } from '../../lib/vendor';
import { fetchVendorCalendars, googleConnection } from '../../lib/googleCalendarConnect';
import { color, type as T, space, radius } from '../../theme';
import { Screen, PageHeader, Section, Group, Row, Button, Notice, StatGrid, Avatar, LoadingState, Toggle, Pill } from '../../components/ui';
import DeleteAccountRow from '../../components/DeleteAccountRow';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

type Profile = VendorProfile;

function fmtMemberSince(iso?: string) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
}

export default function AccountScreen() {
  const router = useRouter();
  const [profile, setProfile] = useState<VendorProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [availability, setAvailability] = useState(true);
  const [togglingAvail, setTogglingAvail] = useState(false);
  const [googleConnected, setGoogleConnected] = useState(false);
  const [googleDetail, setGoogleDetail] = useState('');

  const fetchProfile = useCallback(async () => {
    setError(false);
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/vendor-os/profile`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: VendorProfile = await res.json();
      setProfile(data);
      setAvailability(data.availability);
    } catch (e) {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCalendars = useCallback(async () => {
    try {
      const data = await fetchVendorCalendars();
      const g = googleConnection(data);
      setGoogleConnected(!!g?.connected);
      setGoogleDetail(g?.connected ? 'Connected — auto-assign skips busy slots' : (g?.detail || 'Connect in Settings'));
    } catch {
      setGoogleConnected(false);
      setGoogleDetail('Connect in Settings');
    }
  }, []);

  useEffect(() => { fetchProfile(); }, [fetchProfile]);
  useFocusEffect(useCallback(() => { fetchCalendars(); }, [fetchCalendars]));

  const toggleAvailability = async (val: boolean) => {
    setAvailability(val);
    setTogglingAvail(true);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/vendor-os/profile`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ availability: val }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      setAvailability(!val);
    } finally {
      setTogglingAvail(false);
    }
  };

  const signOut = () =>
    Alert.alert('Sign out', 'Are you sure?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => supabase.auth.signOut() },
    ]);

  const accountRows = (
    <Section label="Account">
      <Group>
        <Row icon={Pencil} title="Edit profile" subtitle={profile ? 'Title, skills, RPAS, service area' : undefined} chevron onPress={() => router.push('/profile-edit')} />
        <Row icon={Settings} title="Settings" subtitle={profile ? 'Notifications and preferences' : undefined} chevron onPress={() => router.push('/settings')} />
      </Group>
    </Section>
  );

  const dangerRows = (
    <Section>
      <Group>
        <Row icon={LogOut} title="Sign out" tone="bad" onPress={signOut} />
        <DeleteAccountRow />
      </Group>
    </Section>
  );

  if (loading && !profile) {
    return (
      <Screen>
        <PageHeader title="Me" />
        <LoadingState />
      </Screen>
    );
  }

  if (error || !profile) {
    return (
      <Screen>
        <PageHeader title="Me" />
        <Notice tone="bad" title="Couldn’t load your profile">Check your connection and try again.</Notice>
        <View style={s.retry}><Button variant="secondary" title="Retry" onPress={fetchProfile} /></View>
        {accountRows}
        {dangerRows}
      </Screen>
    );
  }

  const avatar = mediaUrl(profile.avatar_url);

  return (
    <Screen>
      <PageHeader title="Me" />

      <View style={s.identity}>
        {avatar ? <Image source={{ uri: avatar }} style={s.avatarImg} /> : <Avatar name={profile.name} size={56} />}
        <View style={s.flex}>
          <Text style={[T.headline, { color: color.text }]}>{profile.name}</Text>
          <Text style={[T.bodySm, { color: color.textBody }]}>{[profile.title || profile.role, profile.payout_label].filter(Boolean).join(' · ')}</Text>
          <Text style={[T.bodySm, { color: color.textMuted }]}>{profile.email}</Text>
        </View>
      </View>

      <StatGrid items={[
        { label: 'Jobs', value: String(profile.total_jobs_completed || 0) },
        { label: 'Rating', value: Number(profile.rating || 0).toFixed(1) },
        { label: 'Member since', value: fmtMemberSince(profile.member_since) },
      ]} />

      <Section label="Status">
        <Group>
          <Row
            title={availability ? 'Available for jobs' : 'Not available'}
            subtitle={availability ? 'Auto-dispatch can offer you slots' : 'You won’t get new offers'}
            right={
              <Toggle value={availability} onValueChange={toggleAvailability} disabled={togglingAvail}
        accessibilityLabel="Available for jobs" />
            }
          />
        </Group>
      </Section>

      {accountRows}

      <Section label="Calendar">
        <Group>
          <Row
            icon={Calendar}
            title="Google Calendar"
            subtitle={googleDetail}
            chevron
            right={googleConnected ? <Pill label="Connected" tone="ok" /> : undefined}
            onPress={() => router.push('/settings')}
          />
        </Group>
      </Section>

      <Section label="Earnings">
        <Group>
          <Row icon={Download} title="Payouts" subtitle="Weekly totals and a shareable summary" chevron onPress={() => router.push('/(tabs)/payouts' as any)} />
        </Group>
      </Section>

      {dangerRows}
    </Screen>
  );
}

const s = StyleSheet.create({
  flex: { flex: 1, gap: 2 },
  identity: { flexDirection: 'row', alignItems: 'center', gap: space[16], marginBottom: space[24] },
  avatarImg: { width: 56, height: 56, borderRadius: radius.sm },
  retry: { marginTop: space[12] },
});
