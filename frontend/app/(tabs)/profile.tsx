import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ChevronRight, FileText } from 'lucide-react-native';
import { useRouter } from 'expo-router';
import { actorHeaders } from '../../lib/api';

const API = process.env.EXPO_PUBLIC_API_URL || 'http://10.0.0.206:8000';

// Bricks Design Tokens - Dark Mode (Night)
const TOKENS = {
  night: '#141210',
  nightSoft: '#1C1A17',
  nightHair: '#F4F1EB14',
  nightHairS: '#F4F1EB26',
  moon: '#F4F1EB',
  moonSoft: '#D6CFC2',
  moonMute: '#8F8678',
  brick: '#D2542B',
  ok: '#4A6B3C',
  bad: '#8C2F24'
};

export default function ProfileScreen() {
  const router = useRouter();
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchProfile = useCallback(async () => {
    try { 
      const resp = await fetch(`${API}/api/profile`, { headers: actorHeaders() });
      const data = await resp.json();
      setProfile(data); 
    }
    catch (e) { console.error(e); } 
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { fetchProfile(); }, [fetchProfile]);

  if (loading || !profile) return (
    <SafeAreaView style={s.ctn}>
      <ActivityIndicator size="small" color={TOKENS.brick} />
    </SafeAreaView>
  );

  return (
    <SafeAreaView style={s.ctn} edges={['top']}>
      <ScrollView 
        contentContainerStyle={s.scroll} 
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={fetchProfile} tintColor={TOKENS.brick} />}
      >
        <View style={s.hdr}>
          <Text style={s.hdrT}>profile</Text>
        </View>

        <View style={s.userCard}>
          <View style={s.avatar} />
          <View style={s.userInfo}>
            <Text style={s.userName}>{profile.name.toLowerCase()}</Text>
            <Text style={s.userRole}>{profile.role.toUpperCase()}</Text>
          </View>
          <View style={[s.statusDot, { backgroundColor: profile.availability ? TOKENS.ok : TOKENS.bad }]} />
        </View>

        {/* ONBOARDING CTA */}
        <TouchableOpacity 
          style={s.onboardingBtn} 
          onPress={() => router.push('/onboarding/slides')}
          activeOpacity={0.8}
          data-testid="join-crew-btn"
        >
          <View>
            <Text style={s.overline}>join the crew</Text>
            <Text style={s.ctaTitle}>Apply for Ontario jobs</Text>
          </View>
          <ChevronRight size={18} color={TOKENS.brick} />
        </TouchableOpacity>

        <View style={s.gridContainer}>
           <View style={s.infoRow}>
              <Text style={s.label}>email</Text>
              <Text style={s.value}>{profile.email}</Text>
           </View>
           <View style={s.infoRow}>
              <Text style={s.label}>phone</Text>
              <Text style={s.value}>{profile.phone}</Text>
           </View>
           <View style={[s.infoRow, { borderBottomWidth: 0 }]}>
              <Text style={s.label}>region</Text>
              <Text style={s.value}>ONTARIO (GTA)</Text>
           </View>
        </View>

      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: TOKENS.night },
  scroll: { paddingBottom: 100 },
  hdr: { paddingHorizontal: 36, paddingTop: 24, marginBottom: 32 },
  hdrT: { 
    fontSize: 34, 
    fontFamily: 'System', 
    color: TOKENS.moon, 
    letterSpacing: -1.1,
    textTransform: 'lowercase'
  },
  userCard: { 
    marginHorizontal: 36, 
    flexDirection: 'row', 
    alignItems: 'center', 
    paddingBottom: 24, 
    borderBottomWidth: 1, 
    borderBottomColor: TOKENS.nightHair 
  },
  avatar: { width: 48, height: 48, backgroundColor: TOKENS.nightSoft, borderRadius: 2 },
  userInfo: { flex: 1, marginLeft: 16 },
  userName: { fontSize: 20, fontFamily: 'System', color: TOKENS.moon, letterSpacing: -0.5 },
  userRole: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonMute, letterSpacing: 1.5 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  
  onboardingBtn: {
    marginHorizontal: 36,
    marginVertical: 24,
    backgroundColor: TOKENS.nightSoft,
    padding: 20,
    borderRadius: 2,
    borderWidth: 1,
    borderColor: TOKENS.nightHairS,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between'
  },
  ctaTitle: { fontSize: 15, fontFamily: 'System', color: TOKENS.moon, marginTop: 4 },
  
  gridContainer: { marginHorizontal: 36, backgroundColor: TOKENS.nightSoft, borderRadius: 2, borderWidth: 1, borderColor: TOKENS.nightHair },
  infoRow: { padding: 16, borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair },
  label: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.moonMute, textTransform: 'uppercase', letterSpacing: 1.4 },
  value: { fontSize: 13, fontFamily: 'JetBrainsMono-Regular', color: TOKENS.moon, marginTop: 4 },
  overline: { fontSize: 10, fontFamily: 'JetBrainsMono-Medium', color: TOKENS.brick, textTransform: 'uppercase', letterSpacing: 1.5 }
});
