import { useState, useEffect, useCallback } from 'react';
import { View, Text, StyleSheet, FlatList, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Bell, UploadCloud, Briefcase, AlertTriangle } from 'lucide-react-native';
import { color, type as T, space, radius, layout, hairline } from '../theme';
import { NavBar, TextLink, EmptyState, LoadingState } from '../components/ui';
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
  data?: { route?: string; type?: string; job_id?: string };
};

const TYPE_ICON: Record<string, React.ComponentType<any>> = {
  upload: UploadCloud,
  job: Briefcase,
  alert: AlertTriangle,
};

function timeAgo(iso: string) {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
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

  const openNotification = (item: Notification) => {
    markRead(item.id);
    if (item.type === 'onboarding_approved' || item.data?.type === 'onboarding_approved') {
      router.push('/settings');
      return;
    }
    const route = item.data?.route;
    if (typeof route === 'string' && route.startsWith('/')) router.push(route as any);
  };

  const renderItem = ({ item }: { item: Notification }) => {
    const Icon = TYPE_ICON[item.type] || Bell;
    return (
      <Pressable
        style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
        onPress={() => openNotification(item)}
        accessibilityRole="button"
        accessibilityLabel={`${item.read ? '' : 'Unread. '}${item.title}. ${item.message || ''}`}
      >
        <View style={styles.iconWrap}>
          <Icon size={layout.icon} color={item.read ? color.textFaint : color.accent} strokeWidth={layout.iconStroke} />
        </View>
        <View style={styles.rowBody}>
          <Text style={[T.bodyMedium, { color: item.read ? color.textMuted : color.text }]}>{item.title}</Text>
          {item.message ? <Text style={[T.bodySm, { color: item.read ? color.textFaint : color.textBody }]}>{item.message}</Text> : null}
          <Text style={[T.dataSm, styles.rowTime]}>{timeAgo(item.created_at)}</Text>
        </View>
        {!item.read && <View style={styles.dot} />}
      </Pressable>
    );
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <NavBar
        title="Notifications"
        onBack={() => router.back()}
        right={unreadCount > 0 ? <TextLink tone="accent" title="Mark all read" onPress={markAllRead} /> : undefined}
      />
      {loading ? (
        <LoadingState />
      ) : notifications.length === 0 ? (
        <EmptyState icon={Bell} title="You’re all caught up" body="Job offers, upload receipts and payout alerts land here." />
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
  container: { flex: 1, backgroundColor: color.bg },
  list: { paddingBottom: space[48] },
  row: {
    flexDirection: 'row', alignItems: 'flex-start', gap: space[14],
    paddingHorizontal: layout.gutter, paddingVertical: space[16],
    borderBottomWidth: hairline, borderBottomColor: color.hair,
  },
  rowPressed: { backgroundColor: color.surface },
  iconWrap: {
    width: 36, height: 36, borderRadius: radius.sm, backgroundColor: color.surface,
    alignItems: 'center', justifyContent: 'center',
  },
  rowBody: { flex: 1, gap: 2 },
  rowTime: { color: color.textFaint, marginTop: space[6] },
  dot: { width: 8, height: 8, borderRadius: radius.pill, backgroundColor: color.accent, marginTop: space[8] },
});
