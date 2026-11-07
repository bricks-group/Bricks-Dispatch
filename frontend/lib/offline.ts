// Offline support for Bricks GO.
// - useIsOnline(): live connectivity from @react-native-community/netinfo.
// - cachedJson(): network-first GET that falls back to the last good response
//   saved on this device, so a photographer in a basement or a rural driveway
//   still sees today's jobs, addresses, and access notes.
import { useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';

const PREFIX = 'go_cache_v1:';

export function useIsOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const unsub = NetInfo.addEventListener((s) => {
      // isInternetReachable is null while unknown; only treat explicit false as offline.
      setOnline(!(s.isConnected === false || s.isInternetReachable === false));
    });
    return () => unsub();
  }, []);
  return online;
}

export type Cached<T> = { data: T; fromCache: boolean; savedAt: string | null };

export async function cachedJson<T>(
  key: string,
  url: string,
  init?: RequestInit,
): Promise<Cached<T>> {
  try {
    const res = await fetch(url, init);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as T;
    const savedAt = new Date().toISOString();
    AsyncStorage.setItem(PREFIX + key, JSON.stringify({ data, savedAt })).catch(() => {});
    return { data, fromCache: false, savedAt };
  } catch (err) {
    const raw = await AsyncStorage.getItem(PREFIX + key).catch(() => null);
    if (raw) {
      try {
        const parsed = JSON.parse(raw);
        return { data: parsed.data as T, fromCache: true, savedAt: parsed.savedAt ?? null };
      } catch {}
    }
    throw err;
  }
}

/** Remove every cached response (used on sign-out and account deletion). */
export async function clearOfflineCache(): Promise<void> {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const mine = keys.filter((k) => k.startsWith(PREFIX));
    if (mine.length) await AsyncStorage.multiRemove(mine);
  } catch {}
}
