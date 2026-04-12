import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, ActivityIndicator, Switch, RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { User, Mail, Phone, Star, Award, Calendar, HelpCircle, LogOut, Wifi, WifiOff } from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type Profile = {
  id: string; name: string; email: string; role: string; phone: string;
  availability: boolean; total_jobs_completed: number; rating: number; member_since: string;
};

export default function ProfileScreen() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);

  const fetchProfile = useCallback(async () => {
    try { const res = await fetch(`${API}/api/profile`); setProfile(await res.json()); }
    catch (e) { console.error('Failed to fetch profile:', e); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);

  useEffect(() => { fetchProfile(); }, [fetchProfile]);
  const onRefresh = useCallback(() => { setRefreshing(true); fetchProfile(); }, [fetchProfile]);

  const toggleAvailability = async () => {
    if (!profile || toggling) return;
    setToggling(true);
    const newVal = !profile.availability;
    setProfile({ ...profile, availability: newVal });
    try {
      await fetch(`${API}/api/profile`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ availability: newVal }) });
    } catch (e) { setProfile({ ...profile, availability: !newVal }); }
    finally { setToggling(false); }
  };

  if (loading || !profile) {
    return <SafeAreaView style={styles.container}><View style={styles.loadingWrap}><ActivityIndicator size="large" color="#3B82F6" /></View></SafeAreaView>;
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scrollContent}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#3B82F6" colors={['#3B82F6']} />}>
        <View style={styles.header}><Text style={styles.headerTitle}>Profile</Text></View>

        {/* Avatar */}
        <View style={styles.avatarSection}>
          <View style={styles.avatarWrap}>
            <View style={styles.avatar}><User size={30} color="#8B8FA3" strokeWidth={1.5} /></View>
            <View style={[styles.availDot, profile.availability ? styles.dotOnline : styles.dotOffline]} />
          </View>
          <Text style={styles.name}>{profile.name}</Text>
          <Text style={styles.role}>{profile.role}</Text>
        </View>

        {/* Availability */}
        <View testID="availability-section" style={styles.availCard}>
          <View style={styles.availInfo}>
            {profile.availability ? <Wifi size={18} color="#22C55E" strokeWidth={2} /> : <WifiOff size={18} color="#EF4444" strokeWidth={2} />}
            <View>
              <Text style={styles.availLabel}>Availability</Text>
              <Text style={[styles.availStatus, { color: profile.availability ? '#22C55E' : '#EF4444' }]}>
                {profile.availability ? 'Online' : 'Offline'}
              </Text>
            </View>
          </View>
          <Switch testID="availability-toggle" value={profile.availability} onValueChange={toggleAvailability}
            trackColor={{ false: '#252839', true: '#3B82F640' }} thumbColor={profile.availability ? '#3B82F6' : '#565B7E'} />
        </View>

        {/* Stats */}
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Award size={18} color="#3B82F6" strokeWidth={2} />
            <Text style={styles.statValue}>{profile.total_jobs_completed}</Text>
            <Text style={styles.statLabel}>Jobs Done</Text>
          </View>
          <View style={styles.statCard}>
            <Star size={18} color="#F59E0B" strokeWidth={2} />
            <Text style={styles.statValue}>{profile.rating}</Text>
            <Text style={styles.statLabel}>Rating</Text>
          </View>
          <View style={styles.statCard}>
            <Calendar size={18} color="#8B5CF6" strokeWidth={2} />
            <Text style={styles.statValue}>{new Date(profile.member_since).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}</Text>
            <Text style={styles.statLabel}>Since</Text>
          </View>
        </View>

        {/* Info */}
        <View style={styles.infoSection}>
          <View style={styles.infoItem}>
            <Mail size={16} color="#565B7E" strokeWidth={2} />
            <View style={styles.infoText}>
              <Text style={styles.infoLabel}>Email</Text>
              <Text style={styles.infoValue}>{profile.email}</Text>
            </View>
          </View>
          <View style={[styles.infoItem, { borderBottomWidth: 0 }]}>
            <Phone size={16} color="#565B7E" strokeWidth={2} />
            <View style={styles.infoText}>
              <Text style={styles.infoLabel}>Phone</Text>
              <Text style={styles.infoValue}>{profile.phone}</Text>
            </View>
          </View>
        </View>

        {/* Actions */}
        <View style={styles.actionSection}>
          <TouchableOpacity testID="support-btn" style={styles.actionBtn}>
            <HelpCircle size={16} color="#8B8FA3" strokeWidth={2} />
            <Text style={styles.actionText}>Support</Text>
          </TouchableOpacity>
          <TouchableOpacity testID="logout-btn" style={[styles.actionBtn, styles.logoutBtn]}>
            <LogOut size={16} color="#EF4444" strokeWidth={2} />
            <Text style={[styles.actionText, styles.logoutText]}>Sign Out</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0C0D14' },
  loadingWrap: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  scrollContent: { paddingBottom: 100 },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 16 },
  headerTitle: { fontSize: 26, fontWeight: '700', color: '#FFFFFF', letterSpacing: -0.5 },
  avatarSection: { alignItems: 'center', paddingVertical: 16 },
  avatarWrap: { position: 'relative', marginBottom: 14 },
  avatar: { width: 76, height: 76, borderRadius: 38, backgroundColor: '#151722', borderWidth: 2, borderColor: '#1E2030', alignItems: 'center', justifyContent: 'center' },
  availDot: { position: 'absolute', bottom: 2, right: 2, width: 18, height: 18, borderRadius: 9, borderWidth: 3, borderColor: '#0C0D14' },
  dotOnline: { backgroundColor: '#22C55E' },
  dotOffline: { backgroundColor: '#EF4444' },
  name: { fontSize: 20, fontWeight: '700', color: '#FFFFFF', marginBottom: 4 },
  role: { fontSize: 13, fontWeight: '500', color: '#8B8FA3' },
  availCard: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', marginHorizontal: 16, padding: 16, marginBottom: 16 },
  availInfo: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  availLabel: { fontSize: 12, fontWeight: '500', color: '#565B7E', marginBottom: 2 },
  availStatus: { fontSize: 14, fontWeight: '700' },
  statsRow: { flexDirection: 'row', paddingHorizontal: 16, gap: 8, marginBottom: 16 },
  statCard: { flex: 1, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 16, alignItems: 'center', gap: 6 },
  statValue: { fontSize: 18, fontWeight: '800', color: '#FFFFFF' },
  statLabel: { fontSize: 11, fontWeight: '500', color: '#565B7E' },
  infoSection: { marginHorizontal: 16, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', marginBottom: 16 },
  infoItem: { flexDirection: 'row', alignItems: 'center', padding: 16, borderBottomWidth: 1, borderBottomColor: '#1E2030', gap: 12 },
  infoText: { flex: 1 },
  infoLabel: { fontSize: 11, fontWeight: '500', color: '#565B7E', marginBottom: 2 },
  infoValue: { fontSize: 14, fontWeight: '500', color: '#FFFFFF' },
  actionSection: { marginHorizontal: 16, gap: 8 },
  actionBtn: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#151722', borderRadius: 14, borderWidth: 1, borderColor: '#1E2030', padding: 16 },
  actionText: { fontSize: 14, fontWeight: '500', color: '#8B8FA3' },
  logoutBtn: { borderColor: '#EF444430' },
  logoutText: { color: '#EF4444' },
});
