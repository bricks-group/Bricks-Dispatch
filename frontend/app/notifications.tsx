import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, StyleSheet, FlatList, TouchableOpacity, ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Bell, Check, ChevronLeft, UploadCloud, Briefcase, AlertTriangle } from 'lucide-react-native';
import { TOKENS } from '../constants/Theme';
import { supabase } from '../lib/supabase';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

async function authHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

type Notification = {
  id: string;
  title: string;
  message: string;
  type: string;
  read: boolean;
  created_at: string;
};

const TYPE_ICON: Record<string, React.ComponentType<any>> = {
  upload: UploadCloud,
  job: Briefcase,
  alert: AlertTriangle,
};

function timeAgo(iso: string) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return 'JUST NOW';
  if (diff < 3600) return `${Math.floor(diff / 60)}M AGO`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}H AGO`;
  return `${Math.floor(diff / 86400)}D AGO`;
}

export default function NotificationsScreen() {
  const router = useRouter();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchNotifications = useCallback(async () => {
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API}/api/notifications`, { headers });
      if (res.ok) setNotifications(await res.json());
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { fetchNotifications(); }, [fetchNotifications]);

  const markRead = async (id: string) => {
    const headers = await authHeaders();
    await fetch(`${API}/api/notifications/${id}/read`, {
      method: 'PUT',
      headers,
    }).catch(() => {});
    setNotifications(prev =>
      prev.map(n => n.id === id ? { ...n, read: true } : n)
    );
  };

  const markAllRead = async () => {
    const unread = notifications.filter(n => !n.read);
    const headers = await authHeaders();
    await Promise.all(unread.map(n =>
      fetch(`${API}/api/notifications/${n.id}/read`, {
        method: 'PUT',
        headers,
      }).catch(() => {})
    ));
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
  };

  const renderItem = ({ item }: { item: Notification }) => {
    const Icon = TYPE_ICON[item.type] || Bell;
    return (
      <TouchableOpacity
        style={[styles.row, item.read && styles.rowRead]}
        onPress={() => markRead(item.id)}
        activeOpacity={0.7}
      >
        <View style={styles.iconWrap}>
          <Icon size={16} color={item.read ? TOKENS.moonMute : TOKENS.brick} strokeWidth={1.5} />
        </View>
        <View style={styles.rowBody}>
          <Text style={[styles.rowTitle, item.read && styles.rowTitleRead]}>{item.title.toLowerCase()}</Text>
          <Text style={styles.rowMsg} numberOfLines={2}>{item.message}</Text>
          <Text style={styles.rowTime}>{timeAgo(item.created_at)}</Text>
        </View>
        {!item.read && <View style={styles.dot} />}
      </TouchableOpacity>
    );
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <ChevronLeft size={20} color={TOKENS.moon} strokeWidth={1.5} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>notifications</Text>
        {unreadCount > 0 ? (
          <TouchableOpacity onPress={markAllRead} style={styles.markAllBtn}>
            <Text style={styles.markAllText}>ALL READ</Text>
          </TouchableOpacity>
        ) : (
          <View style={styles.markAllBtn} />
        )}
      </View>

      {loading ? (
        <View style={styles.loadingWrap}>
          <ActivityIndicator size="small" color={TOKENS.brick} />
        </View>
      ) : notifications.length === 0 ? (
        <View style={styles.emptyWrap}>
          <Bell size={32} color={TOKENS.nightHairS} strokeWidth={1} />
          <Text style={styles.emptyText}>no notifications yet</Text>
        </View>
      ) : (
        <FlatList
          data={notifications}
          keyExtractor={item => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: TOKENS.night },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 36, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair,
  },
  backBtn: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center' },
  headerTitle: { fontFamily: 'System', fontSize: 28, color: TOKENS.moon, letterSpacing: -0.6 },
  markAllBtn: { minWidth: 70, alignItems: 'flex-end' },
  markAllText: { fontFamily: 'IoskeleyMono-Medium', fontSize: 10, color: TOKENS.brick, letterSpacing: 1.2 },
  loadingWrap: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  emptyWrap: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyText: { fontFamily: 'System', fontSize: 13, color: TOKENS.moonMute },
  list: { paddingBottom: 32 },
  row: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 16,
    paddingHorizontal: 36, paddingVertical: 20,
    borderBottomWidth: 1, borderBottomColor: TOKENS.nightHair,
  },
  rowRead: { opacity: 0.6 },
  iconWrap: {
    width: 32, height: 32, borderRadius: 2, backgroundColor: TOKENS.nightSoft,
    alignItems: 'center', justifyContent: 'center', marginTop: 2,
  },
  rowBody: { flex: 1 },
  rowTitle: { fontFamily: 'System', fontSize: 15, color: TOKENS.moon, marginBottom: 4 },
  rowTitleRead: { color: TOKENS.moonSoft },
  rowMsg: { fontFamily: 'System', fontSize: 13, color: TOKENS.moonSoft, lineHeight: 18 },
  rowTime: { fontFamily: 'IoskeleyMono-Regular', fontSize: 9, color: TOKENS.moonFaint, marginTop: 8, letterSpacing: 1.2 },
  dot: { width: 6, height: 6, borderRadius: 3, backgroundColor: TOKENS.brick, marginTop: 8 },
});
