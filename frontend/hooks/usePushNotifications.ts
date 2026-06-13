import { useState, useEffect, useRef } from 'react';
import { Platform } from 'react-native';

const API = process.env.EXPO_PUBLIC_BACKEND_URL;

export function usePushNotifications() {
  const [expoPushToken, setExpoPushToken] = useState<string | undefined>(undefined);
  const [notification, setNotification] = useState<any>(undefined);

  useEffect(() => {
    if (Platform.OS === 'web') return; // Push not supported on web

    let notifListener: any;
    let responseListener: any;

    (async () => {
      try {
        const Notifications = require('expo-notifications');
        const Device = require('expo-device');
        const Constants = require('expo-constants');

        Notifications.setNotificationHandler({
          handleNotification: async () => ({
            shouldPlaySound: true,
            shouldSetBadge: true,
            shouldShowBanner: true,
            shouldShowList: true,
          }),
        });

        if (Platform.OS === 'android') {
          await Notifications.setNotificationChannelAsync('default', {
            name: 'Bricks GO',
            importance: Notifications.AndroidImportance.MAX,
            vibrationPattern: [0, 250, 250, 250],
            lightColor: '#3B82F6',
          });
        }

        if (!Device.isDevice) return;

        const { status: existingStatus } = await Notifications.getPermissionsAsync();
        let finalStatus = existingStatus;
        if (existingStatus !== 'granted') {
          const { status } = await Notifications.requestPermissionsAsync();
          finalStatus = status;
        }
        if (finalStatus !== 'granted') return;

        const projectId =
          Constants?.expoConfig?.extra?.eas?.projectId ?? Constants?.easConfig?.projectId;
        if (!projectId) return;

        const tokenData = await Notifications.getExpoPushTokenAsync({ projectId });
        const token = tokenData.data;
        setExpoPushToken(token);

        // Register with backend
        try {
          await fetch(`${API}/api/push-tokens`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, platform: Platform.OS }),
          });
        } catch (e) {}

        notifListener = Notifications.addNotificationReceivedListener((n: any) => setNotification(n));
        responseListener = Notifications.addNotificationResponseReceivedListener((r: any) => {
          console.log('Notification tapped:', r.notification.request.content.data);
        });
      } catch (e) {
        console.log('Push notifications setup skipped:', e);
      }
    })();

    return () => {
      notifListener?.remove();
      responseListener?.remove();
    };
  }, []);

  return { expoPushToken, notification };
}
