import { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Switch,
  RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import {
  User,
  Mail,
  Phone,
  Star,
  Award,
  Calendar,
  HelpCircle,
  LogOut,
  Wifi,
  WifiOff,
} from 'lucide-react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

type Profile = {
  id: string;
  name: string;
  email: string;
  role: string;
  phone: string;
  availability: boolean;
  total_jobs_completed: number;
  rating: number;
  member_since: string;
};

export default function ProfileScreen() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);

  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/profile`);
      const data = await res.json();
      setProfile(data);
    } catch (e) {
      console.error('Failed to fetch profile:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    fetchProfile();
  }, [fetchProfile]);

  const toggleAvailability = async () => {
    if (!profile || toggling) return;
    setToggling(true);
    const newVal = !profile.availability;
    setProfile({ ...profile, availability: newVal });
    try {
      await fetch(`${API}/api/profile`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ availability: newVal }),
      });
    } catch (e) {
      setProfile({ ...profile, availability: !newVal });
      console.error('Toggle failed:', e);
    } finally {
      setToggling(false);
    }
  };

  if (loading || !profile) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="large" color="#FACC15" />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#FACC15" colors={['#FACC15']} />
        }
      >
        <View style={styles.header}>
          <Text style={styles.headerTitle}>PROFILE</Text>
        </View>

        {/* Avatar & Name */}
        <View style={styles.avatarSection}>
          <View style={styles.avatarWrap}>
            <View style={styles.avatar}>
              <User size={32} color="#A1A1AA" strokeWidth={1.5} />
            </View>
            <View style={[styles.availDot, profile.availability ? styles.dotOnline : styles.dotOffline]} />
          </View>
          <Text style={styles.name}>{profile.name}</Text>
          <Text style={styles.role}>{profile.role}</Text>
        </View>

        {/* Availability Toggle */}
        <View testID="availability-section" style={styles.availCard}>
          <View style={styles.availInfo}>
            {profile.availability ? (
              <Wifi size={20} color="#22C55E" strokeWidth={2.5} />
            ) : (
              <WifiOff size={20} color="#EF4444" strokeWidth={2.5} />
            )}
            <View>
              <Text style={styles.availLabel}>AVAILABILITY</Text>
              <Text style={[styles.availStatus, { color: profile.availability ? '#22C55E' : '#EF4444' }]}>
                {profile.availability ? 'ONLINE' : 'OFFLINE'}
              </Text>
            </View>
          </View>
          <Switch
            testID="availability-toggle"
            value={profile.availability}
            onValueChange={toggleAvailability}
            trackColor={{ false: '#27272A', true: '#FACC1580' }}
            thumbColor={profile.availability ? '#FACC15' : '#71717A'}
          />
        </View>

        {/* Stats */}
        <View style={styles.statsRow}>
          <View style={styles.statCard}>
            <Award size={20} color="#FACC15" strokeWidth={2} />
            <Text style={styles.statValue}>{profile.total_jobs_completed}</Text>
            <Text style={styles.statLabel}>JOBS DONE</Text>
          </View>
          <View style={styles.statCard}>
            <Star size={20} color="#FACC15" strokeWidth={2} />
            <Text style={styles.statValue}>{profile.rating}</Text>
            <Text style={styles.statLabel}>RATING</Text>
          </View>
          <View style={styles.statCard}>
            <Calendar size={20} color="#FACC15" strokeWidth={2} />
            <Text style={styles.statValue}>
              {new Date(profile.member_since).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}
            </Text>
            <Text style={styles.statLabel}>SINCE</Text>
          </View>
        </View>

        {/* Info Items */}
        <View style={styles.infoSection}>
          <View style={styles.infoItem}>
            <Mail size={18} color="#71717A" strokeWidth={2} />
            <View style={styles.infoText}>
              <Text style={styles.infoLabel}>EMAIL</Text>
              <Text style={styles.infoValue}>{profile.email}</Text>
            </View>
          </View>
          <View style={styles.infoItem}>
            <Phone size={18} color="#71717A" strokeWidth={2} />
            <View style={styles.infoText}>
              <Text style={styles.infoLabel}>PHONE</Text>
              <Text style={styles.infoValue}>{profile.phone}</Text>
            </View>
          </View>
        </View>

        {/* Actions */}
        <View style={styles.actionSection}>
          <TouchableOpacity testID="support-btn" style={styles.actionBtn}>
            <HelpCircle size={18} color="#A1A1AA" strokeWidth={2} />
            <Text style={styles.actionText}>SUPPORT</Text>
          </TouchableOpacity>
          <TouchableOpacity testID="logout-btn" style={[styles.actionBtn, styles.logoutBtn]}>
            <LogOut size={18} color="#EF4444" strokeWidth={2} />
            <Text style={[styles.actionText, styles.logoutText]}>SIGN OUT</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#050505',
  },
  loadingWrap: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
  },
  scrollContent: {
    paddingBottom: 100,
  },
  header: {
    paddingHorizontal: 20,
    paddingVertical: 16,
  },
  headerTitle: {
    fontSize: 28,
    fontWeight: '900',
    color: '#FFFFFF',
    letterSpacing: 2,
  },
  avatarSection: {
    alignItems: 'center',
    paddingVertical: 20,
  },
  avatarWrap: {
    position: 'relative',
    marginBottom: 14,
  },
  avatar: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: '#1A1A1A',
    borderWidth: 2,
    borderColor: '#27272A',
    alignItems: 'center',
    justifyContent: 'center',
  },
  availDot: {
    position: 'absolute',
    bottom: 2,
    right: 2,
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 3,
    borderColor: '#050505',
  },
  dotOnline: {
    backgroundColor: '#22C55E',
  },
  dotOffline: {
    backgroundColor: '#EF4444',
  },
  name: {
    fontSize: 22,
    fontWeight: '800',
    color: '#FFFFFF',
    marginBottom: 4,
  },
  role: {
    fontSize: 13,
    fontWeight: '600',
    color: '#A1A1AA',
    letterSpacing: 1,
  },
  availCard: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    marginHorizontal: 16,
    padding: 16,
    marginBottom: 16,
  },
  availInfo: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  availLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#71717A',
    letterSpacing: 1.5,
    marginBottom: 2,
  },
  availStatus: {
    fontSize: 14,
    fontWeight: '800',
    letterSpacing: 1,
  },
  statsRow: {
    flexDirection: 'row',
    paddingHorizontal: 16,
    gap: 8,
    marginBottom: 16,
  },
  statCard: {
    flex: 1,
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    padding: 16,
    alignItems: 'center',
    gap: 6,
  },
  statValue: {
    fontSize: 20,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  statLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#71717A',
    letterSpacing: 1,
  },
  infoSection: {
    marginHorizontal: 16,
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    marginBottom: 16,
  },
  infoItem: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: '#1A1A1A',
    gap: 12,
  },
  infoText: {
    flex: 1,
  },
  infoLabel: {
    fontSize: 10,
    fontWeight: '700',
    color: '#71717A',
    letterSpacing: 1.5,
    marginBottom: 2,
  },
  infoValue: {
    fontSize: 14,
    fontWeight: '500',
    color: '#FFFFFF',
  },
  actionSection: {
    marginHorizontal: 16,
    gap: 8,
  },
  actionBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    backgroundColor: '#121212',
    borderWidth: 1,
    borderColor: '#27272A',
    padding: 16,
  },
  actionText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#A1A1AA',
    letterSpacing: 1,
  },
  logoutBtn: {
    borderColor: '#EF444440',
  },
  logoutText: {
    color: '#EF4444',
  },
});
