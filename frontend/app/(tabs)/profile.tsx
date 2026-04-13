import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Switch, RefreshControl } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { User, Mail, Phone, Star, Award, Calendar, HelpCircle, LogOut, Wifi, WifiOff } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;
type Profile = { id: string; name: string; email: string; role: string; phone: string; availability: boolean; total_jobs_completed: number; rating: number; member_since: string };

export default function ProfileScreen() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);
  const fetchProfile = useCallback(async () => {
    try { setProfile(await (await fetch(`${API}/api/profile`)).json()); }
    catch (e) { console.error(e); } finally { setLoading(false); setRefreshing(false); }
  }, []);
  useEffect(() => { fetchProfile(); }, [fetchProfile]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchProfile(); }, [fetchProfile]);
  const toggle = async () => {
    if (!profile || toggling) return; setToggling(true);
    const nv = !profile.availability; setProfile({ ...profile, availability: nv });
    try { await fetch(`${API}/api/profile`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ availability: nv }) }); }
    catch { setProfile({ ...profile, availability: !nv }); } finally { setToggling(false); }
  };

  if (loading || !profile) return <SafeAreaView style={s.ctn}><View style={s.ldg}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;

  return (
    <SafeAreaView style={s.ctn} edges={['top']}>
      <ScrollView contentContainerStyle={s.scroll} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}>
        <View style={s.hdr}><Text style={s.hdrT}>Profile</Text></View>
        <View style={s.avSec}>
          <View style={s.avW}><View style={s.av}><User size={30} color="#8B8FA3" strokeWidth={1.5} /></View><View style={[s.avDot, profile.availability ? s.dotOn : s.dotOff]} /></View>
          <Text style={s.name}>{profile.name}</Text>
          <Text style={s.role}>{profile.role}</Text>
        </View>
        <View testID="availability-section" style={s.avCard}>
          <View style={s.avInfo}>
            {profile.availability ? <Wifi size={18} color="#22C55E" strokeWidth={2} /> : <WifiOff size={18} color="#EF4444" strokeWidth={2} />}
            <View><Text style={s.avLabel}>Availability</Text><Text style={[s.avStatus, { color: profile.availability ? '#22C55E' : '#EF4444' }]}>{profile.availability ? 'Online' : 'Offline'}</Text></View>
          </View>
          <Switch testID="availability-toggle" value={profile.availability} onValueChange={toggle} trackColor={{ false: '#252839', true: '#3B82F640' }} thumbColor={profile.availability ? '#3B82F6' : '#565B7E'} />
        </View>
        <View style={s.stats}>
          <View style={s.stat}><Award size={18} color="#3B82F6" strokeWidth={2} /><Text style={s.statV}>{profile.total_jobs_completed}</Text><Text style={s.statL}>Jobs Done</Text></View>
          <View style={s.stat}><Star size={18} color="#F59E0B" strokeWidth={2} /><Text style={s.statV}>{profile.rating}</Text><Text style={s.statL}>Rating</Text></View>
          <View style={s.stat}><Calendar size={18} color="#8B5CF6" strokeWidth={2} /><Text style={s.statV}>{new Date(profile.member_since).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}</Text><Text style={s.statL}>Since</Text></View>
        </View>
        <View style={s.info}>
          <View style={s.infoI}><Mail size={16} color="#565B7E" strokeWidth={2} /><View style={s.infoTx}><Text style={s.infoL}>Email</Text><Text style={s.infoV}>{profile.email}</Text></View></View>
          <View style={[s.infoI, { borderBottomWidth: 0 }]}><Phone size={16} color="#565B7E" strokeWidth={2} /><View style={s.infoTx}><Text style={s.infoL}>Phone</Text><Text style={s.infoV}>{profile.phone}</Text></View></View>
        </View>
        <View style={s.acts}>
          <TouchableOpacity testID="support-btn" style={s.actBtn}><HelpCircle size={16} color="#8B8FA3" strokeWidth={2} /><Text style={s.actTx}>Support</Text></TouchableOpacity>
          <TouchableOpacity testID="logout-btn" style={[s.actBtn, s.logBtn]}><LogOut size={16} color="#EF4444" strokeWidth={2} /><Text style={[s.actTx, s.logTx]}>Sign Out</Text></TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  ctn: { flex: 1, backgroundColor: '#0C0D14' },
  ldg: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  scroll: { paddingBottom: 100 },
  hdr: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  hdrT: { fontSize: 26, fontFamily: 'Outfit_600SemiBold', color: '#FFF', letterSpacing: -0.5 },
  avSec: { alignItems: 'center', paddingVertical: 16 },
  avW: { position: 'relative', marginBottom: 14 },
  av: { width: 76, height: 76, borderRadius: 38, backgroundColor: '#151722', borderWidth: 2, borderColor: '#1E2030', alignItems: 'center', justifyContent: 'center' },
  avDot: { position: 'absolute', bottom: 2, right: 2, width: 18, height: 18, borderRadius: 9, borderWidth: 3, borderColor: '#0C0D14' },
  dotOn: { backgroundColor: '#22C55E' },
  dotOff: { backgroundColor: '#EF4444' },
  name: { fontSize: 20, fontFamily: 'Outfit_700Bold', color: '#FFF', marginBottom: 4 },
  role: { fontSize: 13, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  avCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', marginHorizontal: 16, padding: 16, marginBottom: 16 },
  avInfo: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avLabel: { fontSize: 12, fontFamily: 'Manrope_500Medium', color: '#565B7E', marginBottom: 2 },
  avStatus: { fontSize: 14, fontFamily: 'Outfit_600SemiBold' },
  stats: { flexDirection: 'row', paddingHorizontal: 16, gap: 8, marginBottom: 16 },
  stat: { flex: 1, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 16, alignItems: 'center', gap: 6 },
  statV: { fontSize: 18, fontFamily: 'Outfit_300Light', color: '#FFF' },
  statL: { fontSize: 11, fontFamily: 'Manrope_500Medium', color: '#565B7E' },
  info: { marginHorizontal: 16, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', marginBottom: 16 },
  infoI: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#1E2030', gap: 12 },
  infoTx: { flex: 1 },
  infoL: { fontSize: 11, fontFamily: 'Manrope_400Regular', color: '#565B7E', marginBottom: 2 },
  infoV: { fontSize: 14, fontFamily: 'Manrope_500Medium', color: '#FFF' },
  acts: { marginHorizontal: 16, gap: 8 },
  actBtn: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 16 },
  actTx: { fontSize: 14, fontFamily: 'Manrope_500Medium', color: '#8B8FA3' },
  logBtn: { borderColor: '#EF444430' },
  logTx: { color: '#EF4444' },
});
