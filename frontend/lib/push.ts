// Push notifications for Bricks GO (expo-notifications).
// Works today through Expo's push service once credentials are attached:
//   iOS     -> upload an APNs key (.p8) with `eas credentials` (needs Apple Team ID)
//   Android -> upload the FCM v1 service-account JSON with `eas credentials`
// Until then getExpoPushTokenAsync fails quietly and the app keeps working.
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import Constants from 'expo-constants';
import { actorHeaders } from './api';

const API = process.env.EXPO_PUBLIC_BACKEND_URL ?? 'https://dispatch-api.brxs.ca';

let handlerSet = false;
export function configureNotificationHandler() {
  if (handlerSet || Platform.OS === 'web') return;
  handlerSet = true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldPlaySound: true,
      shouldSetBadge: true,
      shouldShowBanner: true,
      shouldShowList: true,
    }),
  });
}

export async function ensureAndroidChannel(color: string) {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync('default', {
    name: 'Job updates',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: color,
  });
}

/**
 * Ask permission (only after sign-in), fetch the Expo push token, and store it
 * against the signed-in vendor via POST /api/push-tokens (auth required).
 */
export async function registerForPush(): Promise<string | null> {
  if (Platform.OS === 'web' || !Device.isDevice) return null;
  try {
    const existing = await Notifications.getPermissionsAsync();
    let status = existing.status;
    if (status !== 'granted') {
      status = (await Notifications.requestPermissionsAsync()).status;
    }
    if (status !== 'granted') return null;

    const projectId =
      (Constants?.expoConfig?.extra as any)?.eas?.projectId ?? (Constants as any)?.easConfig?.projectId;
    if (!projectId) return null;
    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId });

    const headers = await actorHeaders({ 'Content-Type': 'application/json' });
    if (!headers.Authorization) return token;
    await fetch(`${API}/api/push-tokens`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ token, platform: Platform.OS }),
    }).catch(() => {});
    return token;
  } catch (e) {
    // No APNs/FCM credentials yet, simulator, or user declined.
    return null;
  }
}

/** Route a tapped notification to the job it is about, when it names one. */
export function routeForNotification(data: any): string | null {
  const jobId = data?.job_id || data?.jobId;
  if (jobId) return `/job/${jobId}`;
  const route = data?.route;
  if (typeof route === 'string' && route.startsWith('/')) return route;
  if (data?.type === 'onboarding_approved' || data?.kind === 'onboarding_approved') return '/settings';
  if (data?.type || data?.kind) return '/notifications';
  return null;
}
