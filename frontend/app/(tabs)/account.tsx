import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Switch, Alert, Linking, Share } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { User, Star, Briefcase, Calendar, Download, LogOut, ChevronRight, Pencil, Settings } from 'lucide-react-native';
import { useRouter } from 'expo-router';
import { actorHeaders } from '../../lib/api';
import { supabase } from '../../lib/supabase';
import { TOKENS, TYPOGRAPHY } from '../../constants/Theme';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? '';

type Profile = {
  name: string; email: string; role: string; phone: string;
  availability: boolean; total_jobs_completed: number; rating: number; member_since: string;
};

function initials(name: string) {
  return name.split(' ').map(p => p[0]).join('').slice(0, 2).toUpperCase();
}

function fmtMemberSince(iso: string) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
}

async function openCalendar(platform: 'google' | 'apple') {
  const url = platform === 'google'
    ? 'https://calendar.google.com'
    : 'calshow://';
  const supported = await Linking.canOpenURL(url);
  if (supported) await Linking.openURL(url);
  else Alert.alert('Calendar unavailable', 'Could not open the calendar app.');
}

export default function AccountScreen() {
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [availability, setAvailability] = useState(true);
  const [togglingAvail, setTogglingAvail] = useState(false);

  const fetchProfile = useCallback(async () => {
    setError(false);
    try {
      const headers = await actorHeaders();
      const res = await fetch(`${API}/api/profile`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: Profile = await res.json();
      setProfile(data);
      setAvailability(data.availability);
    } catch (e) {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchProfile(); }, [fetchProfile]);

  const toggleAvailability = async (val: boolean) => {
    setAvailability(val);
    setTogglingAvail(true);
    try {
      const headers = await actorHeaders({ 'Content-Type': 'application/json' });
      const res = await fetch(`${API}/api/profile`, {
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

  if (error || (!loading && !profile)) {
    return (
      <SafeAreaView style={s.container} edges={['top']}>
        <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
          <Text style={s.screenTitle}>Me</Text>
          <View style={s.section}>
            <Text style={s.sectionLabel}>ACCOUNT</Text>
            <View style={s.menuCard}>
              <TouchableOpacity style={[s.menuRow, s.menuRowBorder]} activeOpacity={0.7} onPress={() => router.push('/profile-edit')}>
                <View style={s.menuIconWrap}><Pencil size={15} color={TOKENS.moonMute} strokeWidth={1.5} /></View>
                <View style={s.menuText}><Text style={s.menuLabel}>Edit profile</Text></View>
                <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
              </TouchableOpacity>
              <TouchableOpacity style={s.menuRow} activeOpacity={0.7} onPress={() => router.push('/settings')}>
                <View style={s.menuIconWrap}><Settings size={15} color={TOKENS.moonMute} strokeWidth={1.5} /></View>
                <View style={s.menuText}><Text style={s.menuLabel}>Settings</Text></View>
                <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
              </TouchableOpacity>
            </View>
          </View>
          <TouchableOpacity onPress={fetchProfile} style={s.retryRow} activeOpacity={0.7}>
            <Text style={s.retryText}>Retry</Text>
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    );
  }

  if (!profile) return null;

  return (
    <SafeAreaView style={s.container} edges={['top']}>
      <ScrollView contentContainerStyle={s.scroll} showsVerticalScrollIndicator={false}>
        <Text style={s.screenTitle}>Me</Text>

        {/* Avatar + identity */}
        <View style={s.identity}>
          <View style={s.avatar}>
            <Text style={s.avatarText}>{initials(profile.name)}</Text>
          </View>
          <View style={s.identityInfo}>
            <Text style={s.name}>{profile.name}</Text>
            <Text style={s.role}>{profile.role}</Text>
            <Text style={s.email}>{profile.email}</Text>
          </View>
        </View>

        {/* Stats rail */}
        <View style={s.rail}>
          <View style={s.railCell}>
            <View style={s.railIcon}>
              <Briefcase size={13} color={TOKENS.moonMute} strokeWidth={1.5} />
            </View>
            <Text style={s.railVal}>{profile.total_jobs_completed}</Text>
            <Text style={s.railLabel}>JOBS</Text>
          </View>
          <View style={[s.railCell, s.railDivider]}>
            <View style={s.railIcon}>
              <Star size={13} color={TOKENS.moonMute} strokeWidth={1.5} />
            </View>
            <Text style={s.railVal}>{profile.rating.toFixed(1)}</Text>
            <Text style={s.railLabel}>RATING</Text>
          </View>
          <View style={[s.railCell, s.railDivider]}>
            <View style={s.railIcon}>
              <User size={13} color={TOKENS.moonMute} strokeWidth={1.5} />
            </View>
            <Text style={s.railVal}>{fmtMemberSince(profile.member_since)}</Text>
            <Text style={s.railLabel}>MEMBER SINCE</Text>
          </View>
        </View>

        {/* Account actions */}
        <View style={s.section}>
          <Text style={s.sectionLabel}>ACCOUNT</Text>
          <View style={s.menuCard}>
            <TouchableOpacity style={[s.menuRow, s.menuRowBorder]} activeOpacity={0.7} onPress={() => router.push('/profile-edit')}>
              <View style={s.menuIconWrap}>
                <Pencil size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </View>
              <View style={s.menuText}>
                <Text style={s.menuLabel}>Edit profile</Text>
                <Text style={s.menuSub}>Update your name, phone, and bio</Text>
              </View>
              <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
            </TouchableOpacity>
            <TouchableOpacity style={s.menuRow} activeOpacity={0.7} onPress={() => router.push('/settings')}>
              <View style={s.menuIconWrap}>
                <Settings size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </View>
              <View style={s.menuText}>
                <Text style={s.menuLabel}>Settings</Text>
                <Text style={s.menuSub}>Notifications, preferences, and more</Text>
              </View>
              <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Availability toggle */}
        <View style={s.section}>
          <Text style={s.sectionLabel}>STATUS</Text>
          <View style={s.toggleRow}>
            <View style={s.toggleLeft}>
              <View style={[s.statusDot, { backgroundColor: availability ? TOKENS.ok : TOKENS.moonFaint }]} />
              <Text style={s.toggleLabel}>{availability ? 'Available for jobs' : 'Not available'}</Text>
            </View>
            <Switch
              value={availability}
              onValueChange={toggleAvailability}
              disabled={togglingAvail}
              trackColor={{ false: TOKENS.nightDeep, true: TOKENS.ok + '66' }}
              thumbColor={availability ? TOKENS.ok : TOKENS.moonFaint}
              ios_backgroundColor={TOKENS.nightDeep}
            />
          </View>
        </View>

        {/* Calendar integrations */}
        <View style={s.section}>
          <Text style={s.sectionLabel}>CALENDAR</Text>
          <View style={s.menuCard}>
            <TouchableOpacity style={[s.menuRow, s.menuRowBorder]} activeOpacity={0.7} onPress={() => openCalendar('google')}>
              <View style={s.menuIconWrap}>
                <Calendar size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </View>
              <View style={s.menuText}>
                <Text style={s.menuLabel}>Google Calendar</Text>
                <Text style={s.menuSub}>Sync your schedule</Text>
              </View>
              <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
            </TouchableOpacity>
            <TouchableOpacity style={s.menuRow} activeOpacity={0.7} onPress={() => openCalendar('apple')}>
              <View style={s.menuIconWrap}>
                <Calendar size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </View>
              <View style={s.menuText}>
                <Text style={s.menuLabel}>Apple Calendar</Text>
                <Text style={s.menuSub}>Sync your schedule</Text>
              </View>
              <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Earnings export */}
        <View style={s.section}>
          <Text style={s.sectionLabel}>EARNINGS</Text>
          <TouchableOpacity
            style={s.menuCard}
            activeOpacity={0.7}
            onPress={async () => {
              await Share.share({ message: `Bricks earnings export for ${profile.name}` });
            }}
          >
            <View style={s.menuRow}>
              <View style={s.menuIconWrap}>
                <Download size={15} color={TOKENS.moonMute} strokeWidth={1.5} />
              </View>
              <View style={s.menuText}>
                <Text style={s.menuLabel}>Export earnings</Text>
                <Text style={s.menuSub}>Full payout history as CSV</Text>
              </View>
              <ChevronRight size={14} color={TOKENS.moonFaint} strokeWidth={1.5} />
            </View>
          </TouchableOpacity>
        </View>

        {/* Sign out */}
        <View style={s.section}>
          <TouchableOpacity
            style={s.signOutRow}
            activeOpacity={0.7}
            onPress={() =>
              Alert.alert('Sign out', 'Are you sure?', [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Sign out', style: 'destructive', onPress: () => supabase.auth.signOut() },
              ])
            }
          >
            <LogOut size={15} color={TOKENS.bad} strokeWidth={1.5} />
            <Text style={s.signOutLabel}>Sign out</Text>
          </TouchableOpacity>
        </View>

        <View style={{ height: 120 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  scroll: { paddingHorizontal: 24, paddingTop: 16 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  screenTitle: { fontSize: 26, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, letterSpacing: -0.4, marginBottom: 28 },

  identity: { flexDirection: 'row', alignItems: 'center', gap: 16, marginBottom: 28 },
  avatar: { width: 60, height: 60, borderRadius: 30, backgroundColor: TOKENS.nightDeep, borderWidth: 1, borderColor: TOKENS.nightHairS, alignItems: 'center', justifyContent: 'center' },
  avatarText: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 18, color: TOKENS.moon, letterSpacing: 1 },
  identityInfo: { flex: 1 },
  name: { fontSize: 18, color: TOKENS.moon, fontFamily: TYPOGRAPHY.heading, marginBottom: 3 },
  role: { fontSize: 12, color: TOKENS.brick, fontFamily: TYPOGRAPHY.monoMedium, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 3 },
  email: { fontFamily: TYPOGRAPHY.body, fontSize: 12, color: TOKENS.moonFaint },

  rail: { flexDirection: 'row', backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, marginBottom: 28 },
  railCell: { flex: 1, paddingVertical: 16, paddingHorizontal: 12, alignItems: 'flex-start' },
  railDivider: { borderLeftWidth: 1, borderLeftColor: TOKENS.nightHair },
  railIcon: { marginBottom: 8 },
  railVal: { fontSize: 15, color: TOKENS.moon, fontFamily: TYPOGRAPHY.mono, marginBottom: 4 },
  railLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 8, color: TOKENS.moonFaint, letterSpacing: 1.4 },

  section: { marginBottom: 24 },
  sectionLabel: { fontFamily: TYPOGRAPHY.monoMedium, fontSize: 9, color: TOKENS.moonFaint, letterSpacing: 1.8, marginBottom: 12 },

  toggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 14 },
  toggleLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  statusDot: { width: 7, height: 7, borderRadius: 3.5 },
  toggleLabel: { fontFamily: TYPOGRAPHY.body, fontSize: 14, color: TOKENS.moon },

  menuCard: { backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2 },
  menuRow: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 14, gap: 12 },
  menuRowBorder: { borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  menuIconWrap: { width: 28, alignItems: 'center' },
  menuText: { flex: 1 },
  menuLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 14, color: TOKENS.moon, marginBottom: 2 },
  menuSub: { fontFamily: TYPOGRAPHY.body, fontSize: 11, color: TOKENS.moonFaint },

  signOutRow: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: TOKENS.nightSoft, borderWidth: 1, borderColor: TOKENS.nightHair, borderRadius: 2, paddingHorizontal: 16, paddingVertical: 14 },
  signOutLabel: { fontFamily: TYPOGRAPHY.heading, fontSize: 14, color: TOKENS.bad },
  retryRow: { alignItems: 'center', paddingVertical: 16 },
  retryText: { fontFamily: TYPOGRAPHY.body, fontSize: 13, color: TOKENS.brick },
});
